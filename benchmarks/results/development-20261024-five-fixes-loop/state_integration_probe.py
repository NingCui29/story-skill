"""Offline journal transitions for browser rejections; no platform operation."""
from pathlib import Path
import hashlib
import json
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from tests.test_platform_drafts import FakeAdapter, drafts, fixtures, story


def scenario(platform, mode):
    fixture = fixtures.PublishTests()
    fixture.setUp()
    try:
        fixture.commit()
        plan = fixture.prepare(platform=platform)
        frozen = plan["manifest"]["chapters"][0]
        adapter = FakeAdapter(fixture.book)
        if mode in ("existing_read_rejected", "wrong_read_id", "wrong_read_body"):
            adapter.seed(frozen)
        baseline = fixture.writing_snapshot()
        state = fixture.root.parent / "target-state"
        state.mkdir(mode=0o700)
        original_read = adapter.read_draft
        remaining = [1]

        def read(remote_id):
            value = original_read(remote_id)
            if remaining[0]:
                remaining[0] -= 1
                if mode in ("existing_read_rejected", "created_read_rejected"):
                    story.fail("platform_browser_identity_mismatch", "Synthetic changed editor")
                if mode == "wrong_read_id":
                    value["remote_id"] = "another-draft"
                if mode == "wrong_read_body":
                    value["body"] += "不属于冻结稿的文字。"
            return value

        adapter.read_draft = read
        if mode == "create_result_lost":
            adapter.fail_create = "after"
        elif mode == "create_rejected_before_remote":
            adapter.fail_create = "before"
        with patch.object(drafts, "_lock_directory", return_value=state):
            first = drafts.run_plan(fixture.book, plan["id"], adapter, execute=True)
            assert not first["ok"] and not first["accepted_chapters"], (platform, mode, first)
            expected = "conflict" if mode == "wrong_read_body" else (
                "blocked" if mode in ("existing_read_rejected", "wrong_read_id") else "unknown")
            assert first["results"][0]["state"] == expected
            adapter.fail_create = None
            second = drafts.run_plan(fixture.book, plan["id"], adapter, execute=True)
            if mode == "create_rejected_before_remote":
                assert not second["ok"] and second["results"][0]["state"] == "unknown"
            else:
                assert second["ok"] and second["results"][0]["state"] == "reconciled", second
            expected_creates = 0 if mode in ("existing_read_rejected", "wrong_read_id", "wrong_read_body") else 1
            assert adapter.created == expected_creates
            historical = drafts.status(fixture.book, plan["id"])
            assert not historical["all_chapters_accepted_this_run"]
            assert fixture.writing_snapshot() == baseline
        return {"platform": platform, "case": mode, "first": expected,
                "second": second["results"][0]["state"], "creates": adapter.created,
                "creative_state_unchanged": True, "ok": True}
    finally:
        fixture.tearDown()


if __name__ == "__main__":
    rows = [scenario(platform, mode) for platform in ("fanqie", "qimao", "qidian")
            for mode in ("existing_read_rejected", "created_read_rejected", "wrong_read_id",
                         "wrong_read_body", "create_result_lost", "create_rejected_before_remote")]
    paths = ["skills/story-skill/scripts/story_platform_drafts.py", "skills/story-skill/scripts/story_publish.py"]
    print(json.dumps({"ok": True, "cases": len(rows), "results": rows,
                      "scope": "offline injected adapter at the durable journal boundary",
                      "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}},
                     ensure_ascii=False, indent=2))
