#!/usr/bin/env python3
"""Exercise the documented CLI in a temporary Unicode book directory, without a model."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"


def smoke():
    commands = []
    with tempfile.TemporaryDirectory(prefix="story-cli-smoke-") as temp:
        root = Path(temp) / "示例书"
        def call(command, *args):
            result = subprocess.run([sys.executable, str(TOOL), command, "--book", str(root), *map(str, args)],
                                    capture_output=True, check=False)
            if result.returncode:
                raise RuntimeError(result.stderr.decode("utf-8") or result.stdout.decode("utf-8"))
            commands.append(command)
            return json.loads(result.stdout)
        def write_json(name, value):
            file = root / name
            file.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            return file
        call("init", "--title", "门后的雨", "--kind", "short")
        notes = [{"id": "hero", "kind": "character", "text": "沈禾手中只有一把钥匙。", "source": "示例初始设定", "tags": ["沈禾"]}]
        call("notes", "--input", write_json("notes.json", notes), "--expect", 0)
        plan = {"volume_dir": "第一卷 雨夜", "goal": "让沈禾用唯一钥匙换取线索", "stop": "进门，不揭开失踪者身份",
                "constraints": ["保留拿不到账本的停笔点"], "requires": ["hero"], "tags": ["沈禾"],
                "length": [150, 350], "beats": [{"choice": "交出唯一钥匙", "change": "进门但失去退路"}]}
        call("plan", "--chapter", 1, "--input", write_json("plan.json", plan), "--expect", 1)
        packet = call("context", "--chapter", 1, "--budget-bytes", 4000)
        draft_text = ("# 第一章 门后的雨\n沈禾把钥匙放在门槛上，没有往前推。\n"
                      "守门人看了一眼她身后的雨，说这把钥匙只能换一次进门。出来以后，锁就会换掉。\n"
                      "她问账本是不是还在楼上。那人伸出手，没有回答。\n"
                      "钥匙是弟弟离家前留给她的。她攥了三个月，边缘已经磨得发亮，原先刻在背面的两个字只剩一横。\n"
                      "沈禾把唯一的钥匙交给守门人。\n门往里开了一道缝，里面没有灯。她侧身进去，先闻到湿木头的味道，又听见二楼有人挪动椅子。\n"
                      "她没有叫弟弟的名字。\n守门人在身后关门，钥匙落进铁盒，发出很轻的一声响。\n")
        draft = root / "draft.md"
        draft.write_bytes(draft_text.encode("utf-8"))
        lint = call("lint", "--chapter", 1, "--draft", draft)
        quote = "沈禾把唯一的钥匙交给守门人。"
        delta = {"book_id": packet["book_id"], "base_revision": packet["revision"], "summary": "沈禾交出唯一钥匙进门，还没有找到账本。",
                 "changes": [{"id": "hero", "text": "钥匙已交出，沈禾进入门内，账本尚未取得。", "quote": quote}],
                 "review": {"draft_sha256": lint["draft_sha256"], "checks": {
                     "causality": {"note": "以唯一钥匙换入门，代价在场。", "quote": quote},
                     "continuity": {"note": "只使用既有钥匙，未突然获得新资源。", "quote": quote},
                     "constraints": {"note": "仅进门，未揭晓楼上的人，也未拿到账本。", "quote": "她没有叫弟弟的名字。"},
                     "style": {"note": "细节与关系线相连，结尾铁盒声呼应丢失退路。", "quote": "钥匙落进铁盒，发出很轻的一声响。"}}, "issues": []}}
        delta_file = write_json("delta.json", delta)
        committed = call("commit", "--chapter", 1, "--draft", draft, "--input", delta_file)
        retry = call("commit", "--chapter", 1, "--draft", draft, "--input", delta_file)
        if not committed["exports_complete"] or not retry["idempotent"]:
            raise AssertionError("Commit/export/idempotency contract failed")
        if Path(committed["path"]).read_bytes() != draft.read_bytes():
            raise AssertionError("Export differs from reviewed draft")
        export_path = Path(committed["path"])
        external_bytes = (draft_text + "沈禾在门内站稳，等楼上的脚步停下。\n").encode("utf-8")
        export_path.write_bytes(external_bytes)
        inspected = call("reconcile", "--chapter", 1)
        if inspected["external_edit"]["sha256"] != hashlib.sha256(external_bytes).hexdigest():
            raise AssertionError("Reconciliation did not identify the outside draft")
        revised_text = external_bytes.decode("utf-8") + "她将空手藏进衣袖。\n"
        draft.write_bytes(revised_text.encode("utf-8"))
        delta.update(base_revision=inspected["revision"], external_sha256=inspected["external_edit"]["sha256"])
        delta["review"]["draft_sha256"] = call("lint", "--chapter", 1, "--draft", draft)["draft_sha256"]
        delta_file = write_json("reconciled-delta.json", delta)
        reconciled = call("reconcile", "--chapter", 1, "--draft", draft, "--input", delta_file)
        reconcile_retry = call("reconcile", "--chapter", 1, "--draft", draft, "--input", delta_file)
        if (not reconciled["exports_complete"] or not reconcile_retry["idempotent"]
                or export_path.read_bytes() != draft.read_bytes()
                or not any(Path(path).read_bytes() == external_bytes for path in reconciled["backups"])):
            raise AssertionError("Reconciliation/export/backup contract failed")
        corpus = root / "source.txt"
        source_text = "\n\n" + draft_text + "番外：铁盒\n守门人把旧钥匙放在铁盒最底层。\n"
        corpus.write_bytes(source_text.encode("utf-8"))
        ingested = call("ingest", "--file", corpus, "--coverage", "partial")
        source = ingested["source"]
        if ingested["analyzed"] != 1:
            raise AssertionError("Leading non-content chunk was not automatically completed")
        while True:
            chunks = call("next", "--source", source)["chunks"]
            if not chunks:
                break
            for chunk in chunks:
                evidence = chunk["text"].strip().splitlines()[-1]
                analysis = {"chunk_sha256": chunk["sha"], "summary": "门与钥匙的行动有可核对的文本依据。",
                            "findings": [{"kind": "行动", "claim": "文本给出一个具体动作，连接场景的前后状态。", "quote": evidence}]}
                call("record", "--source", source, "--chunk", chunk["ordinal"],
                     "--input", write_json("analysis.json", analysis))
        coverage = call("coverage", "--source", source)
        findings = call("findings", "--source", source)
        report = root / "analysis-report.md"
        report.write_text("这是合成样本的程序通路验证。正文用唯一钥匙交换入门资格，末尾铁盒声延续资源转移的结果；番外保留钥匙的去向。本报告只覆盖导入片段。", encoding="utf-8")
        final = call("report", "--source", source, "--file", report,
                     "--expect-analysis", findings["analysis_sha256"])
        if final["report_path"] is None:
            raise AssertionError("Final report receipt lost its saved checkpoint path")
        if "source_coverage: partial" not in Path(final["report"]).read_text(encoding="utf-8"):
            raise AssertionError("Partial source mislabeled")
        coverage = call("coverage", "--source", source)
        return {"ok": True, "method": "Synthetic fixture via real CLI subprocesses; no model or API calls",
                "commands_executed": commands, "visible_chars": lint["visible_chars"],
                "draft_sha256": lint["draft_sha256"],
                "reconciled_draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
                "context_bytes": packet["budget"]["used"], "context_budget_bytes": 4000,
                "chapter_commit_idempotent": retry["idempotent"], "export_bytes_verified": True,
                "reconcile_idempotent": reconcile_retry["idempotent"], "external_backup_verified": True,
                "non_content_chunks_completed": ingested["analyzed"],
                "analysis_coverage": coverage, "finding_pages_returned": len(findings["results"]),
                "final_report_exported": final["exports_complete"], "literary_quality_tested": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default=str(ROOT / "benchmarks/results/v0.5.0/smoke.json"))
    args = p.parse_args()
    result = smoke()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
