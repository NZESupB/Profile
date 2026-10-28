#!/usr/bin/env python3
"""按个人规则白名单准备发布目录，并校验公开规则 URL。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import time
from urllib.parse import quote

from rule_formats import RuleError, fetch_url, github_proxy_url
from rules import contained_file, load_yaml, sha256


def publication_files(package, manifest):
    """从个人来源与产物重建白名单，拒绝配置、第三方文件或缺失项。"""
    expected = set()
    for name in manifest["inputs"]:
        path = Path(name)
        if path.parent == Path("Rule") and path.suffix == ".list":
            expected.add("rules/source/" + path.name)
    for item in manifest["artifacts"]:
        if item.get("origin") != "local":
            continue
        for key in ("path", "source_path"):
            if key not in item:
                continue
            path = item[key]
            parts = Path(path).parts
            if (
                len(parts) != 4
                or parts[:2] != ("rules", "local")
                or Path(path).suffix not in {".mrs", ".list"}
                or "rules/source/" + parts[2] + ".list" not in expected
            ):
                raise RuleError("个人发布产物路径不符合来源范围")
            expected.add(path)
    declared = manifest.get("publish_files", [])
    if not expected or len(declared) != len(set(declared)) or set(declared) != expected:
        raise RuleError("个人规则发布清单缺失、重复或包含额外文件")
    for path in expected:
        if (
            sha256(contained_file(package, path).read_bytes())
            != manifest["files"][path]
        ):
            raise RuleError(f"发布前个人规则哈希不符: {path}")
    return sorted(expected)


def stage_publication(package, output):
    manifest = json.loads((package / "manifest.json").read_text())
    if manifest["mode"] != "mrs":
        raise RuleError("只能发布正式 MRS 包")
    paths = publication_files(package, manifest)
    # 必须是全新目录；绝不删除或覆盖既有目录。
    output.mkdir(parents=True, exist_ok=False)
    for path in paths:
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contained_file(package, path), target)
    return {"files": len(paths), "verified": True}


def verify_public(package, base, attempts=4, retry_delay=10):
    manifest = json.loads((package / "manifest.json").read_text())
    if manifest["mode"] != "mrs":
        raise RuleError("只能发布验证正式 MRS 包")
    paths = publication_files(package, manifest)
    base = github_proxy_url(base).rstrip("/")
    targets = [
        (path, base + "/" + quote(path), manifest["files"][path]) for path in paths
    ]
    return {"base": base, **verify_targets(targets, attempts, retry_delay)}


def verify_upstreams(package, attempts=4, retry_delay=10):
    manifest = json.loads((package / "manifest.json").read_text())
    targets = []
    for item in manifest["artifacts"]:
        deployment = item.get("deployment", {})
        if deployment.get("type") == "http":
            targets.append(
                (
                    item["name"],
                    github_proxy_url(deployment["url"]),
                    item["remote_sha256"],
                )
            )
    if not targets:
        raise RuleError("上游直引清单为空")
    return verify_targets(targets, attempts, retry_delay)


def verify_targets(targets, attempts, retry_delay):
    def check(target):
        path, url, digest = target
        for attempt in range(attempts):
            try:
                raw = fetch_url(url)
                if sha256(raw) == digest:
                    return {"path": path, "verified": True, "attempts": attempt + 1}
                reason = "公开文件哈希不匹配，可能仍为旧缓存"
            except RuleError as error:
                reason = str(error)
            if attempt + 1 < attempts:
                time.sleep(retry_delay)
        return {"path": path, "verified": False, "reason": reason}

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(check, targets))
    return {
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
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--report", type=Path)
    action.add_argument("--stage", type=Path, help="复制到全新个人规则发布目录")
    parser.add_argument(
        "--upstreams", action="store_true", help="验证上游地址而非个人发布文件"
    )
    args = parser.parse_args()
    if args.upstreams and args.stage:
        parser.error("--upstreams 需要 --report，不能与 --stage 同用")
    if args.stage:
        print(json.dumps(stage_publication(args.package.resolve(), args.stage)))
        return 0
    report = (
        verify_upstreams(args.package.resolve())
        if args.upstreams
        else verify_public(
            args.package.resolve(), load_yaml(args.manifest)["publish_base"]
        )
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"checked": report["checked"], "verified": report["verified"]}))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
