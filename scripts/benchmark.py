#!/usr/bin/env python3
"""Count pinned instruction files with a named tokenizer; never claim billed usage."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

# These are audited, named scenarios, not a Markdown dependency resolver.
# Re-audit this contract when a scenario or its applicable reading rules change.
SHARED_ENTRY = "skills/story-skill/SKILL.md"
WRITE_ENTRY = "skills/story-skill-write/SKILL.md"
REVIEW_ENTRY = "skills/story-skill-review/SKILL.md"
CHAPTER_GUIDE = "skills/story-skill-write/references/chapter.md"
DRAMA_GUIDE = "skills/story-skill-write/references/drama.md"
CONTENT_REVIEW = "skills/story-skill-write/references/content-review.md"
PUNCTUATION_GUIDE = "skills/story-skill-write/references/punctuation.md"
WRITING_FILES = (SHARED_ENTRY, WRITE_ENTRY, "skills/story-skill/references/project-state.md",
                 CHAPTER_GUIDE, DRAMA_GUIDE, CONTENT_REVIEW, PUNCTUATION_GUIDE)
REVIEW_FILES = (SHARED_ENTRY, REVIEW_ENTRY, CHAPTER_GUIDE, DRAMA_GUIDE, CONTENT_REVIEW, PUNCTUATION_GUIDE)
ANALYSIS_FILES = (SHARED_ENTRY, "skills/story-skill-analyze/SKILL.md",
                  "skills/story-skill-analyze/references/deep-reading.md")
REQUIRED_CANDIDATE_FILES = {
    "long_chapter_required_minimum": WRITING_FILES,
    "long_multi_line": (*WRITING_FILES, "skills/story-skill-write/references/long-form.md"),
    "short_write_conservative": WRITING_FILES,
    "long_analysis_conservative": ANALYSIS_FILES,
    "analysis_with_examples": (*ANALYSIS_FILES, "skills/story-skill-analyze/references/examples.md"),
    "review_conservative": REVIEW_FILES,
    "review_conflict": REVIEW_FILES,
}


def profile_contract_problems(config):
    """Reject omitted obligations even when the config and report agree."""
    problems = []
    for profile in config["profiles"]:
        required = REQUIRED_CANDIDATE_FILES.get(profile["id"], ())
        for relative in required:
            if relative not in profile["candidate"]:
                problems.append(f"Missing required candidate file: {profile['id']}: {relative}")
    return problems


def candidate_source(config_bytes):
    """Fingerprint the measured development source, including synthetic runtime."""
    revision = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"], text=True).strip()
    runtime = [{"path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
               for path in sorted((ROOT / "skills/story-skill/scripts").glob("*.py"))]
    return {"revision": revision, "working_tree_dirty": bool(dirty),
            "profiles_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "benchmark_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "runtime_files": runtime,
            "scope": "Measured instruction files have individual hashes in each profile. "
                     "Runtime hashes identify the synthetic fixture implementation; this is development-source evidence, not a release attestation."}


def metadata(path):
    text = path.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    fields = []
    for key in ("name", "description"):
        match = re.search(r"^" + key + r":\s*(.+)$", front, re.MULTILINE)
        if not match:
            raise ValueError(f"Missing single-line {key}: {path}")
        value = match.group(1).strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value.strip('"')
        fields.append(value)
    return "\n".join(fields)


def inventory(root, paths, enc):
    result = []
    for relative in paths:
        raw = (root / relative).read_bytes()
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        result.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                       "normalized_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                       "tokens": len(enc.encode(text, disallowed_special=()))})
    return result


def synthetic_context(enc):
    spec = importlib.util.spec_from_file_location("story_benchmark", ROOT / "skills/story-skill/scripts/story.py")
    story = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(story)
    with tempfile.TemporaryDirectory(prefix="story-context-benchmark-") as directory:
        story.Book.create(directory, "合成上下文夹具", "long")
        book = story.Book(directory)
        try:
            with book.transaction():
                book.set_meta("id", "00000000-0000-4000-8000-000000000001")
            cards = [{"id": f"c{i:03d}", "kind": "character", "source": "synthetic benchmark fixture",
                      "text": f"角色{i}在第{i}处车站保留一份信件。" + "上次交接留下的口令尚未交给其他人，任何移交都需要有当场可见的代价。" * 4,
                      "tags": ["本章" if i < 5 else f"支线{i}"], "critical": i == 0} for i in range(160)]
            book.save_notes(cards, 0)
            plan = {"goal": "拿到失踪者最后留下的信件", "stop": "只确认信封署名，暂不揭示寄信人",
                    "beats": [{"choice": "交换唯一口令", "change": "得到信封并失去退路"}],
                    "constraints": ["不添加新能力"], "requires": ["c001"], "tags": ["本章"], "length": [2400, 2800]}
            book.save_plan(1, plan, 1)
            packet = book.context(1, 8000)
            all_cards = story.dumps(list(book.cards().values()))
            packed = story.dumps(packet)
            return {"kind": "synthetic_not_upstream_runtime", "cards_total": len(cards),
                    "naive_all_cards_tokens": len(enc.encode(all_cards, disallowed_special=())),
                    "bounded_packet_tokens": len(enc.encode(packed, disallowed_special=())),
                    "packet_bytes": len(packed.encode("utf-8")), "budget_bytes": 8000,
                    "required_selected": len(packet["required_cards"]), "optional_selected": len(packet["optional_cards"]),
                    "omitted_optional": packet["omitted_optional_count"],
                    "note": "Demonstrates this implementation's retrieval, not a measured upstream project. All candidates and source contents are synthetic."}
        finally:
            book.close()


def benchmark(upstream, output):
    config_bytes = (ROOT / "benchmarks/profiles.json").read_bytes()
    config = json.loads(config_bytes.decode("utf-8"))
    problems = profile_contract_problems(config)
    if problems:
        raise SystemExit("Invalid benchmark profiles:\n" + "\n".join(problems))
    try:
        import tiktoken
    except ImportError:
        raise SystemExit("Install the optional benchmark dependency: python -m pip install -r requirements-dev.txt")
    if output is None:
        output = (ROOT / config.get("report_path", "benchmarks/results/tokens.json")).parent
    upstream = Path(upstream).resolve()
    revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if revision != config["revision"]:
        raise SystemExit(f"Benchmark profile is pinned to {config['revision']}; checkout that revision first.")
    dirty = subprocess.check_output(["git", "-C", str(upstream), "status", "--porcelain"], text=True).strip()
    if dirty:
        raise SystemExit("Use a clean upstream checkout; modified files would invalidate the pinned comparison.")
    enc = tiktoken.get_encoding(config["encoding"])
    profiles = []
    for item in config["profiles"]:
        before = inventory(upstream, item["upstream"], enc)
        after = inventory(ROOT, item["candidate"], enc)
        a, b = sum(f["tokens"] for f in before), sum(f["tokens"] for f in after)
        profiles.append({"id": item["id"], "label": item["label"], "upstream_tokens": a,
                         "candidate_tokens": b, "reduction_percent": round((1 - b / a) * 100, 2),
                         "upstream_files": before, "candidate_files": after, "scope": item["scope"]})
    upstream_skills = sorted((upstream / "skills").glob("*/SKILL.md"))
    candidate_skills = sorted((ROOT / "skills").glob("*/SKILL.md"))
    discovery = {"upstream_skills": len(upstream_skills), "candidate_skills": len(candidate_skills),
                 "upstream_name_description_tokens": sum(len(enc.encode(metadata(p), disallowed_special=())) for p in upstream_skills),
                 "candidate_name_description_tokens": sum(len(enc.encode(metadata(p), disallowed_special=())) for p in candidate_skills),
                 "scope": "Name and description only; host wrappers, paths, truncation and coexistence with installed skills excluded."}
    result = {"schema": 1, "repository": config["repository"], "upstream_revision": revision,
              "generated_at_utc": datetime.now(timezone.utc).isoformat(),
              "candidate_source": candidate_source(config_bytes),
              "tiktoken_version": importlib.metadata.version("tiktoken"), "encoding": config["encoding"],
              "method": "Sum exact tokens per LF-normalized instruction file, disallowed_special=(). This is a reproducible tokenizer proxy, not billed tokens or full-turn usage.",
              "profiles": profiles, "discovery": discovery, "synthetic_context": synthetic_context(enc),
              "not_measured": ["reasoning tokens", "output manuscript tokens", "tool calls and outputs", "cached-input pricing",
                               "actual provider usage", "model-specific tokenizer", "literary quality", "full CLI end-to-end agent behavior",
                               "section-only reference loading", "automatic Markdown dependency closure"]}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "tokens.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Token 基准", "", f"上游提交：`{revision}`；计数器：tiktoken {result['tiktoken_version']} / `{config['encoding']}`。", "",
             f"本次开发工作树基线：`{result['candidate_source']['revision']}`；有未提交改动：`{result['candidate_source']['working_tree_dirty']}`。各指令文件、配置、计数脚本及合成夹具运行代码的 SHA-256 见 JSON。", "",
             "以下只统计冷加载的指令文件。计数可复现，不等同于实际账单或一轮总 token；正文、推理、工具结果和缓存计价均未纳入。", "",
             "场景按显式列出的适用必读文件校验；引用只需部分段落时仍计完整文件，未自动推导 Markdown 引用闭包。上游仅入口的场景是保守下限，不能据此比较双方完整审稿能力。", "",
             "| 场景 | 上游 tokens | 新版 tokens | 指令减少 |", "|---|---:|---:|---:|"]
    lines += [f"| {p['label']} | {p['upstream_tokens']:,} | {p['candidate_tokens']:,} | {p['reduction_percent']}% |" for p in profiles]
    lines += ["", "原始文件清单、SHA-256 和每个场景的统计边界见 [tokens.json](tokens.json)。", "",
              f"发现元数据：上游 {discovery['upstream_skills']} 个入口合计 {discovery['upstream_name_description_tokens']} tokens；新版 {discovery['candidate_skills']} 个入口 {discovery['candidate_name_description_tokens']} tokens。仅含名称/描述；旧技能仍启用时，不能把这个差额当成真实节省。", "",
              "合成召回夹具（不是上游实测）：", "", "```json", json.dumps(result["synthetic_context"], ensure_ascii=False, indent=2), "```", "",
              "没有证据据此声称小说质量更高。质量、实际使用量和重写次数需要相同任务、相同模型、相同输出长度的端到端对照。"]
    (output / "tokens.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str((output / "tokens.md").resolve()), "profiles": [
        {k: p[k] for k in ("id", "upstream_tokens", "candidate_tokens", "reduction_percent")} for p in profiles], "discovery": discovery}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--upstream", required=True)
    p.add_argument("--output", help="Output directory; defaults to the configured benchmark report directory")
    args = p.parse_args()
    print(json.dumps(benchmark(args.upstream, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
