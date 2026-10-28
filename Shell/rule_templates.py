"""从来源清单重建逻辑路由，并生成唯一的 MRS 部署模板。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from textwrap import indent

import yaml

from rule_clients import remote_config
from rule_formats import RuleError

COMMON_KEYS = (
    "proxy_providers_common",
    "rule_providers_common",
    "group_common",
    "rule_common",
    "subscription",
    "ProviderTest",
    "GroupSelect",
    "GroupLoadBalance",
    "RuleSet_classical",
    "RuleSet_geosite",
    "RuleSet_ipcidr",
)


def logical_config(config: dict, spec: dict) -> dict:
    routing = spec.get("routing")
    if not isinstance(routing, dict) or not isinstance(routing.get("rules"), list):
        raise RuleError("构建清单缺少 routing.rules 逻辑路由")
    if not isinstance(routing.get("dns"), dict):
        raise RuleError("构建清单缺少 routing.dns 引用")
    result = template_payload(config)
    providers = {}
    for item in [*spec["local"].values(), *spec["upstreams"].values()]:
        name = item["provider"]
        if name is None:
            continue
        if name in providers:
            raise RuleError("构建清单的来源 provider 名称重复")
        # 仅用于逻辑引用校验；实际文件和格式由对应来源的构建结果替换。
        providers[name] = {
            "type": "file",
            "path": f"./build-input/{name}.txt",
            "behavior": "classical",
            "format": "text",
        }
    result["rule-providers"] = providers
    result["rules"] = copy.deepcopy(routing["rules"])
    result.setdefault("dns", {}).update(copy.deepcopy(routing["dns"]))
    return result


def template_payload(config: dict) -> dict:
    result = copy.deepcopy(config)
    for key in COMMON_KEYS:
        result.pop(key, None)
    return result


def mrs_config(config: dict, artifacts: list[dict], base: str) -> dict:
    """生成 MRS 地址预览；不编译二进制，也不修改文本运行包。"""
    result = template_payload(config)
    projected = copy.deepcopy(artifacts)
    for item in projected:
        if item["behavior"] in {"domain", "ipcidr"}:
            item["path"] = Path(item["path"]).with_suffix(".mrs").as_posix()
            item["format"] = "mrs"
        provider = result["rule-providers"][item["name"]]
        provider.update({key: item[key] for key in ("path", "behavior", "format")})
    return remote_config(result, projected, base)


def render_template(config: dict) -> str:
    config = template_payload(config)
    providers = config.pop("rule-providers")
    routes = config.pop("rules")
    output = [
        "# 唯一的完整 Mihomo MRS 部署模板；来源与逻辑路由由 Rule/build.yaml 维护。\n",
        "# 修改来源后使用 rules.py build --text-only --write-template 更新引用；正式 MRS 仅由 Actions 编译。\n",
    ]
    for key, value in (("mode", "rule"), ("log-level", "info"), ("secret", "''")):
        if key not in config:
            output.append(f"# {key}: {value}\n")
    output.append(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    output.append(
        "\nrule_common:\n"
        "  domain: &mrs_domain {type: http, behavior: domain, format: mrs, interval: 86400, proxy: DIRECT}\n"
        "  ip: &mrs_ip {type: http, behavior: ipcidr, format: mrs, interval: 86400, proxy: DIRECT}\n"
        "  extra: &classical {type: http, behavior: classical, format: text, interval: 86400, proxy: DIRECT}\n"
        "\nrule-providers:\n"
    )
    anchors = {
        ("domain", "mrs"): "mrs_domain",
        ("ipcidr", "mrs"): "mrs_ip",
        ("classical", "text"): "classical",
    }
    for name, provider in providers.items():
        kind = provider["behavior"], provider["format"]
        if kind not in anchors:
            raise RuleError("部署模板存在非 MRS 域名/IP provider 或无效 classical 格式")
        output.append(
            f"  {json.dumps(name, ensure_ascii=False)}:\n    <<: *{anchors[kind]}\n"
        )
        details = {
            key: value
            for key, value in provider.items()
            if key not in {"type", "behavior", "format", "interval", "proxy"}
        }
        output.append(
            indent(yaml.safe_dump(details, allow_unicode=True, sort_keys=False), "    ")
        )
    output.append(
        "\n" + yaml.safe_dump({"rules": routes}, allow_unicode=True, sort_keys=False)
    )
    return "".join(output)
