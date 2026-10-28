"""Independent local API checks: aborted initialization and recovery fencing."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from tests.test_platform_chrome_bridge import ChromeBridgeTests, bridge
from tests.test_platform_draft_retirement import DeletedDraftRetirementTests

rows = []

for exception in (KeyboardInterrupt, SystemExit):
    for mutation in ("before_handle", "after_handle", "session_replaced", "lease_replaced"):
        case = ChromeBridgeTests()
        case.setUp()
        try:
            binding = case.binding()
            other_id = "e" * 32
            other_binding = copy.deepcopy(binding)
            other_binding["target"]["remote_book_id"] = "8765"
            with bridge._directory(case.root, other_id, create=True) as folder, bridge._lock(folder):
                bridge._write(folder, "session.json", {"schema": 1, "session_id": other_id,
                    "binding": other_binding, "binding_sha256": bridge._digest(other_binding),
                    "token_sha256": "c" * 64, "ttl_seconds": 120})
                bridge._write(folder, "lease.json", {"schema": 1, "session_id": other_id,
                    "expires": time.time() + 120, "detached": False})
                other_files = {name: (folder.path / name).read_bytes() for name in ("session.json", "lease.json")}
                other_path = folder.path
            old_external = bridge._external
            facts = {}
            sentinel = exception("independent exact interruption")
            def external(path, *, create_value=None):
                if create_value is None:
                    return old_external(path)
                facts["sid"] = create_value["session_id"]
                if mutation != "before_handle":
                    old_external(path, create_value=create_value)
                    facts["handle"] = Path(path).read_bytes()
                with bridge._directory(case.root, facts["sid"]) as folder, bridge._lock(folder):
                    if mutation == "session_replaced":
                        value = bridge._read(folder, "session.json")
                        value["token_sha256"] = "f" * 64
                        bridge._write(folder, "session.json", value, replace=True)
                    elif mutation == "lease_replaced":
                        value = bridge._read(folder, "lease.json")
                        value["session_id"] = "d" * 32
                        bridge._write(folder, "lease.json", value, replace=True)
                    facts["lease_before"] = (folder.path / "lease.json").read_bytes()
                raise sentinel
            caught = None
            with patch.object(bridge, "_external", external):
                try:
                    bridge.attach(case.book, case.plan["id"], case.path.name, case.handle)
                except BaseException as error:
                    caught = error
            assert caught is not None
            own_folder = case.root / bridge.DIRECTORY / facts["sid"]
            if mutation.endswith("replaced"):
                assert getattr(caught, "code", None) == "platform_chrome_invalid_message"
                assert (own_folder / "lease.json").read_bytes() == facts["lease_before"]
            else:
                assert caught is sentinel
                lease = json.loads((own_folder / "lease.json").read_text())
                assert lease["detached"] is True
                assert bridge.worker_status(case.root, binding)["attached"] is False
                assert bridge.worker_status(case.root, binding)["active_sessions"] == 1
                if mutation == "after_handle":
                    case.assert_code("platform_chrome_worker_unavailable", bridge.heartbeat, case.book, case.handle)
                replacement = case.root / "replacement.json"
                assert bridge.attach(case.book, case.plan["id"], case.path.name, replacement)["ok"]
                bridge.heartbeat(case.book, replacement, detach=True)
            if "handle" in facts:
                assert case.handle.read_bytes() == facts["handle"]
            assert {name: (other_path / name).read_bytes() for name in other_files} == other_files
            rows.append({"case": exception.__name__ + ":" + mutation, "pass": True,
                "unrelated_lease_unchanged": True, "published_handle_preserved": "handle" in facts})
        finally:
            case.doCleanups()

for residue in ("empty", "stage", "session_only", "unknown_outbox", "unknown_claim", "unknown_response", "expired_claim"):
    case = DeletedDraftRetirementTests()
    case.setUp()
    try:
        packet = case.packet()
        sid = "b" * 32
        with bridge._directory(case.book.root, sid, create=True) as folder, bridge._lock(folder):
            if residue == "stage":
                bridge._write(folder, ".chrome-stage-" + "f" * 32, {"unpublished": True})
            if residue in ("session_only", "expired_claim"):
                binding = {"target": packet["target"]}
                bridge._write(folder, "session.json", {"schema": 1, "session_id": sid,
                    "binding": binding, "binding_sha256": bridge._digest(binding), "token_sha256": "f" * 64, "ttl_seconds": 120})
            if residue == "expired_claim":
                bridge._write(folder, "lease.json", {"schema": 1, "session_id": sid, "expires": time.time() - 1, "detached": True})
            if residue.startswith("unknown_") or residue == "expired_claim":
                prefix = {"unknown_outbox": "outbox", "unknown_claim": "claimed", "unknown_response": "response", "expired_claim": "claimed"}[residue]
                bridge._write(folder, prefix + "-" + "c" * 32 + ".json", {"operation": "create_draft"})
            evidence = {path.name: path.read_bytes() for path in folder.path.iterdir() if path.is_file() and path.name != "session.lock"}
            evidence_path = folder.path
        before = case.originals()
        should_pass = residue in ("empty", "stage", "session_only")
        try:
            result = case.retire(packet)
        except Exception:
            assert not should_pass, residue
        else:
            assert should_pass and result["executed"], residue
        assert case.originals() == before
        assert case.case.adapter.created == 0
        assert {name: (evidence_path / name).read_bytes() for name in evidence} == evidence
        rows.append({"case": "retirement:" + residue, "pass": True, "retired": should_pass, "evidence_unchanged": True})
    finally:
        case.doCleanups()

hashes = {name: hashlib.sha256((ROOT / "skills/story-skill/scripts" / name).read_bytes()).hexdigest()
    for name in ("story_platform_chrome_bridge.py", "story_platform_drafts.py")}
report = {"source_sha256": hashes, "checks": len(rows), "passed": sum(row["pass"] for row in rows), "results": rows,
    "scope": "Independent local production APIs with interruption injection and fixture journals; no platform or live books"}
Path(__file__).with_name("independent-session-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key:value for key,value in report.items() if key != "results"}, ensure_ascii=False))
