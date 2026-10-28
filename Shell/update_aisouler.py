#!/usr/bin/env python3
"""锁定 AIsouler 实际来源；只回读现成 MRS，不进行二进制编译。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

import yaml

from rule_formats import RuleError, UniqueLoader, fetch_url, read_mrs
from rule_reference import lock_inline_rules

ROOT = Path(__file__).resolve().parents[1]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-commit", required=True)
    parser.add_argument("--bett-commit", required=True)
    parser.add_argument("--emby-commit", required=True)
    parser.add_argument("--emos-commit", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--mihomo", required=True)
    args = parser.parse_args()
    commits = {
        "AIsouler/MyClash": args.config_commit,
        "appshubcc/bett-rules": args.bett_commit,
        "666OS/rules": args.emby_commit,
        "binaryu/emos-proxy-rule": args.emos_commit,
    }
    if any(not re.fullmatch(r"[a-f0-9]{40}", value) for value in commits.values()):
        raise RuleError("来源必须使用完整不可变提交 SHA")
    core = shutil.which(args.mihomo)
    if not core:
        raise RuleError("找不到 MRS 回读内核")
    args.cache.mkdir(parents=True, exist_ok=True)

    def download(url):
        # 更新操作按 URL 缓存不可变提交；可变来源每次重新下载。
        stable = any(commit in url for commit in commits.values())
        receipt = args.cache / ("url-" + digest(url.encode()) + ".json")
        if stable and receipt.exists():
            entry = json.loads(receipt.read_text())
            raw = (args.cache / entry["sha256"]).read_bytes()
            if digest(raw) != entry["sha256"]:
                raise RuleError("更新缓存哈希不一致")
            return raw, entry
        raw = fetch_url(url)
        entry = {"url": url, "sha256": digest(raw)}
        (args.cache / entry["sha256"]).write_bytes(raw)
        if stable:
            receipt.write_text(json.dumps(entry) + "\n")
        return raw, entry

    config_url = f"https://raw.githubusercontent.com/AIsouler/MyClash/{args.config_commit}/Config/mihomoConfig.yaml"
    config_raw, config_entry = download(config_url)
    config = yaml.load(config_raw, Loader=UniqueLoader)
    providers = config["rule-providers"]
    old_lock = json.loads((ROOT / "Rule/upstreams.lock.json").read_text())
    spec = yaml.load((ROOT / "Rule/build.yaml").read_text(), Loader=UniqueLoader)

    with tempfile.TemporaryDirectory(prefix="aisouler-lock-") as folder:
        stage = Path(folder)

        def lock_provider(pair):
            name, provider = pair
            if provider["format"] != "mrs" or provider["behavior"] not in {
                "domain",
                "ipcidr",
            }:
                raise RuleError(f"{name}: 参考配置出现未支持的新来源格式")
            original = provider["url"]
            match = re.fullmatch(
                r"https://cdn\.jsdmirror\.com/gh/([^/]+/[^@]+)@[^/]+/(.+)", original
            )
            if match:
                repository, path = match.groups()
                if repository not in commits:
                    raise RuleError(f"{name}: 未锁定的新仓库")
                url = f"https://raw.githubusercontent.com/{repository}/{commits[repository]}/{path}"
            elif (
                name == "cn_additional"
                and original
                == "https://static-file-global.353355.xyz/rules/cn-additional-list.mrs"
            ):
                repository, path, url = None, None, original
            else:
                raise RuleError(f"{name}: 未明确支持的来源 URL")
            raw, entry = download(url)
            behavior, decoded = read_mrs(raw, core, provider["behavior"])
            if repository == "appshubcc/bett-rules":
                _, entry["text"] = download(url.removesuffix(".mrs") + ".list")
                if behavior == "domain":
                    _, entry["classical"] = download(
                        url.replace("/geosite/", "/geosite/classical/").removesuffix(
                            ".mrs"
                        )
                        + ".yaml"
                    )
            elif repository == "666OS/rules":
                _, entry["text"] = download(url.removesuffix(".mrs") + ".txt")
            elif repository == "binaryu/emos-proxy-rule":
                text_raw, text_entry = download(
                    url.removesuffix(".mrs") + "-domain.yaml"
                )
                data = yaml.load(text_raw, Loader=UniqueLoader)
                text = ("\n".join(data["payload"]) + "\n").encode()
                entry["text"] = {
                    "sha256": digest(text),
                    "snapshot": f"snapshots/aisouler/{name}.txt",
                    "derived_from": text_entry,
                }
                (stage / f"{name}.txt").write_bytes(text)
                _, entry["classical"] = download(url.removesuffix(".mrs") + ".yaml")
            else:
                # 可变、仅二进制来源保存已下载的 MRS 及其同版回读文本。
                entry["snapshot"] = f"snapshots/aisouler/{name}.mrs"
                entry["text"] = {
                    "sha256": digest(decoded),
                    "snapshot": f"snapshots/aisouler/{name}.txt",
                    "derived_from_mrs_sha256": entry["sha256"],
                }
                (stage / f"{name}.mrs").write_bytes(raw)
                (stage / f"{name}.txt").write_bytes(decoded)
            reuse = None
            for key, item in spec["upstreams"].items():
                old = old_lock["rules"][key]
                if item["format"] != "mrs" or item["behavior"] != behavior:
                    continue
                if old["sha256"] == entry["sha256"] and old.get(
                    "classical_sha256", old.get("classical", {}).get("sha256")
                ) == entry.get("classical", {}).get("sha256"):
                    reuse = key
                    break
            result = {
                "original_url": original,
                "behavior": behavior,
                "reuse": reuse,
                "entry": entry,
            }
            print(
                f"锁定 {name}: {'复用 ' + reuse if reuse else '独立来源'}", flush=True
            )
            return name, result

        with ThreadPoolExecutor(max_workers=6) as pool:
            locked = dict(pool.map(lock_provider, providers.items()))
        inline, payloads = lock_inline_rules(config, config_entry)
        for filename, payload in payloads.items():
            (stage / filename).write_bytes(payload)
        result = {
            "version": 1,
            "config": config_entry,
            "commits": commits,
            "providers": locked,
            "inline": inline,
        }
        target = ROOT / "Rule/snapshots/aisouler"
        target.mkdir(parents=True, exist_ok=True)
        for path in stage.iterdir():
            shutil.copyfile(path, target / path.name)
        (ROOT / "Rule/aisouler.lock.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "providers": len(locked),
                    "inline": len(inline),
                    "reused": sum(
                        item["reuse"] is not None for item in locked.values()
                    ),
                }
            )
        )


if __name__ == "__main__":
    main()
