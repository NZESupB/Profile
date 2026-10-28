"""公共规则源读取、MRS 回读和客户端格式映射。"""

from __future__ import annotations

import ipaddress
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import yaml


class RuleError(ValueError):
    """规则输入或转换不符合契约。"""


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key, _ in node.value:
            if key.tag == "tag:yaml.org,2002:merge":
                continue
            name = self.construct_object(key, deep=deep)
            if name in seen:
                raise RuleError(f"YAML 第 {key.start_mark.line + 1} 行存在重复键")
            seen.add(name)
        self.flatten_mapping(node)
        return super().construct_mapping(node, deep=deep)


def public_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or parts.query
    ):
        raise RuleError("规则 URL 必须为无认证、无查询参数的 HTTP(S) 地址")
    return value


def fetch_url(url: str) -> bytes:
    request = urllib.request.Request(
        public_url(url), headers={"User-Agent": "Profile-rule-build/2"}
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            public_url(response.url)
            raw = response.read(64 * 1024 * 1024 + 1)
    except (OSError, ValueError) as error:
        raise RuleError(f"规则下载失败 ({type(error).__name__})") from None
    if len(raw) > 64 * 1024 * 1024:
        raise RuleError("规则源超过 64 MiB")
    return raw


def read_mrs(raw: bytes, core: str, behavior="auto") -> tuple[str, bytes]:
    choices = ("domain", "ipcidr") if behavior == "auto" else (behavior,)
    if any(value not in {"domain", "ipcidr"} for value in choices):
        raise RuleError("MRS 仅支持 domain/ipcidr")
    with tempfile.TemporaryDirectory(prefix="mrs-url-read-") as directory:
        source, target = Path(directory) / "input.mrs", Path(directory) / "output.txt"
        source.write_bytes(raw)
        for value in choices:
            target.unlink(missing_ok=True)
            try:
                result = subprocess.run(
                    [core, "convert-ruleset", value, "mrs", str(source), str(target)],
                    capture_output=True,
                    timeout=45,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuleError(f"MRS 回读失败 ({type(error).__name__})") from None
            if result.returncode == 0 and target.is_file() and target.stat().st_size:
                return value, target.read_bytes()
    raise RuleError("无效 MRS、行为不匹配或内核不支持该 MRS 版本")


def classical_lines(raw: bytes, behavior: str, fmt="text") -> list[str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RuleError("文本规则不是 UTF-8") from None
    if fmt == "yaml":
        data = yaml.load(text, Loader=UniqueLoader)
        if not isinstance(data, dict) or not isinstance(data.get("payload"), list):
            raise RuleError("YAML 规则缺少 payload 列表")
        entries = data["payload"]
    else:
        entries = text.splitlines()
    lines = []
    for entry in entries:
        if not isinstance(entry, str):
            raise RuleError("规则必须为字符串")
        entry = re.split(r"\s+#", entry.strip(), maxsplit=1)[0]
        if not entry or entry.startswith(("#", ";", "//")):
            continue
        if behavior == "domain":
            if entry.startswith("+."):
                line = "DOMAIN-SUFFIX," + entry[2:]
            elif entry.startswith("."):
                # 仅匹配子域，不能转换为同时匹配根域的 DOMAIN-SUFFIX。
                line = "DOMAIN-WILDCARD,*" + entry
            elif any(mark in entry for mark in ("*", "+")):
                line = "DOMAIN-WILDCARD," + entry
            else:
                line = "DOMAIN," + entry
        elif behavior == "ipcidr":
            network = ipaddress.ip_network(entry, strict=False)
            line = ("IP-CIDR6," if network.version == 6 else "IP-CIDR,") + str(network)
        elif behavior == "classical":
            line = entry
        else:
            raise RuleError("规则行为必须为 domain/ipcidr/classical")
        lines.append(line)
    if not lines:
        raise RuleError("规则源为空")
    return lines


# 客户端没有等价语法时生成明确报告，不自动改写匹配范围。
COMMON = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "IP-CIDR", "IP-CIDR6"}
CAPABILITIES = {
    "clash": COMMON | {"PROCESS-NAME"},
    "surge": COMMON | {"PROCESS-NAME", "USER-AGENT", "DOMAIN-WILDCARD"},
    "surfboard": COMMON | {"PROCESS-NAME", "USER-AGENT"},
    "shadowrocket": COMMON | {"USER-AGENT", "DOMAIN-WILDCARD"},
    "quantumultx": COMMON | {"USER-AGENT"},
}
QX_TYPES = {
    "DOMAIN": "host",
    "DOMAIN-SUFFIX": "host-suffix",
    "DOMAIN-KEYWORD": "host-keyword",
    "IP-CIDR": "ip-cidr",
    "IP-CIDR6": "ip6-cidr",
    "USER-AGENT": "user-agent",
}


def split_fields(line: str) -> list[str]:
    parts, start, stack = [], 0, []
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in pairs.items()}
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif stack and stack[-1] == "[":
            if char == "]":
                stack.pop()
        elif char in pairs:
            stack.append(char)
        elif char in closing:
            if not stack or stack.pop() != closing[char]:
                raise RuleError("规则括号不匹配")
        elif char == "," and not stack:
            parts.append(line[start:index].strip())
            start = index + 1
    if stack:
        raise RuleError("规则括号不匹配")
    return parts + [line[start:].strip()]


def client_lines(lines, client, policy=None):
    if client not in CAPABILITIES:
        raise RuleError("未知客户端")
    result, excluded = [], []
    for line in lines:
        parts = split_fields(line)
        kind = parts[0]
        if kind not in CAPABILITIES[client] or len(parts) < 2:
            excluded.append({"rule": line, "reason": f"{client} 无已验证的等价转换"})
            continue
        params = parts[2:]
        if any(value != "no-resolve" for value in params):
            raise RuleError("客户端转换遇到未知参数")
        if client == "quantumultx":
            if "no-resolve" in params:
                excluded.append(
                    {
                        "rule": line,
                        "reason": "Quantumult X 无已验证的 no-resolve 等价参数",
                    }
                )
                continue
            if policy is None:
                raise RuleError("Quantumult X 规则需要策略")
            parts = [QX_TYPES[kind], parts[1], policy]
        elif policy:
            parts = [*parts[:2], policy, *params]
        result.append(",".join(parts))
    return result, excluded


def client_route(line: str, client: str, policy: str):
    parts = split_fields(line)
    kind, value, *params = parts
    converted, excluded = client_lines(
        [",".join([kind, value, *params])],
        client,
        policy if client == "quantumultx" else None,
    )
    if excluded or not converted:
        return None, excluded
    if client == "quantumultx":
        return converted[0], []
    fields = split_fields(converted[0])
    return ",".join([*fields[:2], policy, *fields[2:]]), []


def write_lines(path: Path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
