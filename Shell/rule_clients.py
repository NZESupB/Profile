"""从同一组规则源生成各客户端的规则文件和有序路由。"""

from __future__ import annotations

import copy
from pathlib import Path
from urllib.parse import quote

import yaml

from rule_formats import (
    CAPABILITIES,
    client_lines,
    client_route,
    github_proxy_url,
    write_lines,
)


def source_slug(name: str) -> str:
    return name.replace("/", "-").replace("@", "-at-").replace("!", "not-")


def write_source(directory: Path, slug: str, client: str, lines: list[str]):
    if client == "clash":
        path = directory / "sources" / f"{slug}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"payload": lines}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path
    path = directory / "sources" / f"{slug}.list"
    write_lines(path, lines)
    return path


def emit_clients(root, config, sources, spec, split, target):
    by_provider = {
        source["provider"]: source for source in sources.values() if source["provider"]
    }
    reports = {}
    for client in CAPABILITIES:
        directory = root / "clients" / client
        directory.mkdir(parents=True, exist_ok=True)
        aliases = spec.get("client_policies", {}).get(client, {})
        dropped, source_paths = [], {}
        for name, source in sources.items():
            if source.get("dns_only"):
                dropped.append(
                    {"source": name, "reason": "DNS 专用模式不导出为代理分流规则"}
                )
                continue
            source_routes = [
                line
                for line in config.get("rules", [])
                if split(line)[0] == "RULE-SET" and split(line)[1] == source["provider"]
            ]
            policy = (
                aliases.get(target(source_routes[0]), target(source_routes[0]))
                if source_routes
                else "DIRECT"
            )
            lines, excluded = client_lines(
                source["export_lines"],
                client,
                policy if client == "quantumultx" else None,
            )
            dropped.extend({"source": name, **item} for item in excluded)
            path = write_source(directory, source_slug(name), client, lines)
            if source["provider"]:
                source_paths[source["provider"]] = path

        routes, clash_providers, reference_count = [], {}, 0
        for index, route in enumerate(config.get("rules") or [], 1):
            parts = split(route)
            policy = aliases.get(target(route), target(route))
            if parts[0] == "MATCH":
                routes.append(
                    f"{'final' if client == 'quantumultx' else 'MATCH' if client == 'clash' else 'FINAL'},{policy}"
                )
                continue
            if parts[0] == "RULE-SET":
                _, provider, _, *params = parts
                source = by_provider[provider]
                source_path = source_paths[provider]
                if client != "clash":
                    raw = list(source["export_lines"])
                    if "no-resolve" in params:
                        raw = [
                            line + ",no-resolve"
                            if line.startswith(("IP-CIDR,", "IP-CIDR6,"))
                            and not line.endswith(",no-resolve")
                            else line
                            for line in raw
                        ]
                    if client == "quantumultx":
                        converted, excluded = client_lines(raw, client, policy)
                        dropped.extend({"route": index, **item} for item in excluded)
                        routes.extend(converted)
                    else:
                        for line in raw:
                            converted, excluded = client_route(line, client, policy)
                            dropped.extend(
                                {"route": index, **item} for item in excluded
                            )
                            if converted:
                                routes.append(converted)
                elif client == "clash":
                    provider_name = "source_" + source_slug(provider)
                    clash_providers[provider_name] = {
                        "type": "file",
                        "behavior": "classical",
                        "format": "yaml",
                        "path": "./" + source_path.relative_to(directory).as_posix(),
                    }
                    suffix = "," + ",".join(params) if params else ""
                    routes.append(f"RULE-SET,{provider_name},{policy}{suffix}")
                reference_count += 1
                continue
            if parts[0] in {"AND", "OR", "NOT"}:
                dropped.append(
                    {
                        "route": index,
                        "rule": route,
                        "reason": "客户端逻辑规则无已验证的等价转换",
                    }
                )
                continue
            raw = ",".join([*parts[:2], *[p for p in parts[2:] if p == "no-resolve"]])
            converted, excluded = client_route(raw, client, policy)
            dropped.extend({"route": index, **item} for item in excluded)
            if converted:
                routes.append(converted)

        if client == "clash":
            (directory / "rules.yaml").write_text(
                yaml.safe_dump(
                    {"rule-providers": clash_providers, "rules": routes},
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
        elif client == "quantumultx":
            # 规则随构建包导入本地段，保留顺序且不引用未发布的 clients URL。
            write_lines(
                directory / "routes.list",
                [line for line in routes if not line.startswith("final,")],
            )
            write_lines(
                directory / "rules.conf",
                ["[filter_local]", *routes],
            )
        else:
            write_lines(directory / "rules.conf", ["[Rule]", *routes])
        reports[client] = {
            "excluded": dropped,
            "excluded_count": len(dropped),
            "source_files": sum(
                not source.get("dns_only", False) for source in sources.values()
            ),
            "route_references": reference_count,
            "policy_names": sorted(
                {aliases.get(target(r), target(r)) for r in config.get("rules", [])}
            ),
        }
    return reports


def remote_config(config, artifacts, base):
    result = copy.deepcopy(config)
    base = github_proxy_url(base).rstrip("/")
    for item in artifacts:
        provider = result["rule-providers"][item["name"]]
        path = item["path"]
        deployment = item.get("deployment")
        if deployment:
            provider.clear()
            provider.update(copy.deepcopy(deployment))
            if deployment["type"] == "inline":
                continue
            provider["url"] = github_proxy_url(deployment["url"])
            suffix = ".mrs" if deployment["format"] == "mrs" else Path(path).suffix
            provider.update(
                path="./" + str(Path(path).with_suffix(suffix)),
                interval=86400,
                proxy="DIRECT",
            )
            continue
        provider.update(
            {
                "type": "http",
                "url": base + "/" + quote(path),
                "path": "./" + path,
                "interval": 86400,
                "proxy": "DIRECT",
            }
        )
    return result
