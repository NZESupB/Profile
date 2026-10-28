#!/usr/bin/env python3
"""传入单个公开规则 URL，读取 MRS/文本并注入 Mihomo 配置；不编译 MRS。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

import yaml

from rule_formats import (
    CAPABILITIES,
    RuleError,
    UniqueLoader,
    classical_lines,
    client_lines,
    fetch_url,
    read_mrs,
    write_lines,
)
from rules import ROOT, contained_file, fields, load_yaml, parse_rules, validate_config


def convert(args):
    if not re.fullmatch(r"[\w.-]+", args.name):
        raise RuleError("provider 名称只允许文字、数字、下划线、点与连字符")
    config = load_yaml(args.config)
    if args.name in config.get("rule-providers", {}):
        raise RuleError("provider 已存在，请使用新名称")
    output = args.output.resolve()
    if (
        output == ROOT
        or output in args.config.resolve().parents
        or args.output.is_symlink()
    ):
        raise RuleError("输出目录不能覆盖工程或配置源")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise RuleError("单 URL 输出必须指定空目录，避免覆盖已有配置")
    raw = fetch_url(args.url)
    digest = hashlib.sha256(raw).hexdigest()
    if args.sha256 and digest != args.sha256:
        raise RuleError("下载规则的 SHA-256 与预期不一致")
    fmt = args.format
    if fmt == "auto":
        suffix = Path(urlsplit(args.url).path).suffix.lower()
        fmt = (
            "mrs"
            if suffix == ".mrs" or raw.startswith(b"\x28\xb5\x2f\xfd")
            else (
                "yaml"
                if suffix in {".yaml", ".yml"}
                or re.search(rb"(?m)^payload\s*:", raw.lstrip(b"\xef\xbb\xbf"))
                else "text"
            )
        )
    behavior = args.behavior
    if fmt == "mrs":
        core = shutil.which(args.mihomo)
        if not core:
            raise RuleError("读取 MRS 需要 --mihomo 指定官方内核；不会编译 MRS")
        behavior, text = read_mrs(raw, core, behavior)
    else:
        text = raw
        if behavior == "auto":
            entries = (
                yaml.load(raw, Loader=UniqueLoader).get("payload", [])
                if fmt == "yaml"
                else raw.decode("utf-8-sig").splitlines()
            )
            values = [
                line.strip()
                for line in entries
                if isinstance(line, str)
                and line.strip()
                and not line.lstrip().startswith(("#", ";", "//"))
            ]
            if not values:
                raise RuleError("规则源为空")
            if all(re.match(r"^[A-Z][A-Z0-9-]*,", line) for line in values):
                behavior = "classical"
            else:
                import ipaddress

                try:
                    for line in values:
                        ipaddress.ip_network(line, strict=False)
                    behavior = "ipcidr"
                except ValueError:
                    behavior = "domain"
    lines = classical_lines(text, behavior, "yaml" if fmt == "yaml" else "text")
    # USER-AGENT 是可移植来源中的已知差异，Mihomo 明确排除并报告。
    mihomo_lines = [line for line in lines if not line.startswith("USER-AGENT,")]
    excluded_mihomo = [
        {"rule": line, "reason": "Mihomo 不支持 USER-AGENT"}
        for line in lines
        if line.startswith("USER-AGENT,")
    ]
    parse_rules(("\n".join(mihomo_lines) + "\n").encode(), "url")
    if not mihomo_lines:
        raise RuleError("来源没有 Mihomo 可表达的规则")
    if excluded_mihomo:
        # HTTP 重新下载原始来源会重新引入不支持的规则，改为本地净化副本。
        raw = ("\n".join(mihomo_lines) + "\n").encode()
        fmt, behavior = "text", "classical"
    relative = f"rule-providers/{args.name}.{'mrs' if fmt == 'mrs' else 'txt'}"
    provider = {
        "type": "http",
        "url": args.url,
        "path": "./" + relative,
        "behavior": behavior,
        "format": "mrs" if fmt == "mrs" else "text",
        "interval": 86400,
    }
    # YAML 输入导出为 text，HTTP 远端格式仍保持 yaml。
    if fmt == "yaml":
        provider["format"] = "yaml"
        relative = f"rule-providers/{args.name}.yaml"
        provider["path"] = "./" + relative
    if excluded_mihomo:
        provider["type"] = "file"
        provider.pop("url")
        provider.pop("interval")
    config.setdefault("rule-providers", {})[args.name] = provider
    route = f"RULE-SET,{args.name},{args.policy}" + (
        ",no-resolve" if args.no_resolve else ""
    )
    routes = config.setdefault("rules", [])
    index = next(
        (i for i, line in enumerate(routes) if fields(line)[0] == "MATCH"), len(routes)
    )
    if args.before:
        try:
            index = routes.index(args.before)
        except ValueError:
            raise RuleError("--before 必须与现有路由整行完全一致") from None
    routes.insert(index, route)
    validate_config(config)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".rules-url-", dir=output.parent
    ) as temporary:
        stage = Path(temporary) / "package"
        stage.mkdir()
        dependencies = [
            *(config.get("rule-providers") or {}).values(),
            *(config.get("proxy-providers") or {}).values(),
        ]
        for item in dependencies:
            if item.get("type") == "file" and item is not provider:
                source = contained_file(args.config.parent, item["path"])
                destination = stage / source.relative_to(args.config.parent.resolve())
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        validate_config(config, stage)
        (stage / "config.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        )
        write_lines(stage / "classical.list", lines)
        report = {
            "url": args.url,
            "sha256": digest,
            "format": fmt,
            "behavior": behavior,
            "count": len(lines),
            "mihomo_excluded": excluded_mihomo,
            "clients": {},
        }
        exports = [
            line + ",no-resolve"
            if args.no_resolve
            and line.startswith(("IP-CIDR,", "IP-CIDR6,"))
            and not line.endswith(",no-resolve")
            else line
            for line in lines
        ]
        for client in CAPABILITIES:
            converted, excluded = client_lines(
                exports, client, args.policy if client == "quantumultx" else None
            )
            if client == "clash":
                (stage / "clash.yaml").write_text(
                    yaml.safe_dump({"payload": converted}, allow_unicode=True)
                )
            else:
                write_lines(stage / (client + ".list"), converted)
            report["clients"][client] = {"count": len(converted), "excluded": excluded}
        (stage / "source.lock.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        if output.exists():
            output.rmdir()
        stage.rename(output)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--name", default="自定义URL")
    parser.add_argument("--policy", default="全球互联")
    parser.add_argument(
        "--format", choices=("auto", "mrs", "text", "yaml"), default="auto"
    )
    parser.add_argument(
        "--behavior", choices=("auto", "domain", "ipcidr", "classical"), default="auto"
    )
    parser.add_argument("--sha256")
    parser.add_argument("--config", type=Path, default=ROOT / "Template/Meta.yaml")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/url-rule")
    parser.add_argument("--mihomo", default="mihomo")
    parser.add_argument("--before", help="插入到指定现有路由前，默认在 MATCH 前")
    parser.add_argument("--no-resolve", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = convert(args)
        print(
            json.dumps(
                {key: report[key] for key in ("sha256", "format", "behavior", "count")},
                ensure_ascii=False,
            )
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        yaml.YAMLError,
    ) as error:
        # 不打印 URL、订阅或底层内核日志。
        print(
            "单 URL 转换失败："
            + (str(error) if isinstance(error, RuleError) else type(error).__name__),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
