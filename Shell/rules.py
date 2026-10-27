#!/usr/bin/env python3
"""审计共享规则，构建可回读、可离线加载的 Mihomo 运行包。"""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BUILTINS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}
DOMAIN_KINDS = {"DOMAIN", "DOMAIN-SUFFIX"}
IP_KINDS = {"IP-CIDR", "IP-CIDR6"}
CLASSICAL_KINDS = {
    "DOMAIN-KEYWORD",
    "DOMAIN-REGEX",
    "DOMAIN-WILDCARD",
    "PROCESS-NAME",
    "PROCESS-PATH",
    "PROCESS-NAME-REGEX",
    "PROCESS-PATH-REGEX",
    "PROCESS-NAME-WILDCARD",
    "PROCESS-PATH-WILDCARD",
    "IP-ASN",
    "GEOIP",
    "GEOSITE",
    "SRC-IP-CIDR",
    "SRC-PORT",
    "DST-PORT",
    "NETWORK",
    "IN-TYPE",
    "IN-PORT",
    "UID",
    "DSCP",
    "SRC-GEOIP",
    "SRC-IP-ASN",
    "IP-SUFFIX",
    "AND",
    "OR",
    "NOT",
}


class RuleError(ValueError):
    """输入不符合构建契约。"""


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


def load_yaml(path: Path):
    data = yaml.load(path.read_text(encoding="utf-8-sig"), Loader=UniqueLoader)
    if not isinstance(data, dict):
        raise RuleError(f"{path.name}: YAML 顶层必须是对象")
    return data


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def fields(text: str) -> list[str]:
    """仅拆分顶层逗号，保留逻辑规则的括号内容。"""
    result, start, depth = [], 0, 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(text[start:index].strip())
            start = index + 1
        if depth < 0:
            raise RuleError("规则括号不匹配")
    if depth:
        raise RuleError("规则括号不匹配")
    return result + [text[start:].strip()]


def domain(value: str) -> str:
    value = value.lower().rstrip(".")
    if not value or any(c.isspace() for c in value) or any(c in value for c in "/,*+:"):
        raise RuleError("DOMAIN/DOMAIN-SUFFIX 中出现无效域名")
    if any(not label or len(label) > 63 for label in value.split(".")):
        raise RuleError("域名标签为空或过长")
    return value.encode("idna").decode("ascii")


@dataclass(frozen=True)
class Rule:
    kind: str
    value: str
    params: tuple[str, ...]
    source: str
    line: int

    @property
    def text(self) -> str:
        return ",".join((self.kind, self.value, *self.params))

    @property
    def shard(self) -> str:
        if self.kind in DOMAIN_KINDS:
            return "domain"
        if self.kind in IP_KINDS:
            return "ipcidr-no-resolve" if "no-resolve" in self.params else "ipcidr"
        return "classical"

    def location(self) -> dict:
        return {"source": self.source, "line": self.line, "rule": self.text}


def parse_rule(text: str, source: str, line: int) -> Rule:
    parts = fields(text)
    if len(parts) < 2 or not parts[1]:
        raise RuleError(f"{source}:{line}: 缺少规则参数")
    kind, value, *params = parts
    if kind not in DOMAIN_KINDS | IP_KINDS | CLASSICAL_KINDS:
        raise RuleError(f"{source}:{line}: 不支持的规则类型 {kind}")
    if kind in DOMAIN_KINDS:
        if params:
            raise RuleError(f"{source}:{line}: 域名规则不应包含策略或参数")
        value = domain(value)
    elif kind in IP_KINDS:
        if any(p != "no-resolve" for p in params) or len(params) > 1:
            raise RuleError(f"{source}:{line}: 不支持的 CIDR 参数")
        network = ipaddress.ip_network(value, strict=False)
        if kind == "IP-CIDR6" and network.version != 6:
            raise RuleError(f"{source}:{line}: IP-CIDR6 必须是 IPv6")
        value = str(network)
    elif params and kind not in {"GEOIP", "IP-ASN", "SRC-GEOIP", "SRC-IP-ASN"}:
        raise RuleError(f"{source}:{line}: classical 规则包含非预期参数")
    return Rule(kind, value, tuple(params), source, line)


def parse_rules(raw: bytes, source: str, fmt="text", exclusions=None):
    text = raw.decode("utf-8-sig")
    exclusions = exclusions or {}
    if fmt == "yaml":
        data = yaml.load(text, Loader=UniqueLoader)
        if not isinstance(data, dict) or not isinstance(data.get("payload"), list):
            raise RuleError(f"{source}: 缺少 YAML payload 列表")
        # YAML 节点保留真实原始行号，方便追溯上游覆盖依据。
        node = yaml.compose(text)
        payload = next(v for k, v in node.value if k.value == "payload")
        entries = [
            (n.start_mark.line + 1, v) for n, v in zip(payload.value, data["payload"])
        ]
    elif fmt == "text":
        entries = enumerate(text.splitlines(), 1)
    else:
        raise RuleError(f"{source}: 仅支持 text/yaml 输入")
    rules, excluded = [], []
    used_exclusions = set()
    for number, raw_line in entries:
        if not isinstance(raw_line, str):
            raise RuleError(f"{source}:{number}: 规则必须是字符串")
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if line in exclusions:
            excluded.append(
                {
                    "source": source,
                    "line": number,
                    "rule": line,
                    "reason": exclusions[line],
                }
            )
            used_exclusions.add(line)
            continue
        rules.append(parse_rule(line, source, number))
    if set(exclusions) - used_exclusions:
        raise RuleError(f"{source}: 排除清单存在已失效的条目，请检查源文件")
    return rules, excluded


def covers(parent: Rule, child: Rule) -> bool:
    if parent.params != child.params:
        return False
    if parent.kind in DOMAIN_KINDS and child.kind in DOMAIN_KINDS:
        if parent.kind == "DOMAIN":
            return child.kind == "DOMAIN" and parent.value == child.value
        return child.value == parent.value or child.value.endswith("." + parent.value)
    if parent.kind in IP_KINDS and child.kind in IP_KINDS:
        p, c = ipaddress.ip_network(parent.value), ipaddress.ip_network(child.value)
        return p.version == c.version and c.subnet_of(p)
    return (parent.kind, parent.value) == (child.kind, child.value)


def self_dedupe(rules: list[Rule]):
    kept, removed = [], []
    for index, rule in enumerate(rules):
        covering = next(
            (
                other
                for j, other in enumerate(rules)
                if j != index
                and covers(other, rule)
                and (not covers(rule, other) or j < index)
            ),
            None,
        )
        if covering:
            removed.append(
                {
                    **rule.location(),
                    "reason": "同文件等价覆盖",
                    "covered_by": covering.location(),
                }
            )
        else:
            kept.append(rule)
    return kept, removed


def rule_target(text: str) -> str:
    parts = fields(text)
    if parts[-1] == "no-resolve":
        parts.pop()
    if len(parts) < 2:
        raise RuleError("路由规则缺少策略")
    return parts[-1]


def validate_config(config: dict, home: Path | None = None):
    groups = config.get("proxy-groups") or []
    group_names = [g["name"] for g in groups]
    proxy_names = [p["name"] for p in config.get("proxies") or []]
    all_names = group_names + proxy_names
    if len(set(all_names)) != len(all_names):
        raise RuleError("代理节点或策略组名称重复")
    targets = set(all_names) | BUILTINS
    providers = config.get("rule-providers") or {}
    subscriptions = config.get("proxy-providers") or {}
    paths = set()
    for item in [*providers.values(), *subscriptions.values()]:
        path = item.get("path")
        if path:
            if path in paths:
                raise RuleError("provider 缓存路径重复")
            paths.add(path)
        if item.get("type") == "http" and not item.get("url"):
            raise RuleError("HTTP provider 缺少 URL")
    for name, item in providers.items():
        if item.get("format") == "mrs" and item.get("behavior") not in {
            "domain",
            "ipcidr",
        }:
            raise RuleError(f"{name}: MRS behavior 无效")
        if home is not None and item.get("type") == "file":
            contained_file(home, item["path"])
    edges = {}
    for group in groups:
        refs = group.get("proxies") or []
        if any(ref not in targets for ref in refs):
            raise RuleError(f"策略组 {group['name']} 含未定义引用")
        if any(ref not in subscriptions for ref in group.get("use") or []):
            raise RuleError(f"策略组 {group['name']} 含未定义订阅引用")
        edges[group["name"]] = [ref for ref in refs if ref in group_names]

    def visit(name, stack):
        if name in stack:
            raise RuleError("策略组引用形成循环")
        for child in edges.get(name, []):
            visit(child, stack | {name})

    for name in group_names:
        visit(name, set())
    for rule in config.get("rules") or []:
        if rule_target(rule) not in targets:
            raise RuleError("路由规则引用未定义策略")
        for name in re.findall(r"(?:^|\()RULE-SET,([^,()]+)", rule):
            if name not in providers:
                raise RuleError(f"规则引用未定义 provider: {name}")
    dns = config.get("dns") or {}
    dns_refs = list((dns.get("nameserver-policy") or {}).keys()) + list(
        dns.get("fake-ip-filter") or []
    )
    for ref in dns_refs:
        if ref.startswith("rule-set:") and any(
            n not in providers for n in ref[9:].split(",")
        ):
            raise RuleError("DNS 引用未定义 rule provider")
    for key in ("nameserver", "direct-nameserver", "proxy-server-nameserver"):
        for server in dns.get(key) or []:
            suffix = server.split("#", 1)[1] if "#" in server else ""
            if suffix and suffix not in targets:
                raise RuleError("DNS 上游引用未定义策略")


def contained_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise RuleError("构建产物路径越界或文件缺失")
    return path


def snapshot(
    entry: dict, cache: Path, offline: bool, base: Path | None = None
) -> bytes:
    digest = entry["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise RuleError("锁文件 SHA-256 无效")
    path = cache / digest
    if entry.get("snapshot"):
        if base is None:
            raise RuleError("锁文件快照缺少根目录")
        data = contained_file(base, entry["snapshot"]).read_bytes()
    elif path.exists():
        data = path.read_bytes()
    elif offline:
        raise RuleError(f"离线缓存缺失: {digest}")
    else:
        request = urllib.request.Request(
            entry["url"], headers={"User-Agent": "Profile-rule-build/1"}
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = response.read(64 * 1024 * 1024 + 1)
        except (OSError, urllib.error.URLError) as error:
            raise RuleError(f"上游下载失败 ({type(error).__name__})") from None
        if len(data) > 64 * 1024 * 1024:
            raise RuleError("上游文件超过 64 MiB")
    if sha256(data) != digest:
        raise RuleError(
            f"上游快照校验失败: {digest}；需要显式更新锁文件，不能继续使用变动内容"
        )
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as temp:
            temp.write(data)
            temporary = Path(temp.name)
        temporary.replace(path)
    return data


def dns_compatible(config: dict, local: str, public: str) -> bool:
    """每个引用本地列表的 DNS 条件也必须已包含覆盖它的上游。"""
    dns = config.get("dns") or {}
    references = list((dns.get("nameserver-policy") or {}).keys())
    references += list(dns.get("fake-ip-filter") or [])
    for pattern in references:
        if pattern.startswith("rule-set:"):
            names = pattern[9:].split(",")
            if local in names and public not in names:
                return False
    return True


def prepare(args):
    config = load_yaml(args.config)
    spec = load_yaml(args.manifest)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if spec.get("version") != 1 or lock.get("version") != 1:
        raise RuleError("不支持的构建清单或锁文件版本")
    validate_config(config)
    actual = {p.name for p in args.rules_dir.glob("*.list")}
    if actual != set(spec["local"]):
        raise RuleError(
            f"本地规则与清单不一致: {sorted(actual.symmetric_difference(spec['local']))}"
        )
    sources, report, inputs = (
        {},
        {"removed": [], "retained_overlap": [], "excluded": [], "files": {}},
        {},
    )
    for filename, item in spec["local"].items():
        path = args.rules_dir / filename
        raw = path.read_bytes()
        rules, excluded = parse_rules(
            raw, "Rule/" + filename, exclusions=item.get("exclusions")
        )
        rules, removed = self_dedupe(rules)
        report["excluded"].extend(excluded)
        report["removed"].extend(removed)
        sources["local/" + path.stem] = {
            "provider": item["provider"],
            "rules": rules,
            "local": True,
        }
        inputs["Rule/" + filename] = sha256(raw)
        report["files"][filename] = {
            "parsed": len(rules) + len(removed),
            "excluded": len(excluded),
            "self_removed": len(removed),
        }
    upstream_records = {}
    for name, item in spec["upstreams"].items():
        if item.get("behavior") != "classical":
            raise RuleError("上游输入清单必须保留 classical 的完整语义")
        entry = lock["rules"][name]
        raw = snapshot(entry, args.cache, args.offline, args.lock.parent)
        rules, _ = parse_rules(raw, "upstream/" + name, fmt=item["format"])
        upstream_records[name] = (raw, item)
        sources["upstream/" + name] = {
            "provider": item["provider"],
            "rules": rules,
            "local": False,
        }
    declared = {source["provider"] for source in sources.values() if source["provider"]}
    if declared != set(config.get("rule-providers") or {}):
        raise RuleError("模板 rule-providers 与构建清单不一致")
    routes = config.get("rules") or []
    references = {}
    for index, route in enumerate(routes):
        parts = fields(route)
        if parts[0] == "RULE-SET":
            references.setdefault(parts[1], []).append(
                (index, rule_target(route), tuple(parts[3:]))
            )
    # 先保留完整来源用于覆盖证明；公共规则不参与本地互删，避免级联空洞。
    upstreams = [s for s in sources.values() if not s["local"]]
    for source in (s for s in sources.values() if s["local"] and s["provider"]):
        kept = []
        positions = references.get(source["provider"], [])
        for rule in source["rules"]:
            candidates = []
            for public in upstreams:
                match = next((r for r in public["rules"] if covers(r, rule)), None)
                if match:
                    candidates.append((public, match))
            proof = None
            if positions:
                for public, match in candidates:
                    earlier = references.get(public["provider"], [])
                    if dns_compatible(
                        config, source["provider"], public["provider"]
                    ) and all(
                        any(
                            j < i and policy == target and opts == params
                            for j, policy, opts in earlier
                        )
                        for i, target, params in positions
                    ):
                        proof = match
                        break
            if proof:
                report["removed"].append(
                    {
                        **rule.location(),
                        "reason": "更早的同策略锁定上游已完整覆盖",
                        "covered_by": proof.location(),
                    }
                )
            else:
                kept.append(rule)
                if candidates:
                    report["retained_overlap"].append(
                        {
                            **rule.location(),
                            "reason": "策略、顺序或参数无法证明等价",
                            "covered_by": candidates[0][1].location(),
                        }
                    )
        source["rules"] = kept
    # 不同本地列表可能代表不同策略；只报告，不自动互删。
    owners = {}
    for source in (s for s in sources.values() if s["local"]):
        for rule in source["rules"]:
            key = (rule.kind, rule.value, rule.params)
            if key in owners:
                report["retained_overlap"].append(
                    {
                        **rule.location(),
                        "reason": "跨本地列表策略覆盖",
                        "covered_by": owners[key].location(),
                    }
                )
            owners[key] = rule
    dns_raw = (args.rules_dir / spec["dns_filter"]).read_bytes()
    inputs["Rule/" + spec["dns_filter"]] = sha256(dns_raw)
    patterns = [
        s.strip()
        for s in dns_raw.decode("utf-8-sig").splitlines()
        if s.strip() and not s.lstrip().startswith("#")
    ]
    # DNS 通配模式原样保留，不经过路由 domain 标准化或 MRS 转换。
    config.setdefault("dns", {})["fake-ip-filter"] = list(
        dict.fromkeys(config.get("dns", {}).get("fake-ip-filter", []) + patterns)
    )
    report["summary"] = {
        "local_rules": sum(v["parsed"] for v in report["files"].values()),
        "removed": len(report["removed"]),
        "retained_overlap": len(report["retained_overlap"]),
        "excluded": len(report["excluded"]),
        "dns_patterns": len(patterns),
    }
    return config, sources, upstream_records, lock, report, inputs


def run_core(core: str, args: list[str], timeout=30) -> str:
    try:
        result = subprocess.run(
            [core, *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuleError(f"Mihomo 调用失败 ({type(error).__name__})") from None
    text = result.stdout + result.stderr
    if result.returncode or re.search(
        r"skip invalid|parse classical rule.*error|level=(?:warning|error|fatal)",
        text,
        re.I,
    ):
        # 内核日志可能含有用户本机 URL 或节点名称，不向外打印原始日志。
        raise RuleError(
            f"Mihomo 检查失败，退出码 {result.returncode}；请在隔离目录直接运行对应命令检查"
        )
    return text


def domain_set(lines):
    exact, suffix = set(), set()
    for line in lines:
        line = line.strip()
        if line.startswith("+."):
            exact.add(line[2:])
            suffix.add(line[2:])
        elif line.startswith("."):
            suffix.add(line[1:])
        else:
            exact.add(line)

    def parents(name):
        labels = name.split(".")
        return (".".join(labels[i:]) for i in range(1, len(labels)))

    suffix = {s for s in suffix if not any(p in suffix for p in parents(s))}
    exact = {s for s in exact if not any(p in suffix for p in parents(s))}
    return sorted(exact), sorted(suffix)


def ip_set(lines):
    networks = [
        ipaddress.ip_network(s.strip(), strict=False) for s in lines if s.strip()
    ]
    return [
        str(n)
        for version in (4, 6)
        for n in ipaddress.collapse_addresses(
            n for n in networks if n.version == version
        )
    ]


def verify_mrs(core: str, root: Path, item: dict):
    source = contained_file(root, item["source_path"])
    mrs = contained_file(root, item["path"])
    with tempfile.TemporaryDirectory(prefix="mrs-readback-") as directory:
        dump = Path(directory) / "rules.txt"
        run_core(
            core, ["convert-ruleset", item["behavior"], "mrs", str(mrs), str(dump)]
        )
        before, after = source.read_text().splitlines(), dump.read_text().splitlines()
    normalize = domain_set if item["behavior"] == "domain" else ip_set
    if normalize(before) != normalize(after):
        raise RuleError(f"{item['path']}: MRS 回读后的匹配集合不一致")


def emit_local(root: Path, name: str, rules: list[Rule], core: str, text_only=False):
    fragments = []
    for index, (shard, values) in enumerate(groupby(rules, key=lambda r: r.shard), 1):
        values = list(values)
        behavior = shard.split("-", 1)[0]
        base = Path("rules") / name / f"{index:03d}.{shard}"
        text_path = base.with_name(base.name + ".txt")
        target = root / text_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if behavior == "domain":
            payload = [
                ("+." if r.kind == "DOMAIN-SUFFIX" else "") + r.value for r in values
            ]
        elif behavior == "ipcidr":
            payload = [r.value for r in values]
        else:
            payload = [r.text for r in values]
        target.write_text("\n".join(payload) + "\n", encoding="utf-8")
        item = {
            "path": text_path.as_posix(),
            "behavior": behavior,
            "format": "text",
            "count": len(values),
            "no_resolve": shard == "ipcidr-no-resolve",
        }
        if behavior != "classical" and not text_only:
            mrs = base.with_name(base.name + ".mrs")
            run_core(
                core,
                ["convert-ruleset", behavior, "text", str(target), str(root / mrs)],
            )
            item.update(
                {
                    "path": mrs.as_posix(),
                    "source_path": text_path.as_posix(),
                    "format": "mrs",
                }
            )
        fragments.append(item)
    return fragments


def rewrite_dns(config, replacements):
    def names(value):
        expanded = []
        for name in value[9:].split(","):
            expanded.extend(
                item["name"]
                for item in replacements[name]
                if item["behavior"] != "ipcidr"
            )
        return "rule-set:" + ",".join(expanded) if expanded else None

    dns = config.get("dns") or {}
    for key in ("nameserver-policy",):
        result = {}
        for pattern, value in (dns.get(key) or {}).items():
            new = names(pattern) if pattern.startswith("rule-set:") else pattern
            if new:
                if new in result:
                    raise RuleError("DNS 规则展开后名称冲突")
                result[new] = value
        dns[key] = result
    dns["fake-ip-filter"] = [
        new
        for value in dns.get("fake-ip-filter") or []
        if (new := names(value) if value.startswith("rule-set:") else value)
    ]


def build_into(args, root: Path, prepared, core: str):
    config, sources, upstream_records, lock, report, inputs = prepared
    config = copy.deepcopy(config)
    for key in ("proxy_providers_common", "rule_providers_common", "group_common"):
        config.pop(key, None)
    replacements, artifacts, providers = {}, [], {}
    for name, source in sources.items():
        provider = source["provider"]
        if source["local"]:
            fragments = emit_local(root, name, source["rules"], core, args.text_only)
            # 保留清理过的 classical 文本源，便于其他文本方式调用及审查。
            source_path = root / "rules/source" / (name.split("/")[-1] + ".list")
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(
                "\n".join(r.text for r in source["rules"]) + "\n", encoding="utf-8"
            )
        else:
            key = name.split("/")[-1]
            raw, spec = upstream_records[key]
            ext = "yaml" if spec["format"] == "yaml" else "list"
            path = Path("rules/upstream") / f"{key}.{ext}"
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(raw)
            fragments = [
                {
                    "path": path.as_posix(),
                    "behavior": spec["behavior"],
                    "format": spec["format"],
                    "count": len(source["rules"]),
                    "no_resolve": False,
                }
            ]
        named = []
        for index, fragment in enumerate(fragments, 1):
            fragment["name"] = (
                f"{provider}__{index:03d}"
                if source["local"] and provider
                else (provider or f"extra_{name.split('/')[-1]}__{index:03d}")
            )
            if fragment["name"] in providers:
                raise RuleError("生成的 provider 名称冲突")
            providers[fragment["name"]] = {
                "type": "file",
                **{k: fragment[k] for k in ("path", "behavior", "format")},
            }
            artifacts.append(fragment)
            named.append(fragment)
        if provider:
            replacements[provider] = named
    routes = []
    for route in config.get("rules") or []:
        parts = fields(route)
        if parts[0] == "RULE-SET":
            _, provider, target, *params = parts
            for item in replacements[provider]:
                options = list(params)
                if item["no_resolve"] and "no-resolve" not in options:
                    options.append("no-resolve")
                routes.append(",".join(["RULE-SET", item["name"], target, *options]))
        else:
            for provider in re.findall(r"(?:^|\()RULE-SET,([^,()]+)", route):
                if (
                    len(replacements[provider]) != 1
                    or replacements[provider][0]["name"] != provider
                ):
                    raise RuleError(
                        "嵌套逻辑规则引用了需要拆分的本地 provider，请先改成顶层 RULE-SET"
                    )
            routes.append(route)
    config["rules"] = routes
    config["rule-providers"] = providers
    rewrite_dns(config, replacements)
    for filename, entry in lock.get("assets", {}).items():
        if Path(filename).name != filename:
            raise RuleError("Geo 数据文件名必须位于输出根目录")
        (root / filename).write_bytes(
            snapshot(entry, args.cache, args.offline, args.lock.parent)
        )
    config["geo-auto-update"] = False
    validate_config(config, root)
    (root / "config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    save_json(root / "audit.json", report)
    save_json(root / "upstreams.lock.json", lock)
    (root / ".rules-build").write_text("Profile Mihomo build v1\n", encoding="utf-8")
    version = run_core(core, ["-v"]).splitlines()[0]
    manifest = {
        "version": 1,
        "mode": "text" if args.text_only else "mrs",
        "mihomo": version,
        "inputs": inputs,
        "config_input_sha256": sha256(args.config.read_bytes()),
        "build_spec_sha256": sha256(args.manifest.read_bytes()),
        "artifacts": artifacts,
        "summary": report["summary"],
        "files": {
            p.relative_to(root).as_posix(): sha256(p.read_bytes())
            for p in sorted(root.rglob("*"))
            if p.is_file()
        },
    }
    save_json(root / "manifest.json", manifest)


def http_json(port: int, path: str):
    # 忽略用户系统代理，控制器只访问本机环回地址。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
        return json.load(response)


def check_core(core: str, output: Path):
    with tempfile.TemporaryDirectory(prefix="mihomo-check-") as directory:
        home = Path(directory)
        # 使用隔离副本和无凭证订阅夹具，避免访问用户订阅或绑定原端口。
        for path in output.iterdir():
            if path.is_dir():
                shutil.copytree(path, home / path.name)
            elif path.suffix in {".dat", ".mmdb"}:
                shutil.copyfile(path, home / path.name)
        config = load_yaml(output / "config.yaml")
        fixture = {
            "proxies": [
                {"name": name, "type": "http", "server": "127.0.0.1", "port": 9}
                for name in ("香港测试", "日本测试", "美国测试", "新加坡测试")
            ]
        }
        (home / "fixture.yaml").write_text(
            yaml.safe_dump(fixture, allow_unicode=True), encoding="utf-8"
        )
        for name in config.get("proxy-providers") or {}:
            config["proxy-providers"][name] = {"type": "file", "path": "./fixture.yaml"}
        # 每个订阅使用独立路径，避免多个 provider 的缓存路径碰撞。
        for index, item in enumerate((config.get("proxy-providers") or {}).values()):
            path = f"fixture-{index}.yaml"
            shutil.copyfile(home / "fixture.yaml", home / path)
            item["path"] = "./" + path
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config.update(
            {
                "mixed-port": 0,
                "port": 0,
                "socks-port": 0,
                "redir-port": 0,
                "tproxy-port": 0,
                "external-controller": f"127.0.0.1:{port}",
                "secret": "",
                "external-ui": "",
                "mode": "rule",
            }
        )
        config.pop("external-ui-url", None)
        config["tun"] = {"enable": False}
        config.setdefault("dns", {})["listen"] = "127.0.0.1:0"
        cfg_path = home / "config.yaml"
        cfg_path.write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        run_core(core, ["-t", "-d", str(home), "-f", str(cfg_path)], timeout=60)
        with tempfile.TemporaryFile(mode="w+") as logs:
            process = subprocess.Popen(
                [core, "-d", str(home), "-f", str(cfg_path)], stdout=logs, stderr=logs
            )
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuleError("隔离 Mihomo 内核提前退出")
                    try:
                        data = http_json(port, "/providers/rules").get("providers", {})
                        expected = set(config["rule-providers"])
                        if expected.issubset(data) and all(
                            data[n].get("ruleCount", 0) > 0 for n in expected
                        ):
                            break
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.2)
                else:
                    raise RuleError("规则 provider 加载超时或有效规则数量为零")
                # 所有 provider 已加载后检查解析警告，不能仅依据 -t 的退出码。
                logs.flush()
                logs.seek(0)
                text = logs.read()
                if re.search(
                    r"skip invalid|parse classical rule.*error|level=(?:warning|error|fatal)",
                    text,
                    re.I,
                ):
                    raise RuleError("隔离内核运行日志包含警告或错误")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def check_output(output: Path, core: str):
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for relative, digest in manifest["files"].items():
        if sha256(contained_file(output, relative).read_bytes()) != digest:
            raise RuleError(f"产物校验失败: {relative}")
    validate_config(load_yaml(output / "config.yaml"), output)
    for item in manifest["artifacts"]:
        if item["format"] == "mrs":
            verify_mrs(core, output, item)
    check_core(core, output)
    return manifest


def build(args, core: str):
    if args.output.is_symlink():
        raise RuleError("输出目录不能是符号链接")
    output = args.output.resolve()
    if (
        output == ROOT
        or output in args.config.resolve().parents
        or output in args.rules_dir.resolve().parents
        or output in args.manifest.resolve().parents
        or output in args.lock.resolve().parents
        or output == args.rules_dir.resolve()
    ):
        raise RuleError("输出目录不能覆盖工程或规则输入目录")
    if output.exists() and (
        not output.is_dir()
        or (
            any(output.iterdir())
            and (
                not (output / ".rules-build").is_file()
                or (output / ".rules-build").read_text() != "Profile Mihomo build v1\n"
            )
        )
    ):
        raise RuleError("输出目录不是本工具创建的运行包；请指定空目录")
    prepared = prepare(args)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".rules-stage-", dir=output.parent
    ) as directory:
        stage = Path(directory) / "package"
        stage.mkdir()
        build_into(args, stage, prepared, core)
        manifest = check_output(stage, core)
        backup = Path(directory) / "previous"
        if output.exists():
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup.exists():
                backup.rename(output)
            raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "build", "check"))
    parser.add_argument(
        "--config", type=Path, default=ROOT / "Template/metaProvider.yml"
    )
    parser.add_argument("--rules-dir", type=Path, default=ROOT / "Rule")
    parser.add_argument("--manifest", type=Path, default=ROOT / "Rule/build.yaml")
    parser.add_argument("--lock", type=Path, default=ROOT / "Rule/upstreams.lock.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache/rules")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/mihomo")
    parser.add_argument("--mihomo", default=os.environ.get("MIHOMO", "mihomo"))
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="仅生成文本规则供本机测试；正式 MRS 构建在 Actions 执行",
    )
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--report", type=Path, help="audit 的 JSON 报告输出路径")
    args = parser.parse_args(argv)
    try:
        if args.command == "audit":
            *_, report, _inputs = prepare(args)
            if args.report:
                save_json(args.report, report)
            print(json.dumps(report["summary"], ensure_ascii=False))
        else:
            core = shutil.which(args.mihomo)
            if not core:
                raise RuleError("找不到 Mihomo，请传入 --mihomo 或设置 MIHOMO")
            manifest = (
                build(args, core)
                if args.command == "build"
                else check_output(args.output.resolve(), core)
            )
            print(
                json.dumps(
                    {
                        "command": args.command,
                        "mode": manifest["mode"],
                        "summary": manifest["summary"],
                        "artifacts": len(manifest["artifacts"]),
                        "verified": True,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    except (
        RuleError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        yaml.YAMLError,
    ) as error:
        # 不打印底层异常内容，避免 URL、凭证或整个 YAML 片段进入共享日志。
        message = (
            str(error)
            if isinstance(error, RuleError)
            else f"{type(error).__name__}；请检查输入文件及权限"
        )
        print("失败: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
