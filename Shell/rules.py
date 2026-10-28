#!/usr/bin/env python3
"""审计共享规则，构建可回读、可离线加载的 Mihomo 运行包。"""

from __future__ import annotations

import argparse
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
import urllib.request
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

import yaml
from rule_clients import emit_clients, remote_config
from rule_formats import (
    RuleError,
    UniqueLoader,
    classical_lines,
    fetch_url,
    split_fields,
)
from rule_sources import read_local_source
from rule_reference import integrate_reference
from rule_templates import logical_config, mrs_config, render_template, template_payload

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
    return split_fields(text)


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
    if re.search(r"(?:GEOSITE|GEOIP|SRC-GEOIP|IP-ASN|SRC-IP-ASN),", text, re.I):
        raise RuleError(f"{source}:{line}: Geodata 规则必须改为显式域名或 CIDR")
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


def parse_rules(raw: bytes, source: str, fmt="text", exclusions=None, locations=None):
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
        entry_source, entry_number = (
            locations[number - 1] if locations is not None else (source, number)
        )
        if not isinstance(raw_line, str):
            raise RuleError(f"{entry_source}:{entry_number}: 规则必须是字符串")
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if line in exclusions:
            excluded.append(
                {
                    "source": entry_source,
                    "line": entry_number,
                    "rule": line,
                    "reason": exclusions[line],
                }
            )
            used_exclusions.add(line)
            continue
        rules.append(parse_rule(line, entry_source, entry_number))
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
    reject_geodata(config)
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


def reject_geodata(config):
    forbidden = {
        "geodata-mode",
        "geodata-loader",
        "geo-auto-update",
        "geo-update-interval",
        "geox-url",
    }
    if forbidden.intersection(config):
        raise RuleError("配置仍包含 Geodata 资源设置")
    text = yaml.safe_dump(config, allow_unicode=True)
    if re.search(
        r"(?<![\w-])(?:GEOSITE|GEOIP|SRC-GEOIP|IP-ASN|SRC-IP-ASN),|(?<![\w-])(?:geosite|geoip):|(?:GeoSite|GeoIP)\.dat|\.mmdb",
        text,
        re.I,
    ):
        raise RuleError("配置仍包含 Geodata 规则或资源依赖")


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
        data = fetch_url(entry["url"])
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


class CoverageIndex:
    """索引覆盖证明，避免大型域名集与本地规则逐项相乘。"""

    def __init__(self, rules):
        self.exact, self.suffix, self.networks = {}, {}, {}
        for rule in rules:
            self.exact.setdefault((rule.kind, rule.value, rule.params), rule)
            if rule.kind == "DOMAIN-SUFFIX":
                self.suffix.setdefault((rule.value, rule.params), rule)
            elif rule.kind in IP_KINDS:
                net = ipaddress.ip_network(rule.value)
                self.networks.setdefault(
                    (net.version, net.prefixlen, int(net.network_address), rule.params),
                    rule,
                )

    def covering(self, rule):
        if match := self.exact.get((rule.kind, rule.value, rule.params)):
            return match
        if rule.kind in DOMAIN_KINDS:
            labels = rule.value.split(".")
            for offset in range(len(labels)):
                if match := self.suffix.get((".".join(labels[offset:]), rule.params)):
                    return match
        elif rule.kind in IP_KINDS:
            net = ipaddress.ip_network(rule.value)
            for prefix in range(net.prefixlen + 1):
                parent = (
                    net.supernet(new_prefix=prefix) if prefix < net.prefixlen else net
                )
                if match := self.networks.get(
                    (net.version, prefix, int(parent.network_address), rule.params)
                ):
                    return match
        return None


def prepare(args):
    config = load_yaml(args.config)
    spec = load_yaml(args.manifest)
    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    if spec.get("version") != 1 or lock.get("version") != 1:
        raise RuleError("不支持的构建清单或锁文件版本")
    reference_inputs, reference_report = {}, None
    if spec.get("reference"):
        reference_path = contained_file(args.manifest.parent, spec["reference"]["lock"])
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        reference_raw = snapshot(
            reference["config"], args.cache, args.offline, args.manifest.parent
        )
        reference_config = yaml.load(reference_raw, Loader=UniqueLoader)
        spec, lock, reference_report = integrate_reference(
            spec, lock, reference, reference_config
        )
        reference_inputs = {
            "Rule/"
            + reference_path.relative_to(args.manifest.parent).as_posix(): sha256(
                reference_path.read_bytes()
            ),
            "reference/config": sha256(reference_raw),
        }
    config = logical_config(config, spec)
    validate_config(config)
    actual = {p.name for p in args.rules_dir.glob("*.list")}
    if actual != set(spec["local"]):
        raise RuleError(
            f"本地规则与清单不一致: {sorted(actual.symmetric_difference(spec['local']))}"
        )
    companions = [
        item["classical"] for item in spec["local"].values() if item.get("classical")
    ]
    actual_companions = {
        path.relative_to(args.rules_dir).as_posix()
        for path in (args.rules_dir / "classical").rglob("*")
        if path.is_file()
    }
    if len(companions) != len(set(companions)) or set(companions) != actual_companions:
        raise RuleError("classical 配套文件与构建清单不一致或重复映射")
    sources, report, inputs = (
        {},
        {"removed": [], "retained_overlap": [], "excluded": [], "files": {}},
        {},
    )
    inputs.update(reference_inputs)
    if reference_report:
        report["reference"] = reference_report
    for filename, item in spec["local"].items():
        path = args.rules_dir / filename
        raw, locations, files = read_local_source(
            path, item, args.rules_dir, DOMAIN_KINDS | IP_KINDS
        )
        rules, excluded = parse_rules(
            raw,
            "Rule/" + filename,
            exclusions=item.get("exclusions"),
            locations=locations,
        )
        order = {location: index for index, location in enumerate(locations)}
        export_lines = [
            text
            for _, text in sorted(
                [(order[(r.source, r.line)], r.text) for r in rules]
                + [(order[(e["source"], e["line"])], e["rule"]) for e in excluded]
            )
        ]
        rules, removed = self_dedupe(rules)
        report["excluded"].extend(excluded)
        report["removed"].extend(removed)
        sources["local/" + path.stem] = {
            "provider": item["provider"],
            "rules": rules,
            "local": True,
            "export_lines": export_lines,
        }
        inputs.update({name: sha256(content) for name, content in files.items()})
        report["files"][filename] = {
            "parsed": len(rules) + len(removed),
            "excluded": len(excluded),
            "self_removed": len(removed),
        }
    upstream_records = {}
    for name, item in spec["upstreams"].items():
        entry = lock["rules"][name]
        if "path" in entry:
            prefix = lock["mrs_base"] + entry["path"]
            entry = {
                "url": prefix + ".mrs",
                "sha256": entry["sha256"],
                "text": {"url": prefix + ".list", "sha256": entry["text_sha256"]},
                **(
                    {
                        "classical": {
                            "url": prefix.replace("/geosite/", "/geosite/classical/")
                            + ".yaml",
                            "sha256": entry["classical_sha256"],
                        }
                    }
                    if entry.get("classical_sha256")
                    else {}
                ),
            }
            lock["rules"][name] = entry
        text_entry = entry.get("text", entry)
        raw = snapshot(text_entry, args.cache, args.offline, args.lock.parent)
        original_digest = sha256(raw)
        behavior = item.get("behavior", "classical")
        fmt = (
            item.get("text_format", "text")
            if item["format"] == "mrs"
            else item["format"]
        )
        if item.get("expand_rules"):
            if fmt != "text" or behavior != "classical":
                raise RuleError("显式规则展开仅支持 classical 文本来源")
            expanded = raw.decode("utf-8-sig").splitlines()
            for old, key in item["expand_rules"].items():
                if old not in expanded:
                    raise RuleError("显式规则展开条目已失效")
                cidr_raw = snapshot(
                    lock["expansions"][key], args.cache, args.offline, args.lock.parent
                )
                cidrs = classical_lines(cidr_raw, "ipcidr")
                suffix = ",no-resolve" if old.endswith(",no-resolve") else ""
                expanded = [
                    value
                    for line in expanded
                    for value in (
                        [cidr + suffix for cidr in cidrs] if line == old else [line]
                    )
                ]
                inputs["expansion/" + key] = sha256(cidr_raw)
            raw = ("\n".join(expanded) + "\n").encode()
        excluded = []
        dns_only = item.get("dns_only", False)
        if dns_only:
            if behavior != "domain" or item["format"] != "mrs":
                raise RuleError("DNS 专用来源必须是 domain MRS")
            # DNS 模式不是普通路由域名；保留原始 MRS/text，不参与路由覆盖与导出。
            lines = [
                line
                for line in raw.decode("utf-8-sig").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if not lines:
                raise RuleError("DNS 专用来源为空")
            rules = []
        elif behavior == "classical":
            entries = classical_lines(raw, behavior, fmt)
            exclusions = {
                line: "Mihomo 不支持 USER-AGENT；支持该规则的客户端继续导出"
                for line in entries
                if line.startswith("USER-AGENT,")
            }
            rules, excluded = parse_rules(
                raw, "upstream/" + name, fmt=fmt, exclusions=exclusions
            )
            report["excluded"].extend(excluded)
        else:
            lines = classical_lines(raw, behavior, fmt)
            rules, _ = parse_rules(
                ("\n".join(lines) + "\n").encode(), "upstream/" + name
            )
        native_rules = list(rules)
        supplements = []
        if not dns_only and behavior != "classical" and entry.get("classical"):
            full_raw = snapshot(
                entry["classical"], args.cache, args.offline, args.lock.parent
            )
            full, _ = parse_rules(
                full_raw, "upstream/" + name + "/classical", fmt="yaml"
            )
            native_domains = [
                ("+." if rule.kind == "DOMAIN-SUFFIX" else "") + rule.value
                for rule in native_rules
                if rule.kind in DOMAIN_KINDS
            ]
            full_domains = [
                ("+." if rule.kind == "DOMAIN-SUFFIX" else "") + rule.value
                for rule in full
                if rule.kind in DOMAIN_KINDS
            ]
            if domain_set(native_domains) != domain_set(full_domains):
                raise RuleError(f"{name}: MRS 域名覆盖与完整 classical 来源不一致")
            supplements = [r for r in full if r.kind not in DOMAIN_KINDS]
            inputs["upstream/" + name + "/classical"] = sha256(full_raw)
        rules = native_rules + supplements
        upstream_records[name] = (raw, item)
        inputs["upstream/" + name] = original_digest
        sources["upstream/" + name] = {
            "provider": item["provider"],
            "rules": rules,
            "local": False,
            "dns_only": dns_only,
            "native_rules": native_rules,
            "supplements": supplements,
            "native_count": len(lines) if dns_only else len(native_rules),
            "export_lines": (
                [
                    text
                    for _, text in sorted(
                        [(r.line, r.text) for r in rules]
                        + [(e["line"], e["rule"]) for e in excluded]
                    )
                ]
                if behavior == "classical"
                else [r.text for r in rules]
            ),
        }
    declared = {source["provider"] for source in sources.values() if source["provider"]}
    if declared != set(config.get("rule-providers") or {}):
        raise RuleError("模板 rule-providers 与构建清单不一致")
    routes = config.get("rules") or []
    route_refs = set(re.findall(r"(?:^|\()RULE-SET,([^,()]+)", "\n".join(routes), re.M))
    if any(
        source.get("dns_only") and source["provider"] in route_refs
        for source in sources.values()
    ):
        raise RuleError("DNS 专用来源不能用于路由")
    references = {}
    nested = {
        name
        for route in routes
        if fields(route)[0] != "RULE-SET"
        for name in re.findall(r"\(RULE-SET,([^,()]+)", route)
    }
    for index, route in enumerate(routes):
        parts = fields(route)
        if parts[0] == "RULE-SET":
            references.setdefault(parts[1], []).append(
                (index, rule_target(route), tuple(parts[3:]))
            )
    # 先保留完整来源用于覆盖证明；公共规则不参与本地互删，避免级联空洞。
    upstreams = [
        (source, CoverageIndex(source["rules"]))
        for source in sources.values()
        if not source["local"]
    ]
    for source in (s for s in sources.values() if s["local"] and s["provider"]):
        kept = []
        positions = (
            []
            if source["provider"] in nested
            else references.get(source["provider"], [])
        )
        for rule in source["rules"]:
            candidates = []
            for public, index in upstreams:
                match = index.covering(rule)
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


def core_log_errors(text):
    for line in text.splitlines():
        # 官方内核提示 classical DNS provider 只参与域名匹配，并非解析失败。
        if re.search(
            r'level=warning msg="[^"\n]+ provider is Classical, only matching '
            r'(?:it contain domain rule|domain rules in fake-ip-filter)"\s*$',
            line,
        ):
            continue
        if re.search(
            r"skip invalid|parse classical rule.*error|level=(?:warning|error|fatal)",
            line,
            re.I,
        ):
            return True
    return False


def run_core(core: str, args: list[str], timeout=30) -> str:
    try:
        result = subprocess.run(
            [core, *args], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuleError(f"Mihomo 调用失败 ({type(error).__name__})") from None
    text = result.stdout + result.stderr
    if result.returncode or core_log_errors(text):
        # 内核日志可能含有用户本机 URL 或节点名称，不向外打印原始日志。
        raise RuleError(
            f"Mihomo 检查失败，退出码 {result.returncode}；请在隔离目录直接运行对应命令检查"
        )
    return text


def domain_set(lines):
    exact, suffix = set(), set()
    for line in lines:
        line = line.strip().lower()
        if not line or line.startswith(("#", ";", "//")):
            continue
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
            "dns_compatible": any(rule.kind.startswith("DOMAIN") for rule in values),
        }
        if behavior != "classical" and not text_only:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                raise RuleError(
                    "正式 MRS 编译只在 GitHub Actions 执行；本机请使用 --text-only"
                )
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


def expand_nested(route, replacements):
    """把嵌套 RULE-SET 替换为有序分片的并集，NOT/AND 仍作用于整个集合。"""
    pattern = r"\(RULE-SET,([^,()]+)((?:,[^()]*)?)\)"

    def replace(match):
        provider, params = match.groups()
        fragments = replacements[provider]
        if not fragments:
            raise RuleError("嵌套规则引用了空 provider，无法保留匹配语义")
        children = []
        for item in fragments:
            options = [value for value in params.split(",") if value]
            if item["no_resolve"] and "no-resolve" not in options:
                options.append("no-resolve")
            children.append("(" + ",".join(["RULE-SET", item["name"], *options]) + ")")
        if len(children) == 1:
            return children[0]
        return "(OR,(" + ",".join(children) + "))"

    return re.sub(pattern, replace, route)


def rewrite_dns(config, replacements):
    def names(value):
        expanded = []
        for name in value[9:].split(","):
            expanded.extend(
                item["name"]
                for item in replacements[name]
                if item.get("dns_compatible", item["behavior"] == "domain")
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
    config = template_payload(config)
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
            # 含 ASN 展开的净化来源可独立公开，不再触发 Geodata 下载。
            if not source.get("dns_only"):
                source_path = root / "rules/source" / (name.replace("/", "-") + ".list")
                source_path.parent.mkdir(parents=True, exist_ok=True)
                source_path.write_text(
                    "\n".join(r.text for r in source["rules"]) + "\n", encoding="utf-8"
                )
            if spec["format"] == "mrs":
                behavior = spec["behavior"]
                text_path = Path("rules/upstream") / f"{key}.{behavior}.txt"
                (root / text_path).parent.mkdir(parents=True, exist_ok=True)
                (root / text_path).write_bytes(raw)
                path = text_path
                fmt = "text"
                if not args.text_only:
                    path = text_path.with_suffix(".mrs")
                    (root / path).write_bytes(
                        snapshot(
                            lock["rules"][key],
                            args.cache,
                            args.offline,
                            args.lock.parent,
                        )
                    )
                    fmt = "mrs"
                fragments = [
                    {
                        "path": path.as_posix(),
                        "behavior": behavior,
                        "format": fmt,
                        "count": source["native_count"],
                        "no_resolve": False,
                        "dns_compatible": behavior == "domain",
                    }
                ]
                if fmt == "mrs":
                    fragments[0]["source_path"] = text_path.as_posix()
                if source["supplements"]:
                    fragments.extend(
                        emit_local(
                            root,
                            name + "/supplement",
                            source["supplements"],
                            core,
                            args.text_only,
                        )
                    )
            else:
                fragments = emit_local(
                    root, name, source["rules"], core, args.text_only
                )
            for fragment in fragments:
                if len(fragments) > 1:
                    fragment["split"] = True
        named = []
        for index, fragment in enumerate(fragments, 1):
            fragment["name"] = (
                f"{provider}__{index:03d}"
                if provider and (source["local"] or fragment.get("split"))
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
            routes.append(expand_nested(route, replacements))
    config["rules"] = routes
    config["rule-providers"] = providers
    rewrite_dns(config, replacements)
    validate_config(config, root)
    (root / "config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    spec = load_yaml(args.manifest)
    clients = emit_clients(root, prepared[0], sources, spec, fields, rule_target)
    save_json(root / "clients/report.json", clients)
    online = remote_config(config, artifacts, spec["publish_base"])
    validate_config(online)
    template = render_template(mrs_config(config, artifacts, spec["publish_base"]))
    if args.check_template and template_payload(
        load_yaml(args.config)
    ) != template_payload(yaml.load(template, Loader=UniqueLoader)):
        raise RuleError(
            "Meta.yaml 与当前规则不一致；请 build --text-only --write-template 更新"
        )
    (root / "config.remote.yaml").write_text(
        yaml.safe_dump(online, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (root / "mihomo.yaml").write_text(
        yaml.safe_dump(online, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    if report.get("reference"):
        save_json(root / "reference.json", report["reference"])
    save_json(root / "audit.json", report)
    save_json(root / "upstreams.lock.json", lock)
    (root / ".rules-build").write_text("Profile Mihomo build v1\n", encoding="utf-8")
    version = run_core(core, ["-v"]).splitlines()[0]
    manifest = {
        "version": 1,
        "mode": "text" if args.text_only else "mrs",
        "mihomo": version,
        "inputs": inputs,
        "config_input_sha256": sha256(
            template.encode() if args.write_template else args.config.read_bytes()
        ),
        "build_spec_sha256": sha256(args.manifest.read_bytes()),
        "artifacts": artifacts,
        "summary": report["summary"],
        "clients": {
            name: {
                "excluded_count": item["excluded_count"],
                "source_files": item["source_files"],
                "route_references": item["route_references"],
            }
            for name, item in clients.items()
        },
        "files": {
            p.relative_to(root).as_posix(): sha256(p.read_bytes())
            for p in sorted(root.rglob("*"))
            if p.is_file()
        },
    }
    save_json(root / "manifest.json", manifest)
    return template


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
                if core_log_errors(text):
                    raise RuleError("隔离内核运行日志包含警告或错误")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def check_output(output: Path, core: str):
    # 新运行包不能携带旧 Geodata 资产，即使未被配置引用。
    if any(p.suffix in {".dat", ".mmdb"} for p in output.rglob("*")):
        raise RuleError("运行包仍包含 Geodata 资产")
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
    if not args.text_only and os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuleError("正式 MRS 构建只在 GitHub Actions 执行；本机请使用 --text-only")
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
        or output in ROOT.parents
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
        template = build_into(args, stage, prepared, core)
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
        if args.write_template:
            args.config.write_text(template, encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "build", "check"))
    parser.add_argument("--config", type=Path, default=ROOT / "Template/Meta.yaml")
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
    templates = parser.add_mutually_exclusive_group()
    templates.add_argument(
        "--write-template", action="store_true", help="验证构建后更新完整 MRS 部署模板"
    )
    templates.add_argument(
        "--check-template",
        action="store_true",
        help="验证模板与当前 MRS/classical 产物引用一致",
    )
    parser.add_argument("--report", type=Path, help="audit 的 JSON 报告输出路径")
    args = parser.parse_args(argv)
    try:
        if (args.write_template or args.check_template) and args.command != "build":
            raise RuleError("模板更新或校验参数仅适用于 build")
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
