#!/usr/bin/env python3
"""Audit explicit task/role rule loads; counts are not tokens or observed usage.

The manifest is a reviewed inventory, not a Markdown dependency resolver. ATX
heading selectors include either their whole subtree or only their direct body.
Calls start cold: repeated node references are deduplicated within a call, while
another role's call counts its own preloads and rules again.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/instruction-loads.json"
METRICS = ("nonspace_chars", "utf8_bytes")
ID = re.compile(r"^[a-z][a-z0-9_-]*$")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def objects(items, label):
    if not isinstance(items, list) or not items:
        raise ValueError(f"{label} must be a nonempty list")
    result = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not ID.fullmatch(item["id"]):
            raise ValueError(f"Invalid {label} id")
        if item["id"] in result:
            raise ValueError(f"Duplicate {label} id: {item['id']}")
        result[item["id"]] = item
    return result


def references(value, label, *, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a {'possibly empty' if allow_empty else 'nonempty'} list")
    if any(not isinstance(item, str) or not ID.fullmatch(item) for item in value):
        raise ValueError(f"Invalid reference in {label}")
    if len(set(value)) != len(value):
        raise ValueError(f"Duplicate reference in {label}")
    return value


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")


def limits(value, label):
    if not isinstance(value, dict) or set(value) != set(METRICS):
        raise ValueError(f"{label} must declare exactly {', '.join(METRICS)}")
    if any(type(value[metric]) is not int or value[metric] <= 0 for metric in METRICS):
        raise ValueError(f"Invalid positive integer limit: {label}")
    return value


def headings(text):
    """Return ATX headings outside fenced code, with original byte offsets."""
    found = []
    position = 0
    fence = None
    lines = text.splitlines(keepends=True)
    for number, line in enumerate(lines, 1):
        probe = line.removesuffix("\n").removesuffix("\r")
        if number == 1:
            probe = probe.removeprefix("\ufeff")
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", probe)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
        elif marker:
            # A backtick fence's info string cannot contain a backtick.
            if marker[1][0] != "`" or "`" not in marker[2]:
                fence = (marker[1][0], len(marker[1]))
        else:
            match = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$", probe)
            if match:
                title = re.sub(r"[ \t]+#+[ \t]*$", "", match[2] or "").strip()
                found.append({"title": title, "level": len(match[1]), "line": number, "byte": position})
        position += len(line.encode("utf-8"))
    return found, len(lines)


def source(root, node):
    relative = node["path"]
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).drive
            or any(part in (".", "..", "") for part in relative.split("/"))):
        raise ValueError(f"Invalid repository-relative path: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Path escapes source root: {relative}")
    if not path.is_file():
        raise ValueError(f"Missing instruction file: {relative}")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Instruction file is not UTF-8: {relative}") from error
    parsed, line_count = headings(text)
    start, end, first, last = 0, len(raw), 1, line_count
    section = node.get("section")
    scope = node.get("scope", "subtree")
    if scope not in ("body", "subtree") or (section is None and "scope" in node):
        raise ValueError(f"Invalid section scope: {node['id']}")
    if section is not None:
        require_text(section, f"Section for {node['id']}")
        matches = [(index, item) for index, item in enumerate(parsed) if item["title"] == section]
        if not matches:
            raise ValueError(f"Missing ATX section: {relative}: {section}")
        if len(matches) != 1:
            raise ValueError(f"Ambiguous ATX section: {relative}: {section}")
        index, heading = matches[0]
        start, first = heading["byte"], heading["line"]
        for following in parsed[index + 1:]:
            if scope == "body" or following["level"] <= heading["level"]:
                end, last = following["byte"], following["line"] - 1
                break
    selected = raw[start:end]
    if not selected.decode("utf-8").strip():
        raise ValueError(f"Empty instruction source: {relative}")
    return {"id": node["id"], "path": relative, "section": section,
            "scope": scope if section is not None else "file", "start_line": first, "end_line": last,
            "byte_start": start, "byte_end": end, "file_sha256": digest(raw),
            "slice_sha256": digest(selected), "utf8_bytes": len(selected),
            "nonspace_chars": sum(not char.isspace() for char in selected.decode("utf-8")),
            "resolved_path": path.relative_to(root).as_posix()}


def audit(config, root=ROOT):
    """Validate every registered branch and report cold rule loads per call."""
    root = Path(root).resolve()
    if not isinstance(config, dict) or config.get("schema") != 1:
        raise ValueError("Unsupported instruction-load manifest schema")
    require_text(config.get("scope"), "Manifest scope")
    nodes = objects(config.get("nodes"), "node")
    scenarios = objects(config.get("scenarios"), "scenario")
    sources = {}
    for identifier, node in nodes.items():
        if ("path" in node) == ("includes" in node):
            raise ValueError(f"Node must have either path or includes: {identifier}")
        if "path" in node:
            sources[identifier] = source(root, node)
        else:
            references(node["includes"], f"Node {identifier}")
    expanded = {}

    def expand(identifier, chain=()):
        if identifier not in nodes:
            raise ValueError(f"Unknown node reference: {identifier}")
        if identifier in chain:
            raise ValueError(f"Cyclic dependency: {' -> '.join((*chain, identifier))}")
        if identifier not in expanded:
            node = nodes[identifier]
            selected = {identifier} if "path" in node else set()
            if "includes" in node:
                for item in node["includes"]:
                    selected.update(expand(item, (*chain, identifier)))
            expanded[identifier] = selected
        return expanded[identifier]

    for identifier in nodes:
        expand(identifier)

    # Reject different selectors over the same bytes. Reuse the existing node ID
    # for duplicate dependencies; split a parent into body + children if needed.
    ordered = sorted(sources.values(), key=lambda item: (item["resolved_path"], item["byte_start"], item["byte_end"]))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if right["resolved_path"] != left["resolved_path"]:
                break
            if right["byte_start"] >= left["byte_end"]:
                break
            raise ValueError(f"Overlapping instruction slices: {left['id']} and {right['id']}")

    report = []
    for identifier, scenario in scenarios.items():
        require_text(scenario.get("task"), f"Task for {identifier}")
        require_text(scenario.get("assumptions"), f"Assumptions for {identifier}")
        calls = objects(scenario.get("calls"), f"call in {identifier}")
        for call_id, call in calls.items():
            label = f"{identifier}/{call_id}"
            require_text(call.get("role"), f"Role for {label}")
            preloads = references(call.get("preload", []), f"Preload for {label}", allow_empty=True)
            required = references(call.get("required"), f"Required for {label}")
            budget = limits(call.get("limits"), f"Limits for {label}")
            base_budget = limits(call["base_limits"], f"Base limits for {label}") if "base_limits" in call else budget
            if any(base_budget[metric] > budget[metric] for metric in METRICS):
                raise ValueError(f"Base limit exceeds call limit: {label}")
            if "branches" in call and not isinstance(call["branches"], list):
                raise ValueError(f"Branches must be a list: {label}")
            branches = objects(call["branches"], f"branch in {label}") if call.get("branches") else {}
            for branch_id, branch in branches.items():
                require_text(branch.get("when"), f"Condition for {label}/{branch_id}")
                references(branch.get("includes"), f"Branch {label}/{branch_id}")
            if "combinations" in call and not isinstance(call["combinations"], list):
                raise ValueError(f"Combinations must be a list: {label}")
            combinations = objects(call["combinations"], f"combination in {label}") if call.get("combinations") else {}
            variants = [("base", [], "required rules and declared host/agent preloads")]
            variants.extend((f"branch:{key}", [key], item["when"]) for key, item in branches.items())
            for key, combination in combinations.items():
                picked = references(combination.get("branches"), f"Combination {label}/{key}")
                if unknown := set(picked) - set(branches):
                    raise ValueError(f"Unknown branch reference in {label}/{key}: {sorted(unknown)}")
                require_text(combination.get("when"), f"Combination condition for {label}/{key}")
                variants.append((f"combination:{key}", picked, combination["when"]))
            if branches:
                variants.append(("registered_branch_union", list(branches),
                                 "Upper bound of all registered additive branches; not a claim of complete Markdown closure or a required runtime read."))
            for variant, picked, condition in variants:
                ids = [*preloads, *required]
                for branch_id in picked:
                    ids.extend(branches[branch_id]["includes"])
                selected = set().union(*(expand(item) for item in ids))
                preloaded = set().union(*(expand(item) for item in preloads)) if preloads else set()
                files = [sources[item] for item in sorted(selected)]
                totals = {metric: sum(item[metric] for item in files) for metric in METRICS}
                selected_budget = base_budget if variant == "base" else budget
                headroom = {metric: selected_budget[metric] - totals[metric] for metric in METRICS}
                report.append({"scenario": identifier, "task": scenario["task"], "assumptions": scenario["assumptions"],
                               "call": call_id, "role": call["role"], "variant": variant, "condition": condition,
                               "branches": picked, "preload_nodes": preloads, "required_nodes": required,
                               "preload_totals": {metric: sum(sources[item][metric] for item in preloaded) for metric in METRICS},
                               "source_ids": sorted(selected), "totals": totals, "limits": dict(selected_budget),
                               "headroom": headroom, "passed": all(value >= 0 for value in headroom.values())})
    return {"schema": 1, "scope": config["scope"], "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "method": "Exact UTF-8 source slices; nonspace_chars counts Unicode characters for which str.isspace() is false. No normalization, tokenizer, or actual usage measurement. Deduplicated within each cold call, counted again across calls/roles.",
            "not_measured": ["dynamic manuscripts, outlines, contracts and retrieved story state", "system and host prompts, skill discovery metadata and other conversation history", "tool schemas, calls and returned content", "reasoning, output and cached tokens, pricing or provider usage", "whether an agent actually read the registered rules", "unregistered conditional branches or automatic Markdown dependency closure", "literary quality, originality, platform outcomes or reader response"],
            "sources": [{key: value for key, value in item.items() if key != "resolved_path"} for item in sources.values()],
            "loads": report, "passed": all(item["passed"] for item in report)}


def markdown(report):
    lines = ["# 显式任务与角色规则加载预算", "", report["scope"], "",
             "按每个角色的一次冷调用统计 UTF-8 字节及去空白字符。相同资源在一次调用中去重，跨角色分别计算；不是 token、实际读取证据或账单。动态正文、规划、约定、状态、宿主提示与工具结果不在测量内。", "",
             "`registered_branch_union` 仅为已登记可叠加分支的字节上界；不要求实际全读，也不声明发现了全部引用或条件。", "",
             "| 任务 / 调用 | 变体 | 去空白字符 / 限额 | UTF-8 字节 / 限额 | 通过 |",
             "|---|---|---:|---:|---|"]
    for item in report["loads"]:
        lines.append(f"| {item['scenario']} / {item['call']} | {item['variant']} | {item['totals']['nonspace_chars']:,} / {item['limits']['nonspace_chars']:,} | {item['totals']['utf8_bytes']:,} / {item['limits']['utf8_bytes']:,} | {'是' if item['passed'] else '否'} |")
    lines.extend(["", "文件、标题切片范围、SHA-256、角色预加载、任务假设与剩余额度见 JSON 报告。此表只审计声明的规则预算，不能证明文学效果或运行时确实读取。"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--root", type=Path, default=ROOT, help="Source root for manifest-relative instruction paths")
    parser.add_argument("--output", type=Path, help="Optional directory for instruction-loads.json and instruction-loads.md")
    parser.add_argument("--json", action="store_true", help="Print the full report instead of the compact call summary")
    args = parser.parse_args(argv)
    try:
        raw = args.manifest.read_bytes()
        report = audit(json.loads(raw.decode("utf-8")), args.root)
        report["manifest_sha256"] = digest(raw)
        report["audit_script_sha256"] = digest(Path(__file__).read_bytes())
        if args.output:
            if args.output.is_symlink():
                raise ValueError("Output directory must not be a symbolic link")
            directory = args.output.resolve()
            outputs = [directory / "instruction-loads.json", directory / "instruction-loads.md"]
            protected = {(args.root / item["path"]).resolve() for item in report["sources"]}
            protected.update((args.manifest.resolve(), Path(__file__).resolve()))
            if any(item.resolve() in protected for item in outputs):
                raise ValueError("Output would overwrite an instruction source or manifest")
            if any(item.exists() or item.is_symlink() for item in outputs):
                raise ValueError("Output report already exists; choose a fresh output directory")
            directory.mkdir(parents=True, exist_ok=True)
            for path, content in zip(outputs, (json.dumps(report, ensure_ascii=False, indent=2) + "\n", markdown(report))):
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
        summary = {"passed": report["passed"], "manifest_sha256": report["manifest_sha256"],
                   "measured": "Explicit cold rule loads only; not tokens, dynamic story context or actual usage",
                   "loads_checked": len(report["loads"]), "calls": []}
        for scenario in dict.fromkeys((item["scenario"], item["call"]) for item in report["loads"]):
            rows = [item for item in report["loads"] if (item["scenario"], item["call"]) == scenario]
            summary["calls"].append({"scenario": scenario[0], "call": scenario[1], "base": rows[0]["totals"],
                                     "base_limits": rows[0]["limits"],
                                     "maximum": {metric: max(item["totals"][metric] for item in rows) for metric in METRICS},
                                     "maximum_limits": rows[-1]["limits"], "passed": all(item["passed"] for item in rows),
                                     "failed_variants": [item["variant"] for item in rows if not item["passed"]]})
        if args.output:
            summary["reports"] = [str(item) for item in outputs]
        print(json.dumps(report if args.json else summary, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1
    except (OSError, ValueError) as error:
        print(f"Instruction budget error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
