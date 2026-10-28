#!/usr/bin/env python3
"""发布后下载真实公开 URL，校验配置和全部规则 provider 的内容哈希。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from urllib.parse import quote

from rule_formats import RuleError, fetch_url, github_proxy_url
from rules import contained_file, load_yaml, sha256


def verify_public(package, base, attempts=4, retry_delay=10):
    manifest = json.loads((package / "manifest.json").read_text())
    if manifest["mode"] != "mrs":
        raise RuleError("只能发布验证正式 MRS 包")
    paths = list(
        dict.fromkeys(
            [
                "mihomo.yaml",
                "config.remote.yaml",
                *[item["path"] for item in manifest["artifacts"]],
            ]
        )
    )
    for path in paths:
        if (
            sha256(contained_file(package, path).read_bytes())
            != manifest["files"][path]
        ):
            raise RuleError(f"发布前本地产物哈希不符: {path}")
    base = github_proxy_url(base).rstrip("/")

    def check(path):
        for attempt in range(attempts):
            try:
                raw = fetch_url(base + "/" + quote(path))
                if sha256(raw) == manifest["files"][path]:
                    return {"path": path, "verified": True, "attempts": attempt + 1}
                reason = "公开文件哈希不匹配，可能仍为旧缓存"
            except RuleError as error:
                reason = str(error)
            if attempt + 1 < attempts:
                time.sleep(retry_delay)
        return {"path": path, "verified": False, "reason": reason}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(check, paths))
    return {
        "base": base,
        "checked": len(results),
        "verified": all(item["verified"] for item in results),
        "files": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "Rule/build.yaml",
    )
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify_public(
        args.package.resolve(), load_yaml(args.manifest)["publish_base"]
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"checked": report["checked"], "verified": report["verified"]}))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
