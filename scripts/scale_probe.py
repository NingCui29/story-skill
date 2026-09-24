#!/usr/bin/env python3
"""Reproducible synthetic capacity evidence; never a novel-quality evaluation."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"
BASELINE = ROOT / "benchmarks/results/scaling.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_snapshot():
    return {path.name: file_sha(path) for path in sorted(TOOL.parent.glob("*.py"))}


def error_details(error):
    return {"type": type(error).__name__, "message": str(error),
            "code": getattr(error, "code", None), "details": getattr(error, "details", {})}


def measure(book, story, operation, destination):
    """Count only work inside this operation, including its real nested calls."""
    reads = {"file_reads": 0, "file_bytes_read": 0,
             "manuscript_file_reads": 0, "manuscript_bytes_read": 0}
    decodes = {"json_decode_calls": 0, "json_decode_input_bytes": 0, "decoded_card_objects": 0}
    sql = Counter()
    searches = []
    original_read, original_loads, original_query = Path.read_bytes, json.loads, story.search.query

    def counted_read(path):
        data = original_read(path)
        reads["file_reads"] += 1
        reads["file_bytes_read"] += len(data)
        if book.root / "chapters" in path.parents:
            reads["manuscript_file_reads"] += 1
            reads["manuscript_bytes_read"] += len(data)
        return data

    def counted_loads(data, *args, **kwargs):
        decodes["json_decode_calls"] += 1
        decodes["json_decode_input_bytes"] += len(data.encode("utf-8") if isinstance(data, str) else data)
        value = original_loads(data, *args, **kwargs)
        if isinstance(value, dict) and {"id", "kind", "text", "source"}.issubset(value):
            decodes["decoded_card_objects"] += 1
        return value

    def traced_statement(statement):
        parts = statement.lstrip().split(None, 1)
        sql[parts[0].upper() if parts else "EMPTY"] += 1

    def counted_query(*args, **kwargs):
        value = original_query(*args, **kwargs)
        searches.append({key: value[key] for key in
                         ("query", "metrics", "scope", "complete", "search_complete", "truncated_reasons")})
        return value

    book.db.set_trace_callback(traced_statement)
    value = None
    started = time.perf_counter()
    tracemalloc.start()
    try:
        with patch.object(Path, "read_bytes", counted_read), patch.object(json, "loads", counted_loads), \
                patch.object(story.search, "query", counted_query):
            value = operation()
        destination["ok"] = True
        return value
    except BaseException as error:
        destination.update(ok=False, error=error_details(error))
        raise
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        book.db.set_trace_callback(None)
        destination.update(elapsed_seconds=round(time.perf_counter() - started, 6),
                           python_peak_allocated_bytes=peak, **reads, **decodes,
                           sql_statement_callbacks=sum(sql.values()), sql_callbacks_by_verb=dict(sql),
                           search_calls=searches)
        if value is not None:
            destination["output_json_bytes"] = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            if isinstance(value, dict):
                for key in ("integrity", "budget", "exports_complete", "scope_exports_complete", "committed"):
                    if key in value:
                        destination[key] = value[key]


def make_fixture(story, root, count, card_count):
    story.Book.create(root, "合成容量夹具，不是真实小说", "long")
    book = story.Book(root)
    body = "甲" * 2500
    sha = story.digest(body)
    (root / "chapters/第一卷 容量夹具").mkdir(parents=True)
    (root / "load-draft.md").write_text(body, encoding="utf-8")
    try:
        with book.transaction():
            for number in range(1, count + 1):
                relative = f"chapters/第一卷 容量夹具/第{number}章 容量夹具.md"
                (root / relative).write_text(body, encoding="utf-8")
                summary = f"Synthetic chapter {number}; no semantic validation."
                book.db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,0)",
                                (number, body, sha, summary, "{}", "fixture"))
                book.db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (relative, body, sha, sha))
                book.set_meta(f"chapter_path:{number}", relative)
                book.index_chapter(number, body, summary)
            book.set_meta("last_chapter", count)
            for number in range(card_count):
                text = ("合成检索夹具：江棠请杜承安保管船票。" if number == 0 else
                        "历史支线事实，仅作容量夹具。")
                book.put_card(story.valid_card({"id": f"c{number:05d}", "kind": "fact", "text": text,
                    "source": "synthetic, no semantic validation", "tags": [f"branch{number}"],
                    "critical": False}))
        plan = {"volume_dir": "第一卷 容量夹具", "title": "容量探针", "goal": "容量探针", "stop": "一次结构提交", "constraints": [],
                "requires": ["c00000"], "tags": ["current"], "length": [2500, 2500],
                "beats": [{"choice": "生成重复字符夹具", "change": "测量读取放大"}]}
        saved = book.save_plan(count + 1, plan, book.meta("revision"))
        if saved.get("exports_complete") is False:
            raise AssertionError("Fixture plan exports failed")
        return {"chapter_body_sha256": sha, "chapter_unique_body_count": 1,
                "database_bytes": book.path.stat().st_size,
                "search_documents": book.db.execute("SELECT COUNT(*) FROM search_documents").fetchone()[0],
                "search_postings": book.db.execute("SELECT COUNT(*) FROM search_postings").fetchone()[0],
                "core_objects": book.db.execute("SELECT COUNT(*) FROM core_objects").fetchone()[0]}
    finally:
        book.close()


def cli_status(root, integrity, timeout):
    command = [sys.executable, "-B", "-X", "utf8", str(TOOL), "status", "--book", str(root),
               "--integrity", integrity]
    started = time.perf_counter()
    process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    result = {"command": command, "returncode": process.returncode,
              "elapsed_seconds": round(time.perf_counter() - started, 6), "stderr": process.stderr,
              "instrumentation": "separate process; no Python allocation/read/SQL counters attached"}
    if process.returncode:
        result.update(ok=False, stdout=process.stdout)
        return result
    value = json.loads(process.stdout)
    result.update(ok=value.get("integrity", {}).get("mode") == integrity, integrity=value.get("integrity"))
    return result


def run_case(story, root, count, card_count, integrity, timeout):
    case = {"chapters": count, "manuscript_characters": count * 2500, "cards": card_count,
            "integrity_mode": integrity, "operations": {}, "ok": False}
    book = story.Book(root, integrity=integrity)
    draft = root / "load-draft.md"
    try:
        case["cli_status"] = cli_status(root, integrity, timeout)
        if not case["cli_status"]["ok"]:
            raise AssertionError("Actual CLI --integrity status failed")

        def run(label, operation):
            record = case["operations"].setdefault(label, {})
            value = measure(book, story, operation, record)
            print(json.dumps({"chapters": count, "cards": card_count, "integrity": integrity,
                              "operation": label, "seconds": record["elapsed_seconds"],
                              "manuscript_reads": record["manuscript_file_reads"]}), flush=True)
            return value

        run("status", book.status)
        context = run("context", lambda: book.context(count + 1))
        if not any(card["id"] == "c00000" for card in context["required_cards"]):
            raise AssertionError("Explicit required card disappeared from context")
        case["context_output_bytes"] = context["budget"]["used"]
        run("prepare", lambda: book.prepare(count + 1, draft))
        recalled = run("recall_exact_id", lambda: book.recall("c00000"))
        if not any(item.get("id") == "c00000" for item in recalled["matches"]):
            raise AssertionError("Exact card ID was not recalled")
        chinese = run("chinese_substring_search", lambda: story.search.query(book.db, "江棠"))
        if not any(item["key"] == "c00000" for item in chinese["matches"]):
            raise AssertionError("Two-character Chinese name was not recalled")
        exported = run("export_no_changes", book.export)
        if not exported.get("scope_exports_complete", exported.get("exports_complete")):
            raise AssertionError("Scoped unchanged export failed")
        delta = {"book_id": context["book_id"], "base_revision": context["revision"],
                 "summary": "Synthetic capacity fixture; not a reviewed novel.", "changes": [],
                 "review": {"draft_sha256": story.digest(draft.read_text(encoding="utf-8")),
                            "checks": {key: {"note": "Synthetic field fixture, no literary claim.", "quote": "甲"}
                                       for key in story.CHECKS}, "issues": []}}
        committed = run("commit_one_next_chapter", lambda: book.commit(count + 1, draft, delta))
        if not committed.get("committed") or not committed.get("scope_exports_complete", committed.get("exports_complete")):
            raise AssertionError("Synthetic commit or its scoped exports failed")
        if file_sha(Path(committed["path"])) != delta["review"]["draft_sha256"]:
            raise AssertionError("Committed chapter bytes do not match the submitted fixture")

        critical_ids = [f"scale-critical-{number}" for number in range(100)]
        try:
            with book.transaction():
                for cid in critical_ids:
                    book.put_card(story.valid_card({"id": cid, "kind": "contract",
                        "text": "不能悄悄丢弃的硬约束。" * 10, "source": "synthetic", "critical": True,
                        "scope": "global"}))
            overload = {}
            try:
                measure(book, story, lambda: book.context(count + 1), overload)
            except story.StoryError as error:
                if error.code != "budget_exceeded":
                    raise
                case["required_card_overload"] = {"expected_error_observed": True, "error": error.code,
                                                  "details": error.details, "measurement": overload}
            else:
                raise AssertionError("100 global critical cards were silently cut")
        finally:
            with book.transaction():
                for cid in critical_ids:
                    book.delete_card(cid)
            remaining = book.db.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
            indexed = book.db.execute("SELECT COUNT(*) FROM search_documents WHERE kind='card'").fetchone()[0]
            case["overload_cleanup"] = {"cards": remaining, "indexed_cards": indexed,
                                         "ok": remaining == card_count and indexed == card_count}
        if not case["overload_cleanup"]["ok"]:
            raise AssertionError("Required-card overflow fixture cleanup was incomplete")
        case["ok"] = True
    except (Exception, KeyboardInterrupt) as error:
        case["error"] = error_details(error)
    finally:
        book.close()
    return case


def probe(chapters=(400, 4000), cards=None, integrities=("strict", "local"), timeout=300):
    snapshot = runtime_snapshot()
    baseline_sha = file_sha(BASELINE) if BASELINE.exists() else None
    story = load_module("story_scale_probe", TOOL)
    report = {"schema": 3, "date": datetime.now(timezone.utc).isoformat(), "ok": False,
              "runtime_version": story.VERSION, "runtime_sha256": snapshot[TOOL.name],
              "runtime_files": snapshot, "python_version": platform.python_version(),
              "sqlite_version": sqlite3.sqlite_version, "platform": platform.platform(),
              "baseline": {"path": "benchmarks/results/scaling.json", "sha256": baseline_sha},
              "method": "Synthetic repeated 2500-character chapters and unrelated cards. Direct state/view and index fixture inserts, one measured warm run per operation, size and integrity mode. Each mode starts from a fresh copy of the same fixture. Not a complete event history, cold-disk benchmark, real ten-million-character novel, model test or literary-quality validation.",
              "measurement_scope": {"reads": "Path.read_bytes application reads, not physical disk IO or total application reads; descriptor-bound export/backup reads and database-native reads are not included",
                                    "memory": "tracemalloc peak Python allocations during each operation; excludes SQLite native allocations, OS file cache and RSS",
                                    "sql": "SQLite trace callbacks, including repeated trigger callbacks; not VM step counts or decoded row counts",
                                    "decodes": "actual json.loads calls and input bytes; decoded_card_objects counts top-level card-shaped objects",
                                    "search": "actual search.query counters, including candidate/text bounds and completeness",
                                    "timing": "Single warm runs with instrumentation overhead and possible CPU contention from concurrent local tests. Fixture setup, cloning, CLI corroboration and report serialization are excluded from operation timings."},
              "fixtures": [], "cases": []}
    with tempfile.TemporaryDirectory(prefix="story-scale-v03-") as name:
        base = Path(name).resolve()
        for index, count in enumerate(chapters):
            card_count = cards[index] if cards else (2000 if count <= 400 else 20000)
            fixture = base / f"fixture-{index}"
            try:
                info = make_fixture(story, fixture, count, card_count)
                report["fixtures"].append({"chapters": count, "cards": card_count, **info})
                for integrity in integrities:
                    target = base / f"case-{index}-{integrity}"
                    shutil.copytree(fixture, target)
                    report["cases"].append(run_case(story, target, count, card_count, integrity, timeout))
            except (Exception, KeyboardInterrupt) as error:
                report["cases"].append({"chapters": count, "cards": card_count, "ok": False,
                                        "stage": "fixture_or_case_setup", "error": error_details(error)})
                break
    report["temporary_data_removed"] = not base.exists()
    report["runtime_stable"] = runtime_snapshot() == snapshot
    report["baseline_unchanged"] = (file_sha(BASELINE) if BASELINE.exists() else None) == baseline_sha
    report["ok"] = (len(report["cases"]) == len(chapters) * len(integrities)
                    and all(case["ok"] for case in report["cases"]) and report["runtime_stable"]
                    and report["baseline_unchanged"] and report["temporary_data_removed"])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapters", type=int, nargs="+", default=[400, 4000],
                        help="Chapter sizes; --chapters 10000 enables the larger synthetic fixture")
    parser.add_argument("--cards", type=int, nargs="+", help="One count per chapter size; defaults to 2000/20000")
    parser.add_argument("--integrity", choices=("strict", "local"), nargs="+", default=["strict", "local"])
    parser.add_argument("--timeout", type=int, default=300, help="Seconds allowed for each CLI corroboration")
    parser.add_argument("--output", type=Path,
                        help="Defaults to benchmarks/results/v<runtime VERSION>/scaling.json")
    args = parser.parse_args()
    if any(count < 1 for count in args.chapters) or args.timeout < 1:
        parser.error("chapter counts and timeout must be positive")
    if args.cards and (len(args.cards) != len(args.chapters) or any(count < 1 for count in args.cards)):
        parser.error("--cards requires one positive count per --chapters entry")
    if len(set(args.integrity)) != len(args.integrity):
        parser.error("integrity modes must not be repeated")
    writer = load_module("scale_report_writer", ROOT / "scripts/verify.py")
    output = writer.report_path(args.output if args.output is not None else
                                writer.current_evidence_directory() / "scaling.json")
    if output == BASELINE.resolve():
        parser.error("The v0.2 scaling.json baseline is immutable; select another --output")
    try:
        report = probe(args.chapters, args.cards, args.integrity, args.timeout)
    except (Exception, KeyboardInterrupt) as error:
        report = {"schema": 3, "ok": False, "date": datetime.now(timezone.utc).isoformat(),
                  "error": error_details(error), "stage": "probe_initialization"}
    output = writer.write_report(report, output)
    print(json.dumps({"ok": report["ok"], "report": str(output), "cases": len(report.get("cases", []))},
                     ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
