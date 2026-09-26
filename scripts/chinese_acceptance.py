#!/usr/bin/env python3
"""Exercise Chinese CLI transactions with synthetic inputs or explicit local manuscripts."""
import argparse
from contextlib import nullcontext
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def fixture(base, relative):
    path = (base / relative).resolve()
    path.relative_to(base.resolve())
    return path


def replay(root, scenario_path, resume_prepared_first=False):
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
    base = scenario_path.parent
    book = root / scenario["title"]
    calls, chapters = [], []
    chapter_paths = {}

    def call(command, *args, expected=0, target=None):
        proc = subprocess.run([sys.executable, "-B", "-X", "utf8", str(TOOL), command,
                               "--book", str(target or book), *map(str, args)], capture_output=True, timeout=45)
        if proc.returncode != expected:
            raise RuntimeError(f"{command}: {proc.returncode}: {(proc.stderr or proc.stdout).decode('utf-8')}")
        result = json.loads(proc.stdout or proc.stderr)
        calls.append({"command": command, "exit_code": proc.returncode})
        return result

    if resume_prepared_first:
        # A narrowly scoped recovery of the actual exFAT first-export failure.
        # Reuse the saved reviewed delta; never regenerate its revision or reset state.
        state = call("status")
        if state["last_chapter"] != 1 or state["title"] != scenario["title"]:
            raise ValueError("Expected only the first native chapter at this recovery checkpoint")
        saved_notes = json.loads((book / ".story/drafts/notes.json").read_text(encoding="utf-8"))
        if saved_notes != scenario["notes"]:
            raise ValueError("Initial scenario changed; do not replay it onto existing state")
    else:
        call("init", "--title", scenario["title"], "--kind", scenario["kind"])
        for name in ("创作约定.md", "设定.md", "大纲.md"):
            source = base / name
            if source.is_file():
                (book / name).write_bytes(source.read_bytes())
        call("notes", "--input", write_json(book / ".story/drafts/notes.json", scenario["notes"]), "--expect", 0)
        for number, plan in enumerate(scenario["plans"], 1):
            plan = {"volume_dir": "第一卷 " + scenario["title"], **plan}
            revision = call("status")["revision"]
            call("plan", "--chapter", number, "--input", write_json(book / f".story/drafts/plan-{number}.json", plan),
                 "--expect", revision)
    recovery = None
    for index, unit in enumerate(scenario["units"], 1):
        number = unit["chapter"]
        raw = fixture(base, unit["draft"]).read_bytes()
        if sha(raw) != unit["reviewed_sha256"]:
            raise AssertionError(f"Manuscript changed after semantic review: {unit['draft']}")
        draft = book / f".story/drafts/{index:02d}.md"
        retry_prepared = resume_prepared_first and index == 1
        if retry_prepared:
            if draft.read_bytes() != raw:
                raise ValueError("Saved recovery draft differs from the reviewed manuscript")
        else:
            draft.write_bytes(raw)
        if unit.get("plan"):
            call("plan", "--chapter", number, "--input", write_json(book / f".story/drafts/plan-{index}-revision.json",
                 {"volume_dir": "第一卷 " + scenario["title"], **unit["plan"]}),
                 "--expect", call("status")["revision"])
        if unit.get("external_recovery"):
            # Only files created by this run, under its new isolated book, are modified.
            first = chapter_paths[1]
            original_first = first.read_bytes()
            last = chapter_paths[number]
            last.write_bytes(raw)
            first.unlink()
            blocked = call("reconcile", "--chapter", number, expected=2)
            assert blocked["error"] == "exports_unresolved"
            blocked_export = call("export", expected=2)
            assert blocked_export["error"] == "export_conflict"
            recovered = call("export", "--safe-only", expected=2)
            assert not recovered["exports_complete"] and first.read_bytes() == original_first
            assert last.read_bytes() == raw
            recovery = {"missing_chapter_restored": True, "outside_draft_preserved": True,
                        "remaining_changes": recovered["changed_exports"]}
        if retry_prepared:
            packet = json.loads((book / ".story/drafts/01-context.json").read_text(encoding="utf-8"))
            data = book / ".story/drafts/01-delta.json"
            delta = json.loads(data.read_text(encoding="utf-8"))
            if (delta["book_id"] != state["id"] or delta["base_revision"] + 1 != state["revision"]
                    or delta["changes"] != unit["changes"] or delta["summary"] != unit["summary"]
                    or delta["review"]["checks"] != unit["checks"]):
                raise ValueError("Saved delta is not this first reviewed transaction")
            prepared = {"lint": call("lint", "--chapter", number, "--draft", draft)}
        else:
            context_cmd = "reconcile" if unit.get("external_recovery") else "context"
            packet = call(context_cmd, "--chapter", number, "--budget-bytes", 16000)
            write_json(book / f".story/drafts/{index:02d}-context.json", packet)
            extra = ["--reconcile"] if unit.get("external_recovery") else []
            prepared = call("prepare", "--chapter", number, "--draft", draft, *extra)
            assert not prepared["ready_to_commit"] and prepared["lint"]["ok"]
            delta = prepared["delta"]
            delta.update(summary=unit["summary"], changes=unit["changes"])
            delta["review"].update(checks=unit["checks"], issues=unit.get("issues", []))
            data = write_json(book / f".story/drafts/{index:02d}-delta.json", delta)
        command = "reconcile" if unit.get("external_recovery") else "commit"
        arguments = ["--chapter", number, "--draft", draft, "--input", data]
        if unit.get("replace_last") and not unit.get("external_recovery"):
            arguments += ["--replace-last"]
        saved = call(command, *arguments)
        repeated = call(command, *arguments)
        assert saved["exports_complete"] and repeated["idempotent"]
        chapter_paths[number] = Path(saved["path"])
        assert chapter_paths[number].read_bytes() == raw
        for cid, expected_text in unit.get("expected_cards", {}).items():
            recalled = call("recall", "--query", cid)
            card = next(c for c in recalled["matches"] if c.get("type") == "card" and c["id"] == cid)
            assert card["text"] == expected_text, cid
        chapters.append({"draft": unit["draft"], "chapter": number, "mode": packet["mode"],
                         "sha256": sha(raw), "lint": prepared["lint"], "context_bytes": packet["budget"]["used"],
                         "revision": saved["revision"], "idempotent_retry": repeated["idempotent"],
                         "resumed_committed_export": retry_prepared})
    final = call("status")
    assert final["pending_export_count"] == final["changed_export_count"] == 0
    checkpoint = book / "验收断点.md"
    checkpoint.write_text(f"# 实测断点\n\n已导出 {final['last_chapter']} 个单元，状态版本 {final['revision']}。"
                          "原始创作约定保留。继续前先读创作约定与 status；本次验收不授权自动扩写示例。\n", encoding="utf-8")
    write_json(book / "验收回执.json", {"chapters": chapters, "status": final, "recovery": recovery})
    analysis = None
    if scenario.get("analysis"):
        spec = scenario["analysis"]
        separate = root / (scenario["title"] + "_拆文")
        def ac(command, *args, **kwargs):
            return call(command, *args, target=separate, **kwargs)
        source = fixture(base, spec["source"])
        assert sha(source.read_bytes()) == spec["reviewed_sha256"]
        ac("init", "--title", scenario["title"] + "拆文", "--kind", "analysis")
        ingested = ac("ingest", "--file", source, "--coverage", spec["coverage"], "--chunk-chars", 10000)
        sid = ingested["source"]
        resumed_ordinals = []
        for expected in spec["chunks"]:
            # A separate CLI process resumes the persisted checkpoint after every record.
            chunk = ac("next", "--source", sid, "--limit", 1)["chunks"][0]
            assert chunk["ordinal"] == expected["ordinal"]
            resumed_ordinals.append(chunk["ordinal"])
            payload = {"chunk_sha256": chunk["sha"], "summary": expected["summary"], "findings": expected["findings"]}
            ac("record", "--source", sid, "--chunk", chunk["ordinal"], "--input",
               write_json(separate / f"chunk-{chunk['ordinal']}.json", payload))
        assert not ac("next", "--source", sid)["chunks"]
        # This fixture's report was reviewed against the recorded analyses above.
        analysis_baseline = ac("findings", "--source", sid)["analysis_sha256"]
        reviewed_report = fixture(base, spec["report"])
        saved_report = ac("report", "--source", sid, "--file", reviewed_report,
                          "--expect-analysis", analysis_baseline)
        coverage = ac("coverage", "--source", sid)
        assert saved_report["exports_complete"] and coverage["complete_for_imported_text"]
        analysis = {"coverage": coverage, "resumed_ordinals": resumed_ordinals,
                    "source_sha256": sid, "report_path": saved_report["report"],
                    "report_sha256": sha(Path(saved_report["report"]).read_bytes())}
    return {"title": scenario["title"], "kind": scenario["kind"], "book": str(book), "chapters": chapters,
            "recovery": recovery, "analysis": analysis, "status": final, "commands": calls,
            "scenario_sha256": sha(scenario_path.read_bytes())}


def generated_scenarios(base):
    """Generate tiny technical fixtures, not novels or literary review evidence."""
    paths = []
    for kind in ("long", "short"):
        folder = base / kind
        folder.mkdir(parents=True)
        plans, units = [], []
        for number in range(1, 4 if kind == "long" else 2):
            quote = f"测试员将编号{number}的蓝色方块放进盒子。"
            text = f"第{number}章 测试单元\n\n{quote}\n\n盒盖已经合上，本次操作结束。\n"
            draft = folder / f"unit-{number}.md"
            draft.write_bytes(text.encode("utf-8"))
            plans.append({"goal": "放入方块", "stop": "盒盖合上", "requires": ["operator"],
                          "length": [10, 200], "beats": [{"choice": "放入方块", "change": "盒盖合上"}]})
            units.append({"chapter": number, "draft": draft.name, "reviewed_sha256": sha(draft.read_bytes()),
                          "summary": f"编号{number}方块已经入盒。",
                          "changes": [{"id": "operator", "text": f"已放入编号{number}方块。", "quote": quote}],
                          "checks": {key: {"note": "合成事务夹具，仅核对动作与停止状态，不评价文学质量。", "quote": quote}
                                     for key in ("causality", "continuity", "constraints", "style")},
                          "expected_cards": {"operator": f"已放入编号{number}方块。"}})
        if kind == "long":
            # Preserve two revisions, external-edit recovery, and idempotent retries.
            for index, color in enumerate(("红色", "绿色"), 1):
                revised = copy.deepcopy(units[2])
                previous = (folder / revised["draft"]).read_text(encoding="utf-8")
                draft = folder / f"revision-{index}.md"
                draft.write_bytes(previous.replace("蓝色", color).encode("utf-8"))
                revised.update(draft=draft.name, reviewed_sha256=sha(draft.read_bytes()), replace_last=True)
                for check in revised["checks"].values():
                    check["quote"] = check["quote"].replace("蓝色", color)
                revised["changes"][0]["quote"] = revised["changes"][0]["quote"].replace("蓝色", color)
                if index == 2:
                    revised["external_recovery"] = True
                units.append(revised)
        scenario = {"title": "合成中文事务" + kind, "kind": kind,
                    "notes": [{"id": "operator", "kind": "character", "text": "测试员准备放入方块。", "source": "合成输入"}],
                    "plans": plans, "units": units}
        if kind == "short":
            source = folder / "source.txt"
            source.write_bytes("第1章 输入甲\n测试员放入方块。\n\n第2章 输入乙\n测试员关闭盒盖。\n".encode("utf-8"))
            report = folder / "report.md"
            report.write_bytes("# 合成拆文报告\n\n先放入方块，再关闭盒盖。两个输入单元分别保存精确引文，逐块续跑后生成报告。仅验证分块恢复与报告保存，不作文学评价。\n".encode("utf-8"))
            scenario["analysis"] = {"source": source.name, "reviewed_sha256": sha(source.read_bytes()),
                                    "coverage": "complete", "report": report.name,
                                    "chunks": [{"ordinal": i, "summary": q,
                                                "findings": [{"kind": "动作", "claim": q, "quote": q}]}
                                               for i, q in enumerate(("测试员放入方块。", "测试员关闭盒盖。"), 1)]}
        paths.append(write_json(folder / "scenario.json", scenario))
    return paths


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workdir", help="New empty directory to retain actual book state; otherwise temporary")
    p.add_argument("--scenario-dir", help="Explicit local reviewed manuscripts; default uses temporary synthetic fixtures")
    p.add_argument("--output", default=str(ROOT / "dist/ci-chinese.json"))
    args = p.parse_args()
    ctx = nullcontext(args.workdir) if args.workdir else tempfile.TemporaryDirectory(prefix="story-chinese-")
    with ctx as name:
        root = Path(name).resolve()
        if root.exists() and any(root.iterdir()):
            raise ValueError("Use a new empty workdir; existing manuscripts and state are never replaced")
        root.mkdir(parents=True, exist_ok=True)
        scenarios = (sorted(Path(args.scenario_dir).glob("*/scenario.json")) if args.scenario_dir
                     else generated_scenarios(root / "_synthetic-inputs"))
        if len(scenarios) < 2:
            raise ValueError("Both long and complete short manuscript scenarios are required")
        books = [replay(root, path) for path in scenarios]
        result = {"ok": True, "method": ("Explicit local fixed manuscripts replayed through CLI; no new model evaluation" if args.scenario_dir
                  else "Generated minimal Chinese transaction fixtures through real CLI; not novels or literary quality evidence"),
                  "mode": "local-manuscripts" if args.scenario_dir else "synthetic-transactions",
                  "retained_workdir": str(root) if args.workdir else None,
                  "runtime_sha256": sha(TOOL.read_bytes()), "books": books}
        write_json(Path(args.output), result)
        print(json.dumps({"ok": True, "books": [b["title"] for b in books], "retained_workdir": result["retained_workdir"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
