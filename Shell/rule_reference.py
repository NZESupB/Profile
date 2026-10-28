"""将锁定参考的 provider、内联规则、路由与 DNS 映射到构建清单。"""

from __future__ import annotations

import copy
import hashlib
import re

from rule_formats import RuleError, split_fields

INLINE_KINDS = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "IP-CIDR",
    "IP-CIDR6",
    "DOMAIN-KEYWORD",
    "PROCESS-NAME",
}


def lock_inline_rules(config: dict, config_entry: dict):
    """从固定配置派生单条来源，序号用于保留原路由位置。"""
    locked, payloads = {}, {}
    for position, route in enumerate(config["rules"], 1):
        parts = split_fields(route)
        kind = parts[0]
        if kind not in INLINE_KINDS:
            if kind not in {"RULE-SET", "AND", "OR", "NOT", "MATCH"}:
                raise RuleError(f"参考路由出现未支持的类型: {kind}")
            continue
        params = ["no-resolve"] if parts[-1] == "no-resolve" else []
        if len(parts) != 3 + len(params) or (
            params and kind not in {"IP-CIDR", "IP-CIDR6"}
        ):
            raise RuleError(f"参考内联规则参数无效: {route}")
        key = f"aisouler-inline-{position:03d}"
        filename = f"{key}.txt"
        payload = (",".join([*parts[:2], *params]) + "\n").encode("utf-8")
        behavior = (
            "domain"
            if kind in {"DOMAIN", "DOMAIN-SUFFIX"}
            else "ipcidr"
            if kind in {"IP-CIDR", "IP-CIDR6"}
            else "classical"
        )
        locked[key] = {
            "position": position,
            "route": route,
            "provider": f"参考_内联_{position:03d}",
            "behavior": behavior,
            "entry": {
                "text": {
                    "snapshot": f"snapshots/aisouler/{filename}",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "derived_from": config_entry,
                }
            },
        }
        payloads[filename] = payload
    return locked, payloads


def integrate_reference(spec: dict, lock: dict, reference: dict, config: dict):
    spec, lock = copy.deepcopy(spec), copy.deepcopy(lock)
    settings = spec["reference"]
    if reference.get("version") != 1:
        raise RuleError("不支持的参考来源锁版本")
    if set(reference["providers"]) != set(config["rule-providers"]):
        raise RuleError("参考 provider 清单缺失或过期")

    dns_only = set(settings.get("dns_only", []))
    if dns_only - set(reference["providers"]):
        raise RuleError("DNS 专用来源声明已失效")

    mapping, provider_report = {}, {}
    for name, declared in config["rule-providers"].items():
        item = reference["providers"][name]
        if (
            declared.get("url") != item["original_url"]
            or declared.get("behavior") != item["behavior"]
            or declared.get("format") != "mrs"
        ):
            raise RuleError(f"{name}: 参考来源声明与锁文件不一致")

        reuse = item.get("reuse")
        if reuse:
            source = spec["upstreams"].get(reuse)
            entry = lock["rules"].get(reuse)
            if source is None or entry is None:
                raise RuleError(f"{name}: 复用来源不存在")
            supplement = entry.get(
                "classical_sha256", entry.get("classical", {}).get("sha256")
            )
            expected = item["entry"].get("classical", {}).get("sha256")
            if (
                source.get("format") != "mrs"
                or source.get("behavior") != item["behavior"]
                or entry.get("sha256") != item["entry"].get("sha256")
                or supplement != expected
            ):
                raise RuleError(f"{name}: 复用来源已失去同版哈希证明")
            provider, source_key = source["provider"], reuse
        else:
            source_key, provider = "aisouler-" + name, "参考_" + name
            if source_key in spec["upstreams"] or source_key in lock["rules"]:
                raise RuleError(f"{name}: 参考来源名称冲突")
            spec["upstreams"][source_key] = {
                "provider": provider,
                "behavior": item["behavior"],
                "format": "mrs",
            }
            lock["rules"][source_key] = copy.deepcopy(item["entry"])
        if name in dns_only:
            spec["upstreams"][source_key]["dns_only"] = True
        mapping[name] = provider
        provider_report[name] = {
            "provider": provider,
            "reused_source": reuse,
            "dns_only": name in dns_only,
            "source_sha256": item["entry"]["sha256"],
        }

    inline_lock = reference.get("inline", {})
    expected_inline, _ = lock_inline_rules(config, reference["config"])
    if inline_lock != expected_inline:
        raise RuleError("参考内联来源与固定配置派生结果不一致")
    inline_by_route, inline_report = {}, {}
    for key, item in inline_lock.items():
        route, provider = item.get("route"), item.get("provider")
        entry, behavior = item.get("entry"), item.get("behavior")
        if (
            not isinstance(route, str)
            or not isinstance(provider, str)
            or behavior not in {"domain", "ipcidr", "classical"}
            or not isinstance(entry, dict)
        ):
            raise RuleError(f"{key}: 内联来源锁格式无效")
        if route in inline_by_route or key in spec["upstreams"] or key in lock["rules"]:
            raise RuleError(f"{key}: 内联来源重复或名称冲突")
        if key not in spec.get("inline", {}):
            raise RuleError(f"{key}: 构建清单未声明锁定的内联来源")
        declared = spec["inline"][key]
        if declared.get("route") != route or declared.get("provider") != provider:
            raise RuleError(f"{key}: 内联来源映射与锁文件不一致")
        spec["upstreams"][key] = {
            "provider": provider,
            # 输入保留规则类型；实际分片按解析结果生成 domain/ipcidr MRS。
            "behavior": "classical",
            "format": "text",
        }
        lock["rules"][key] = copy.deepcopy(entry)
        inline_by_route[route] = (key, item)
        inline_report[key] = {
            "provider": provider,
            "route": route,
            "position": item["position"],
            "behavior": behavior,
            "source_sha256": entry["text"]["sha256"],
        }
    if set(inline_lock) != set(spec.get("inline", {})):
        raise RuleError("锁定内联规则缺失或存在未声明来源")

    def replace_rulesets(text: str) -> str:
        def replace(match):
            name = match[1]
            if name not in mapping:
                raise RuleError(f"参考路由引用未知 provider: {name}")
            return "RULE-SET," + mapping[name]

        return re.sub(r"(?<![\w-])RULE-SET,([^,()]+)", replace, text)

    routes = spec["routing"]["rules"]
    before = settings["before"]
    if routes.count(before) != 1:
        raise RuleError("参考路由插入位置缺失或不唯一")
    aliases = settings["policies"]
    additions, route_report, policies, used_inline = [], [], set(), set()

    for route in config["rules"]:
        parts = split_fields(route)
        if parts[0] in INLINE_KINDS:
            if route not in inline_by_route:
                raise RuleError(f"参考内联规则未锁定: {route}")
            key, item = inline_by_route[route]
            expected_kind = {
                "domain": {"DOMAIN", "DOMAIN-SUFFIX"},
                "ipcidr": {"IP-CIDR", "IP-CIDR6"},
                "classical": {"DOMAIN-KEYWORD", "PROCESS-NAME"},
            }[item["behavior"]]
            if parts[0] not in expected_kind:
                raise RuleError(f"{route}: 内联来源行为与规则类型不一致")
            policy = parts[-2] if parts[-1] == "no-resolve" else parts[-1]
            params = ["no-resolve"] if parts[-1] == "no-resolve" else []
            if policy not in aliases:
                raise RuleError(f"参考策略缺少映射: {policy}")
            policies.add(policy)
            mapped = ",".join(["RULE-SET", item["provider"], aliases[policy], *params])
            additions.append(mapped)
            used_inline.add(key)
            route_report.append({"source": route, "mapped": mapped, "status": "inline"})
            continue

        parts = split_fields(replace_rulesets(route))
        index = -2 if parts[-1] == "no-resolve" else -1
        policy = parts[index]
        if policy not in aliases:
            raise RuleError(f"参考策略缺少映射: {policy}")
        policies.add(policy)
        parts[index] = aliases[policy]
        mapped = ",".join(parts)
        if parts[0] == "MATCH":
            if mapped != routes[-1]:
                raise RuleError("参考 MATCH 与原配置最终策略不一致")
            status = "existing_final"
        elif mapped in routes:
            status = "existing_route"
        else:
            additions.append(mapped)
            status = "inserted"
        route_report.append({"source": route, "mapped": mapped, "status": status})

    if policies != set(aliases):
        raise RuleError("参考策略映射包含失效条目")
    if used_inline != set(inline_lock):
        raise RuleError("参考内联规则存在未引用或未映射项")
    route_refs = set(
        re.findall(r"(?<![\w-])RULE-SET,([^,()]+)", "\n".join(routes + additions))
    )
    if {mapping[name] for name in dns_only} & route_refs:
        raise RuleError("DNS 专用来源不能用于路由")

    position = routes.index(before)
    routes[position:position] = additions
    dns = spec["routing"]["dns"]
    dns_report = []

    def dns_ref(pattern: str) -> str:
        if not pattern.startswith("rule-set:"):
            raise RuleError("参考 DNS 出现未显式支持的非 rule-set 条件")
        names = pattern[9:].split(",")
        if any(name not in mapping for name in names):
            raise RuleError("参考 DNS 引用未知来源")
        return "rule-set:" + ",".join(mapping[name] for name in names)

    for pattern in config["dns"].get("fake-ip-filter") or []:
        mapped = dns_ref(pattern)
        if mapped not in dns["fake-ip-filter"]:
            dns["fake-ip-filter"].append(mapped)
        dns_report.append(
            {"field": "fake-ip-filter", "source": pattern, "mapped": mapped}
        )

    existing = dns["nameserver-policy"]
    extensions = {
        key: key[9:].split(",") for key in existing if key.startswith("rule-set:")
    }
    inserted, reused_policies = {}, set()
    for pattern, servers in config["dns"].get("nameserver-policy", {}).items():
        mapped = dns_ref(pattern)
        reuse_key = settings.get("dns_policy_reuse", {}).get(pattern)
        if reuse_key:
            if reuse_key not in extensions:
                raise RuleError("参考 DNS 复用的原策略不存在")
            extensions[reuse_key] += mapped[9:].split(",")
            reused_policies.add(pattern)
            status = "existing_resolvers"
        else:
            inserted[mapped] = copy.deepcopy(servers)
            status = "reference_resolvers"
        dns_report.append(
            {
                "field": "nameserver-policy",
                "source": pattern,
                "mapped": mapped,
                "status": status,
            }
        )
    if reused_policies != set(settings.get("dns_policy_reuse", {})):
        raise RuleError("参考 DNS 复用映射存在失效条目")

    for key, servers in existing.items():
        target = (
            "rule-set:" + ",".join(dict.fromkeys(extensions[key]))
            if key in extensions
            else key
        )
        if target in inserted:
            raise RuleError("参考 DNS 展开后策略名称冲突")
        inserted[target] = servers
    dns["nameserver-policy"] = inserted

    used = set(re.findall(r"(?<![\w-])RULE-SET,([^,()]+)", "\n".join(routes)))
    for pattern in [*dns["fake-ip-filter"], *dns["nameserver-policy"]]:
        if pattern.startswith("rule-set:"):
            used.update(pattern[9:].split(","))
    if set(mapping.values()) - used:
        raise RuleError("参考 provider 已生成但未参与路由或 DNS")

    report = {
        "config": reference["config"],
        "providers": provider_report,
        "inline": inline_report,
        "routes": route_report,
        "dns": dns_report,
        "insert_before": before,
        "policies": aliases,
    }
    return spec, lock, report
