"""读取个人 MRS 主列表及按原顺序插入的 classical 配套文件。"""

from __future__ import annotations

from pathlib import Path
import re

from rule_formats import RuleError, split_fields


def read_local_source(path: Path, spec: dict, root: Path, mrs_kinds: set[str]):
    files = {}

    def read(source):
        source = source.resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise RuleError("个人规则文件越界或缺失")
        name = "Rule/" + source.relative_to(root.resolve()).as_posix()
        raw = source.read_bytes()
        files[name] = raw
        return [
            (text, name, number)
            for number, text in enumerate(raw.decode("utf-8-sig").splitlines(), 1)
        ]

    def active(text):
        return bool(text.strip()) and not text.lstrip().startswith(("#", ";", "//"))

    parts, current = {}, None
    if spec.get("classical"):
        companion = Path(spec["classical"])
        if companion.is_absolute() or companion.parent != Path("classical"):
            raise RuleError("classical 配套文件必须位于规则目录的 classical/ 下")
        for row in read(root / companion):
            text, name, number = row
            marker = re.fullmatch(r"#\s*@part\s+(\d{3})", text.strip())
            if marker:
                if marker[1] == "000":
                    raise RuleError(f"{name}:{number}: classical 段号必须从 001 开始")
                current = marker[1]
                if current in parts:
                    raise RuleError(f"{name}:{number}: classical 段号重复")
                parts[current] = []
            elif re.match(r"#\s*@(part|classical)\b", text.strip()):
                raise RuleError(f"{name}:{number}: classical 分段标记格式错误")
            elif active(text):
                if current is None:
                    raise RuleError(f"{name}:{number}: classical 规则缺少 @part 段号")
                if split_fields(text.strip())[0] in mrs_kinds:
                    raise RuleError(f"{name}:{number}: 域名/IP 规则应放在主列表")
                parts[current].append(row)
            elif current is not None:
                parts[current].append(row)
        if not parts or any(
            not any(active(row[0]) for row in rows) for rows in parts.values()
        ):
            raise RuleError("classical 配套文件包含空段或没有规则")

    expanded, used = [], set()
    for row in read(path):
        text, name, number = row
        marker = re.fullmatch(r"#\s*@classical\s+(\d{3})", text.strip())
        if marker:
            if marker[1] == "000":
                raise RuleError(f"{name}:{number}: classical 段号必须从 001 开始")
            part = marker[1]
            if part not in parts or part in used:
                raise RuleError(f"{name}:{number}: classical 段号缺失或重复引用")
            used.add(part)
            expanded.extend(parts[part])
        else:
            if re.match(r"#\s*@(part|classical)\b", text.strip()):
                raise RuleError(f"{name}:{number}: classical 顺序标记格式错误")
            if active(text) and split_fields(text.strip())[0] not in mrs_kinds:
                raise RuleError(
                    f"{name}:{number}: 非 MRS 规则必须移至 classical 配套文件"
                )
            expanded.append(row)
    if used != set(parts):
        raise RuleError("classical 配套文件存在未引用的段")
    raw = ("\n".join(row[0] for row in expanded) + "\n").encode()
    locations = [(name, number) for _, name, number in expanded]
    return raw, locations, files
