#!/usr/bin/env python3
"""Verify schema1 migration and rollback using generated fixtures or an explicit retained source."""
import argparse
from contextlib import closing
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

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"
SCHEMA1_RUNTIME = ROOT / "tests/fixtures/schema1_runtime.py"
SCHEMA1_RUNTIME_SHA256 = "70c8a0294d72103cb2232834ac951f30abef6ab20e940cb0303aca1858910cb3"
RETAINED_NAMES = ("渡口夜账", "留半寸", "留半寸_拆文")
GENERATED_NAMES = ("合成迁移长篇", "合成迁移短篇", "合成迁移拆文")


def load_runtime(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


story = load_runtime("migration_probe_story", TOOL)


def inventory(root):
    if not root.is_dir() or root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Missing or linked fixture source")
    result = {}
    for path in root.rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Linked fixture path")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def legacy_runtime(path):
    raw = SCHEMA1_RUNTIME.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA1_RUNTIME_SHA256:
        raise ValueError("Schema 1 runtime fixture differs from its retained SHA-256")
    path.write_bytes(raw)
    return load_runtime("migration_probe_legacy", path)


def generated_fixtures(source, old):
    """Small synthetic Chinese inputs exercise native commit, adopt, and analysis."""
    for name, kind in zip(GENERATED_NAMES, ("long", "short", "analysis")):
        root = source / name
        old.Book.create(root, name, kind)
        book = old.Book(root)
        try:
            text = "# 第1章 交接\n江棠把旧钥匙交给杜承安。天亮前，他须带回收条。\n"
            draft = root / "输入样文.md"
            draft.write_bytes(text.encode("utf-8"))
            if kind == "long":
                plan = {"goal": "交付钥匙并约定收条", "stop": "等候收条", "length": [10, 100],
                        "beats": [{"choice": "交出钥匙", "change": "承担归还责任"}], "requires": []}
                book.save_plan(1, plan, book.meta("revision"))
                delta = {"book_id": book.meta("id"), "base_revision": book.meta("revision"),
                         "summary": "江棠交钥匙，杜承安须带回收条。", "changes": [],
                         "review": {"draft_sha256": old.digest(text), "issues": [], "checks": {
                             key: {"note": "合成迁移夹具：核对交付动作与等待断点。",
                                   "quote": "江棠把旧钥匙交给杜承安。"} for key in old.CHECKS}}}
                book.commit(1, draft, delta)
            elif kind == "short":
                book.adopt(1, draft, "合成导入基线：钥匙交付后等待收条。", book.meta("revision"))
            else:
                sid = book.ingest(draft)["source"]
                chunk = book.next_chunks(sid)["chunks"][0]
                book.record(sid, chunk["ordinal"], {"chunk_sha256": chunk["sha"],
                            "summary": "钥匙交付与期限约定。", "findings": [{"kind": "动作",
                            "claim": "江棠主动交付钥匙。", "quote": "江棠把旧钥匙交给杜承安。"}]})
        finally:
            book.close()


def snapshot(db):
    return {"meta": dict(db.execute("SELECT key,value FROM meta")),
            "chapters": list(map(tuple, db.execute("SELECT chapter,sha,summary FROM chapters ORDER BY chapter"))),
            "sources": list(map(tuple, db.execute("SELECT * FROM sources ORDER BY id"))),
            "chunks": list(map(tuple, db.execute("SELECT * FROM chunks ORDER BY source,ordinal")))}


def probe(source=None, timeout=60):
    runtime = lambda: {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in TOOL.parent.glob("*.py")}
    generated = source is None
    evidence = {"schema": 1, "version": story.VERSION, "date": datetime.now(timezone.utc).isoformat(), "ok": False,
                "mode": "generated-schema1-fixtures" if generated else "retained-source",
                "scope": ("Synthetic Chinese schema1 fixtures created by the fixed v0.2.0 runtime; not retained historical book databases"
                          if generated else "Explicit retained schema1 books copied to isolated TEMP; source books unchanged"),
                "environment": {"platform": platform.platform(), "python": platform.python_version()},
                "schema1_runtime_fixture": {"path": str(SCHEMA1_RUNTIME),
                                            "sha256": SCHEMA1_RUNTIME_SHA256},
                "runtime": runtime(), "books": []}
    try:
        with tempfile.TemporaryDirectory(prefix="story-migration-probe-") as directory:
            temp = Path(directory).resolve()
            old_path = temp / "old.py"
            old = legacy_runtime(old_path)
            if generated:
                source = temp / "generated-source"
                generated_fixtures(source, old)
            else:
                source = Path(source).expanduser().absolute()
            names = GENERATED_NAMES if generated else RETAINED_NAMES
            initial = inventory(source)
            evidence["source_inventory_sha256"] = hashlib.sha256(
                json.dumps(initial, sort_keys=True).encode("utf-8")).hexdigest()
            for name in names:
                origin = source / name
                database = origin / ".story/state.sqlite3"
                if not database.is_file():
                    raise ValueError(f"Source database unavailable: {database}; no historical migration claimed")
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                    before = snapshot(db)
                if json.loads(before["meta"].get("schema", "null")) != 1:
                    raise ValueError("Migration source must use schema 1")
                target = temp / "migrated" / name
                shutil.copytree(origin, target)
                result = story.storage.migrate(story.CORE, target)
                book = story.Book(target)
                try:
                    after = snapshot(book.db)
                    if any(book.meta(k) != json.loads(v) for k, v in before["meta"].items() if k != "schema"):
                        raise ValueError("Identity/checkpoint changed during migration")
                    if any(after[key] != before[key] for key in ("chapters", "sources", "chunks")):
                        raise ValueError("Manuscript, summary, source or analysis changed")
                    status = book.status()
                    if status["pending_export_count"] or status["changed_export_count"]:
                        raise ValueError("Migrated exports differ from the source fixture")
                    book.export()
                    rollback = temp / "rollback" / name
                    shutil.copytree(origin, rollback)
                    shutil.copyfile(result["backup"], rollback / ".story/state.sqlite3")
                    process = subprocess.run([sys.executable, "-B", "-X", "utf8", str(old_path),
                                              "status", "--book", str(rollback)], capture_output=True, timeout=timeout)
                    if process.returncode:
                        raise ValueError(process.stderr.decode("utf-8", errors="replace"))
                    old_status = json.loads(process.stdout)
                    with closing(sqlite3.connect(rollback / ".story/state.sqlite3")) as db:
                        if snapshot(db) != before:
                            raise ValueError("Rollback copy changed legacy database content")
                    if any(old_status[key] != status[key] for key in ("id", "revision", "last_chapter", "sources")):
                        raise ValueError("Rollback copy lost identity/checkpoint")
                    evidence["books"].append({"title": name, "kind": book.meta("kind"), "book_id": status["id"],
                        "revision": status["revision"], "last_chapter": status["last_chapter"], "sources": status["sources"],
                        "migrated": result["migrated"], "original_manuscript_and_summary_preserved": True,
                        "source_and_analysis_preserved": True, "exports_clean": True,
                        "old_runtime_rollback_copy_verified": True})
                finally:
                    book.close()
            evidence["original_tree_unchanged"] = inventory(source) == initial
            evidence["runtime_stable"] = runtime() == evidence["runtime"]
            evidence["ok"] = evidence["original_tree_unchanged"] and evidence["runtime_stable"] and len(evidence["books"]) == 3
    except Exception as error:
        evidence["error"] = {"type": type(error).__name__, "message": str(error)}
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="Explicit retained schema1 source containing the three historical book directories; default generates synthetic fixtures")
    parser.add_argument("--output", default=str(ROOT / f"benchmarks/results/v{story.VERSION}/migration.json"))
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    verification = load_runtime("migration_probe_verify", ROOT / "scripts/verify.py")
    output = verification.report_path(args.output)
    result = probe(args.source, args.timeout)
    output = verification.write_report(result, output)
    print(story.dumps({"ok": result["ok"], "mode": result["mode"], "books": len(result["books"]),
                       "report": str(output), "error": result.get("error")}))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
