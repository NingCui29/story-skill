"""Behavioral contract for the read-only local author workbench v1."""
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"
SPEC = importlib.util.spec_from_file_location("workbench_contract_story", TOOL)
story = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(story)


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-workbench-")
        self.root = Path(self.temp.name).resolve() / "中文 书#册%目录"
        self.addCleanup(self.temp.cleanup)
        self.title = '雨夜 <script>alert("workbench-title")</script> & “账本”'
        story.Book.create(self.root, self.title, "long")
        self.book = story.Book(self.root)
        self.addCleanup(self.book.close)
        self.body_tokens = []
        for chapter in (1, 2):
            self.commit(chapter)
        self.account_id = "ACCOUNT-SECRET-DO-NOT-DISPLAY"
        self.remote_book_id = "REMOTE-SECRET-DO-NOT-DISPLAY"
        self.publish_plan = story.publish.prepare(self.book, {
            "platform": "fanqie", "account_id": self.account_id,
            "remote_book_id": self.remote_book_id, "chapters": [1, 2], "mode": "draft",
        }, self.book.meta("revision"), summary=True)

    def commit(self, chapter):
        title = f"雨夜 & 账本#{chapter}%"
        token = f"FULL-CHAPTER-{chapter}-MUST-NOT-BE-EMBEDDED"
        text = (f"# 第{chapter}章 {title}\n"
                "沈禾把唯一的钥匙交给守门人。\n"
                "她答应在天亮之前带回账本。\n"
                f"{token}，这句只用于证明工作台没有嵌入整章。\n")
        plan = {
            "title": title, "volume_dir": "第一卷 雨#夜%", "goal": "用钥匙换取入口",
            "stop": "进入门内，不拿到账本", "constraints": ["天亮前返回"],
            "requires": [], "tags": ["沈禾"], "length": [10, 1000],
            "beats": [{"choice": "沈禾交出钥匙", "change": "得到入口并失去退路"}],
        }
        self.book.save_plan(chapter, plan, self.book.meta("revision"))
        draft = self.root / ".story/drafts" / f"第{chapter}章.md"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_bytes(text.encode("utf-8"))
        review = {name: {
            "note": "已核对人物选择、代价、连续性和停笔位置。",
            "quote": "沈禾把唯一的钥匙交给守门人。",
        } for name in story.CHECKS}
        delta = {
            "book_id": self.book.meta("id"), "base_revision": self.book.meta("revision"),
            "summary": f"第{chapter}章完成钥匙交接，并保留下一章要处理的账本。",
            "changes": [],
            "review": {"draft_sha256": story.digest(text), "checks": review, "issues": []},
        }
        result = self.book.commit(chapter, draft, delta)
        self.assertTrue(result["exports_complete"], result)
        self.body_tokens.append(token)

    def cli(self, command, *arguments):
        process = subprocess.run(
            [sys.executable, "-B", str(TOOL), command, "--book", str(self.root), *arguments],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        raw = process.stdout if process.stdout.strip() else process.stderr
        try:
            packet = json.loads(raw)
        except json.JSONDecodeError:
            self.fail(f"CLI did not return JSON: stdout={process.stdout!r} stderr={process.stderr!r}")
        return process, packet

    def test_workbench_links_use_existing_registered_volume_paths(self):
        packet = story.workbench.snapshot(self.book)
        page = story.workbench.render_html(packet)
        for row in packet["chapters"]["results"]:
            expected = self.book.chapter_path(row["chapter"])
            self.assertEqual(row["path"], expected)
            target = self.root / expected
            self.assertTrue(target.is_file())
            self.assertIn(target.as_uri(), page)
        process, cli_packet = self.cli("workbench-snapshot")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(cli_packet["chapters"]["results"], packet["chapters"]["results"])

    def test_unregistered_legacy_chapter_path_keeps_original_fallback(self):
        metadata = {"__root": str(self.root)}
        self.assertEqual(story.workbench._chapter_path(metadata, 1), "chapters/0001.md")

    def authoritative_state(self):
        state_path = self.root / ".story/state.sqlite3"
        ledger_path = self.root / ".story/publishing.sqlite3"
        chapter_paths = [self.root / self.book.chapter_path(chapter) for chapter in (1, 2)]
        return {
            "revision": self.book.meta("revision"),
            "core_bytes": state_path.read_bytes(),
            "core_mtime": state_path.stat().st_mtime_ns,
            "core_dump": tuple(self.book.db.iterdump()),
            "publishing_bytes": ledger_path.read_bytes(),
            "publishing_mtime": ledger_path.stat().st_mtime_ns,
            "chapters": {str(path): path.read_bytes() for path in chapter_paths},
        }

    def assert_story_error(self, code, call, *args, **kwargs):
        with self.assertRaises(story.StoryError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.details)
        return caught.exception

    def test_snapshot_and_export_do_not_change_authoritative_state(self):
        before = self.authoritative_state()
        first = story.workbench.snapshot(self.book)
        second = story.workbench.snapshot(self.book)
        exported = story.workbench.export(self.book)

        self.assertEqual(first["snapshot"]["id"], second["snapshot"]["id"])
        self.assertEqual(exported["snapshot_id"], first["snapshot"]["id"])
        self.assertEqual(self.authoritative_state(), before)

    def test_snapshot_contract_is_bounded_paged_and_composite(self):
        packet = story.workbench.snapshot(self.book, limit=1, budget=64000)
        required = {
            "contract", "captured_at", "snapshot", "book", "progress", "chapters",
            "artifacts", "cards", "world", "history", "analysis", "publish",
            "attention", "completeness",
        }
        self.assertTrue(required.issubset(packet), set(packet))
        self.assertEqual(packet["contract"], "story.workbench.v1")
        self.assertRegex(packet["captured_at"], r"^\d{4}-\d\d-\d\dT.*Z$")
        self.assertEqual(packet["book"], {
            "id": self.book.meta("id"), "root": str(self.root), "title": self.title,
            "kind": "long", "revision": self.book.meta("revision"),
        })

        composite = packet["snapshot"]
        self.assertEqual(composite["core_revision"], self.book.meta("revision"))
        for field in ("id", "publishing_fingerprint", "filesystem_manifest_sha256"):
            self.assertRegex(composite[field], r"^[0-9a-f]{64}$")

        chapters = packet["chapters"]
        self.assertEqual(chapters["total"], 2)
        self.assertEqual(chapters["offset"], 0)
        self.assertEqual(chapters["limit"], 1)
        self.assertEqual(chapters["returned"], 1)
        self.assertEqual(len(chapters["results"]), 1)
        self.assertIsNotNone(chapters["next_cursor"])
        self.assertTrue(chapters["has_more"])
        self.assertFalse(chapters["complete"])

        next_page = story.workbench.snapshot(
            self.book, limit=1, budget=64000, cursor=chapters["next_cursor"])
        self.assertEqual(next_page["snapshot"]["id"], composite["id"])
        self.assertEqual(next_page["chapters"]["offset"], 1)
        self.assertEqual(next_page["chapters"]["returned"], 1)
        self.assertFalse(next_page["chapters"]["has_more"])
        self.assertFalse(next_page["chapters"]["complete"])
        self.assertIsNone(next_page["chapters"]["next_cursor"])

        wider_page = story.workbench.snapshot(self.book, limit=2, budget=64000)
        self.assertEqual(wider_page["snapshot"]["id"], composite["id"])
        self.assertTrue(wider_page["chapters"]["complete"])

        serialized = story.dumps(packet)
        for token in (*self.body_tokens, self.account_id, self.remote_book_id):
            self.assertNotIn(token, serialized)
        self.assertNotRegex(serialized, r'"(?:body|text|prose)":')

        process, cli_packet = self.cli("workbench-snapshot", "--limit", "1", "--budget-bytes", "64000")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(cli_packet["contract"], "story.workbench.v1")
        self.assertEqual(cli_packet["chapters"]["returned"], 1)
        self.assertEqual(cli_packet["snapshot"]["id"], composite["id"])

        self.book.save_notes([{
            "id": "cursor-change", "kind": "hook", "text": "第二页之后新增的待办。",
            "source": "测试", "tags": [], "critical": False, "status": "active", "due": 3,
        }], self.book.meta("revision"))
        self.assert_story_error(
            "stale_snapshot", story.workbench.snapshot, self.book,
            limit=1, budget=64000, cursor=chapters["next_cursor"])

    def test_composite_identity_tracks_publishing_and_displayed_file_facts(self):
        initial = story.workbench.snapshot(self.book)
        revision = initial["snapshot"]["core_revision"]

        story.publish.cancel(self.book, self.publish_plan["id"])
        publishing_changed = story.workbench.snapshot(self.book)
        self.assertEqual(publishing_changed["snapshot"]["core_revision"], revision)
        self.assertNotEqual(publishing_changed["snapshot"]["publishing_fingerprint"],
                            initial["snapshot"]["publishing_fingerprint"])
        self.assertNotEqual(publishing_changed["snapshot"]["id"], initial["snapshot"]["id"])

        chapter = self.root / self.book.chapter_path(2)
        chapter.write_text(chapter.read_text(encoding="utf-8") + "作者在工具外修改。\n", encoding="utf-8")
        file_changed = story.workbench.snapshot(self.book)
        self.assertEqual(file_changed["snapshot"]["core_revision"], revision)
        self.assertNotEqual(file_changed["snapshot"]["filesystem_manifest_sha256"],
                            publishing_changed["snapshot"]["filesystem_manifest_sha256"])
        self.assertNotEqual(file_changed["snapshot"]["id"], publishing_changed["snapshot"]["id"])
        self.assertEqual(file_changed["progress"]["exports"]["changed_count"], 1)

    def test_cursor_rejects_an_off_page_chapter_change_without_revision_bump(self):
        first = story.workbench.snapshot(self.book, limit=1, budget=64000)
        cursor = first["chapters"]["next_cursor"]
        self.book.db.execute("UPDATE chapter_state SET summary=? WHERE chapter=1",
                             ("工具外改变了尚未读取页的摘要。",))
        self.book.db.commit()

        self.assert_story_error(
            "stale_snapshot", story.workbench.snapshot, self.book,
            limit=1, budget=64000, cursor=cursor)

    def test_composite_identity_tracks_publishing_export_files(self):
        before = story.workbench.snapshot(self.book)
        exported = story.publish.export_material(self.book, self.publish_plan["id"])
        self.assertTrue(exported["export_created"], exported)
        after_export = story.workbench.snapshot(self.book)
        self.assertNotEqual(after_export["snapshot"]["filesystem_manifest_sha256"],
                            before["snapshot"]["filesystem_manifest_sha256"])

        archive = Path(exported["export"]["path"])
        archive.write_bytes(archive.read_bytes() + b"changed outside the tool")
        after_change = story.workbench.snapshot(self.book)
        self.assertNotEqual(after_change["snapshot"]["filesystem_manifest_sha256"],
                            after_export["snapshot"]["filesystem_manifest_sha256"])
        self.assertNotEqual(after_change["snapshot"]["id"], after_export["snapshot"]["id"])

    def test_invalid_publishing_receipt_changes_the_composite_identity(self):
        before = story.workbench.snapshot(self.book)
        directory = self.root / ".story/publishing-exports"
        directory.mkdir(parents=True, exist_ok=True)
        receipt = directory / ("material-" + "f" * 32 + ".receipt.json")
        receipt.write_text("not valid receipt json", encoding="utf-8")
        after = story.workbench.snapshot(self.book)
        self.assertEqual(after["publish"]["invalid_exports"], 1)
        self.assertNotEqual(after["snapshot"]["publishing_fingerprint"],
                            before["snapshot"]["publishing_fingerprint"])
        self.assertNotEqual(after["snapshot"]["id"], before["snapshot"]["id"])

    def test_noncanonical_publishing_manifest_is_rejected(self):
        ledger = self.root / ".story/publishing.sqlite3"
        db = sqlite3.connect(ledger)
        try:
            trigger = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='plans_immutable'"
            ).fetchone()[0]
            manifest = db.execute(
                "SELECT manifest FROM plans WHERE id=?", (self.publish_plan["id"],)
            ).fetchone()[0]
            db.execute("DROP TRIGGER plans_immutable")
            db.execute("UPDATE plans SET manifest=? WHERE id=?",
                       (manifest + " ", self.publish_plan["id"]))
            db.execute(trigger)
            db.commit()
        finally:
            db.close()

        self.assert_story_error("publishing_corrupt", story.workbench.snapshot, self.book)

    def test_publishing_manifest_limit_is_checked_before_manifest_validation(self):
        with (patch.object(story.workbench, "MAX_PUBLISH_MANIFEST_BYTES", 1),
              patch.object(story.publish, "_validated_manifest",
                           wraps=story.publish._validated_manifest) as validated):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)
        validated.assert_not_called()

    def test_missing_or_wrong_size_publish_zip_is_required_attention(self):
        exported = story.publish.export_material(self.book, self.publish_plan["id"])
        archive = Path(exported["export"]["path"])
        archive.unlink()

        missing = story.workbench.snapshot(self.book)
        self.assertEqual(missing["publish"]["export_receipts_total"], 1)
        self.assertEqual(missing["publish"]["archives_missing"], 1)
        self.assertEqual(missing["publish"]["archives_size_mismatch"], 0)
        self.assertFalse(missing["publish"]["archive_hash_checked"])
        self.assertTrue(any(item["code"] == "publish_archives_missing" and
                            item["level"] == "required" for item in missing["attention"]))
        page = story.workbench.render_html(missing)
        self.assertIn("1 份导出回执", page)
        self.assertNotIn("份材料导出", page)
        self.assertIn("尚未逐字节核验 ZIP", page)

        archive.write_bytes(b"wrong-size")
        mismatched = story.workbench.snapshot(self.book)
        self.assertEqual(mismatched["publish"]["archives_missing"], 0)
        self.assertEqual(mismatched["publish"]["archives_size_mismatch"], 1)
        self.assertTrue(any(item["code"] == "publish_archives_size_mismatch" and
                            item["level"] == "required" for item in mismatched["attention"]))

    def test_budget_and_page_limits_are_enforced(self):
        self.assert_story_error("budget_exceeded", story.workbench.snapshot, self.book, budget=256)
        self.assert_story_error("invalid_input", story.workbench.snapshot, self.book, limit=101)

    def test_sparse_future_plan_does_not_claim_the_next_chapter_is_ready(self):
        plan = {
            "title": "更远的一章", "volume_dir": "第一卷 雨夜", "goal": "处理后续选择",
            "stop": "留下新的问题", "constraints": ["不越过当前章"], "requires": [],
            "tags": ["沈禾"], "length": [10, 1000],
            "beats": [{"choice": "沈禾先等待", "change": "远期计划已存在"}],
        }
        self.book.save_plan(4, plan, self.book.meta("revision"))
        packet = story.workbench.snapshot(self.book)
        self.assertGreater(packet["chapters"]["planned_total"], packet["progress"]["last_chapter"])
        self.assertFalse(packet["progress"]["next_plan_exists"])
        self.assertIn("先补第3章计划", story.workbench.render_html(packet))

    def test_due_card_total_does_not_underreport_the_bounded_list(self):
        notes = [{
            "id": f"due-{index:02d}", "kind": "hook", "text": f"第 {index} 项待处理事项。",
            "source": "测试", "tags": [], "critical": False, "status": "active", "due": 3,
        } for index in range(12)]
        self.book.save_notes(notes, self.book.meta("revision"))
        packet = story.workbench.snapshot(self.book)
        self.assertEqual(packet["cards"]["due_or_overdue_total"], 12)
        self.assertEqual(packet["cards"]["due_or_overdue_returned"], 10)
        self.assertEqual(packet["cards"]["due_or_overdue_omitted"], 2)
        self.assertEqual(len(packet["cards"]["due_or_overdue"]), 10)
        self.assertTrue(any("有 12 项" in item["message"] for item in packet["attention"]))

    def test_unregistered_files_are_reported_honestly_and_never_guessed(self):
        misleading = {
            "最新草稿.md": "这不是已登记草稿。",
            "全书总纲_最终采用版.md": "这不是已登记大纲。",
            "封面_最终版.png": "not really a png",
        }
        for name, content in misleading.items():
            (self.root / name).write_text(content, encoding="utf-8")

        packet = story.workbench.snapshot(self.book)
        self.assertFalse(packet["artifacts"]["registry_exists"])
        self.assertEqual(packet["artifacts"]["recent"], [])
        for field in ("drafts", "candidates", "readable_outlines", "covers_and_other_artifacts"):
            self.assertEqual(packet["completeness"][field], "unregistered")
        serialized = story.dumps(packet)
        for name in misleading:
            self.assertNotIn(name, serialized)

        result = story.workbench.export(self.book)
        page = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn("尚未登记", page)
        for name in misleading:
            self.assertNotIn(name, page)

    def test_editor_http_saves_candidate_and_rejects_stale_or_foreign_requests(self):
        import threading
        import http.client
        server = story.workbench.editor_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            route = '/' + server.editor_url.split('/')[-2] + '/'
            token = route.strip('/')
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            connection.request('GET', route)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn('保存候选稿', response.read().decode())
            connection.close()
            origin = f'http://127.0.0.1:{server.server_port}'
            text = '第1章 修订\n\n她走进雨里。'
            body = json.dumps({'chapter': 1, 'text': text})
            headers = {'Content-Type': 'application/json', 'Origin': origin, 'X-Story-Token': token}
            def post(extra):
                client = http.client.HTTPConnection('127.0.0.1', server.server_port)
                client.request('POST', route+'candidate', body, extra)
                response = client.getresponse()
                status, raw = response.status, response.read()
                client.close()
                return status, json.loads(raw)
            before = self.book.meta('revision')
            status, result = post(headers)
            self.assertEqual(status, 200, result)
            self.assertTrue((self.root / result['path']).read_text(encoding='utf-8').endswith(text))
            self.assertEqual(self.book.meta('revision'), before)
            self.assertFalse(result['formal_changed'])
            status, second = post(headers)
            self.assertEqual(status, 200)
            self.assertNotEqual(result['path'], second['path'])
            self.assertEqual(post({**headers, 'Origin': 'https://example.com'})[0], 403)
            self.assertEqual(post({**headers, 'X-Story-Token': 'wrong'})[0], 403)
            self.assertEqual(post({**headers, 'Host': 'example.com'})[0], 403)
            packet = story.workbench.snapshot(self.book)
            (self.root / packet['chapters']['results'][0]['path']).write_bytes(b'changed')
            self.assertEqual(post(headers)[0], 409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_editor_switching_and_recovery_request_order(self):
        import shutil
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for browser request ordering checks')
        page = story.workbench._editor_page(story.workbench.snapshot(self.book), {}, 'test').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        opening = script[script.index('async function openDoc'):script.index('function pendingEdits')]
        recovery = script[script.index('async function recover'):script.index("$('text').addEventListener", script.index('async function recover'))]
        program = r"""
const assert=require('assert');
let openSequence=0,active=null; const docs=new Map(), location={hash:''};
const crypto={randomUUID:()=> 'test-recovery-key'};
let requests=[],shown=[],writes=[];
const show=d=>{active=d.id;shown.push(d.id);};
const note=()=>{}; const catalog=async()=>{}; const pendingEdits=()=>{};
const dirty=d=>d.value!==d.text;
let call=(action,data)=>new Promise(resolve=>requests.push({data,resolve}));
eval(process.argv[1]);eval(process.argv[2]);
(async()=>{
 const first=openDoc('first'),second=openDoc('second');
 requests[1].resolve({id:'second',text:'two'});await second;
 requests[0].resolve({id:'first',text:'one'});await first;
 assert.deepStrictEqual(shown,['second']);assert.strictEqual(location.hash,'second');
 requests=[];
 const repeat1=openDoc('repeat'),repeat2=openDoc('repeat');
 requests[1].resolve({id:'repeat',text:'original'});await repeat2;
 docs.get('repeat').value='unsaved edit';
 requests[0].resolve({id:'repeat',text:'original'});await repeat1;
 assert.strictEqual(docs.get('repeat').value,'unsaved edit');
 requests=[];
 call=(action,data)=>new Promise(resolve=>{writes.push(data.text);requests.push(resolve);});
 const d={id:'draft',text:'original',value:'first edit',recoveryKey:'key'};
 const save1=recover(d);d.value='second edit';const save2=recover(d);
 assert.deepStrictEqual(writes,['first edit']);
 requests[0]({path:'recovery'});await save1;
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepStrictEqual(writes,['first edit','second edit']);
 requests[1]({path:'recovery'});await save2;
 await recover(d);assert.strictEqual(writes.length,2);
 assert.strictEqual(d.recovered,'second edit');
 const forced=recover(d,true);assert.strictEqual(writes.length,3);
 requests[2]({path:'recreated'});await forced;
 assert.strictEqual(d.recoveryPath,'recreated');
 call=async()=>{throw Error('disk failure');};
 await recover(d,true);assert.notStrictEqual(d.recovered,d.value);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run([node, '-e', program, opening, recovery], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_interface_identifier_is_independent_of_session_token(self):
        packet = story.workbench.snapshot(self.book)
        first = story.workbench._editor_page(packet, {}, 'first-token').decode()
        second = story.workbench._editor_page(packet, {}, 'second-token').decode()
        pattern = r'界面标识：([0-9a-f]{10})'
        self.assertEqual(re.search(pattern, first).group(1), re.search(pattern, second).group(1))

    def test_pending_edits_remain_accessible_outside_catalog(self):
        import shutil
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for editor interaction checks')
        page = story.workbench._editor_page(story.workbench.snapshot(self.book), {}, 'test').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        code = script[script.index('function pendingEdits'):script.index('function item')]
        program = r"""
const assert=require('assert'),docs=new Map(),elements={};let opened;
const $=id=>elements[id]||(elements[id]={children:[],replaceChildren(){this.children=[];},append(b){this.children.push(b);}});
const document={createElement:()=>({})},openDoc=id=>{opened=id;};
const dirty=d=>d.editable&&d.value!==d.text;
eval(process.argv[1]);
docs.set('outside-page',{id:'outside-page',title:'outside page',editable:true,text:'old',value:'new'});
docs.set('clean',{id:'clean',editable:true,text:'same',value:'same'});
pendingEdits();assert.strictEqual($('pending-list').children.length,1);
$('pending-list').children[0].onclick();assert.strictEqual(opened,'outside-page');
docs.get('outside-page').recovered='new';pendingEdits();
assert($('pending-list').children[0].textContent.includes('已写入恢复稿'));
docs.get('outside-page').value='old';pendingEdits();assert.strictEqual($('pending-box').hidden,true);
"""
        result = subprocess.run([node, '-e', program, code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_autorecovery_protects_changed_or_missing_metadata_and_legacy_receipts(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        for change in ('chapter', 'remove', 'corrupt', 'legacy'):
            with self.subTest(change=change):
                payload = {**source, 'text': '原有恢复正文', 'recovery_key': 'recovery-case-' + change + '-0001'}
                first = w._editor_save(self.root, payload, recovery=True)
                path = self.root / first['path']
                metadata = Path(str(path) + '.meta.json')
                if change == 'chapter':
                    meta = json.loads(metadata.read_text()); meta.update(chapter=2, source='formal:2')
                    metadata.write_text(json.dumps(meta))
                elif change == 'remove':
                    metadata.unlink()
                elif change == 'corrupt':
                    metadata.write_text('{broken')
                original_meta = metadata.read_bytes() if metadata.exists() else None
                receipt = {} if change == 'legacy' else {
                    'recovery_sha256': first['recovery_sha256'],
                    'recovery_metadata_sha256': first['recovery_metadata_sha256']}
                second = w._editor_save(self.root, {**payload, **receipt, 'text': '新的恢复正文'}, recovery=True)
                self.assertNotEqual(first['path'], second['path'])
                self.assertTrue(second['conflict'])
                self.assertEqual(path.read_text(), payload['text'])
                self.assertEqual(metadata.read_bytes() if metadata.exists() else None, original_meta)
                third = w._editor_save(self.root, {**payload, 'text': '继续输入',
                    'recovery_key': second['recovery_key'], 'recovery_sha256': second['recovery_sha256'],
                    'recovery_metadata_sha256': second['recovery_metadata_sha256']}, recovery=True)
                self.assertEqual(third['path'], second['path'])
                self.assertFalse(third['conflict'])

    def test_autorecovery_continues_after_transient_body_write_failure(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        payload = {**source, 'text': '故障前的编辑', 'recovery_key': 'temporary-failure-001'}
        original = w.api.atomic_write
        def fail_body(path, *args, **kwargs):
            if str(path).endswith('.txt'):
                raise OSError('temporary failure')
            return original(path, *args, **kwargs)
        with patch.object(w.api, 'atomic_write', side_effect=fail_body):
            self.assert_story_error('workbench_partial_save', w._editor_save, self.root, payload, recovery=True)
        pending = next(row for row in w._editor_catalog(self.root)['files'] if row['id'].startswith('pending:'))
        original_record = (self.root / pending['path']).read_bytes()
        second = w._editor_save(self.root, {**payload, 'text': '恢复后最新编辑'}, recovery=True)
        self.assertEqual(second['conflict_reason'], 'pending')
        self.assertEqual(w._editor_document(self.root, second['id'])['text'], '恢复后最新编辑')
        self.assertEqual((self.root / pending['path']).read_bytes(), original_record)

    def test_recovery_metadata_race_cannot_overwrite_external_record(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        payload = {**source, 'text': '旧恢复正文', 'recovery_key': 'metadata-race-0001'}
        first = w._editor_save(self.root, payload, recovery=True)
        path = self.root / first['path']; metadata = Path(str(path) + '.meta.json')
        external = json.loads(metadata.read_text()); external['chapter'] = 2
        original = w.api.atomic_write
        def change_after_body(target, *args, **kwargs):
            result = original(target, *args, **kwargs)
            if Path(target) == path:
                metadata.write_text(json.dumps(external))
            return result
        with patch.object(w.api, 'atomic_write', side_effect=change_after_body):
            self.assert_story_error('workbench_save_rolled_back', w._editor_save, self.root,
                {**payload, 'text': '页面的新编辑', 'recovery_sha256': first['recovery_sha256'],
                 'recovery_metadata_sha256': first['recovery_metadata_sha256']}, recovery=True)
        self.assertEqual(json.loads(metadata.read_text()), external)
        self.assertEqual(path.read_text(), payload['text'])

    def test_service_single_instance_status_stop_and_restart(self):
        import threading
        w = story.workbench
        revision = self.book.meta('revision')
        server = w.editor_server(self.root)
        def serve():
            try:
                server.serve_forever()
            finally:
                server.server_close()
        thread = threading.Thread(target=serve, daemon=True); thread.start()
        try:
            self.assert_story_error('workbench_running', w.editor_server, self.root)
            status = w._service_request(self.root)
            self.assertTrue(status['running'])
            self.assertFalse(status['outdated'])
            self.assertEqual(status['url'], server.editor_url)
            self.assert_story_error('workbench_unsaved', w._service_request, self.root, 'stop')
            self.assertTrue(w._service_request(self.root, 'stop', True)['stop_requested'])
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertFalse(w._service_request(self.root)['running'])
            newer = w.editor_server(self.root)
            newer.server_close()
            self.assertEqual(self.book.meta('revision'), revision)
        finally:
            if thread.is_alive():
                server.shutdown(); thread.join(timeout=5)
            server.server_close()

    def test_service_rejects_forged_identity_and_releases_lease_after_start_failure(self):
        import threading
        w = story.workbench
        original = w.api.atomic_write
        def fail_record(path, *args, **kwargs):
            if str(path).endswith('workbench-service.json'):
                raise OSError('record unavailable')
            return original(path, *args, **kwargs)
        with patch.object(w.api, 'atomic_write', side_effect=fail_record):
            with self.assertRaises(OSError):
                w.editor_server(self.root)
        server = w.editor_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            path = self.root / '.story/workbench-service.json'
            record = json.loads(path.read_text()); record['instance'] = '0' * 32
            path.write_text(json.dumps(record))
            self.assert_story_error('workbench_service_mismatch', w._service_request, self.root, 'stop', True)
            self.assertTrue(thread.is_alive())
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close()

    def test_service_lease_released_when_process_is_terminated(self):
        program = r'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location('service_story', sys.argv[1])
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
server = s.workbench.editor_server(sys.argv[2])
print('ready', flush=True)
server.serve_forever()
'''
        child = subprocess.Popen([sys.executable, '-u', '-c', program, str(TOOL), str(self.root)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.assert_story_error('workbench_running', story.workbench.editor_server, self.root)
            child.terminate(); child.wait(timeout=5)
            server = story.workbench.editor_server(self.root)
            server.server_close()
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)
            child.stdout.close(); child.stderr.close()

    def test_candidate_provenance_change_addition_and_removal_conflict(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        saved = w._editor_save(self.root, {**source, 'text': '第一章候选'})
        path = self.root / saved['path']
        metadata = Path(str(path) + '.meta.json')
        original = metadata.read_bytes()
        for change in ('chapter', 'remove', 'add', 'whitespace'):
            with self.subTest(change=change):
                metadata.write_bytes(original)
                if change == 'add':
                    metadata.unlink()
                opened = w._editor_document(self.root, saved['id'])
                if change == 'chapter':
                    data = json.loads(original)
                    data.update(chapter=2, base_sha256=w._editor_document(self.root, 'formal:2')['sha256'])
                    metadata.write_text(json.dumps(data))
                elif change == 'remove':
                    metadata.unlink()
                elif change == 'add':
                    metadata.write_bytes(original)
                else:
                    metadata.write_bytes(original + b'\n')
                before = path.read_bytes()
                self.assert_story_error('stale_snapshot', w._editor_save, self.root,
                                        {**opened, 'text': '仍按第一章修改'})
                self.assertEqual(path.read_bytes(), before)

    def test_old_generated_filename_and_bom_hide_only_known_heading(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        folder = self.root / '.story/drafts/workbench'
        folder.mkdir(parents=True, exist_ok=True)
        for bom in ('', '\ufeff'):
            for legacy in (False, True):
                with self.subTest(bom=bool(bom), legacy=legacy):
                    name = ('第1章_基线' + source['sha256'] + '_修订' + 'a' * 32 if legacy else source['title']) + '.md'
                    path = folder / name
                    prefix = bom + '# ' + source['title'] + '\r\n\r\n'
                    path.write_bytes((prefix + '正文第一段。\n\n第二段。').encode('utf-8'))
                    opened = w._editor_document(self.root, 'file:' + path.relative_to(self.root).as_posix())
                    self.assertEqual(opened['text'], '正文第一段。\n\n第二段。')
                    self.assertEqual(opened['prefix'], prefix)
                    saved = w._editor_save(self.root, {**opened, 'text': '修改后的正文。'})
                    self.assertEqual((self.root / saved['path']).read_bytes(), (prefix + '修改后的正文。').encode('utf-8'))
        path.write_text('第1章 她写在纸上的字\n\n正文。')
        opened = w._editor_document(self.root, 'file:' + path.relative_to(self.root).as_posix())
        self.assertTrue(opened['text'].startswith('第1章 她写在纸上的字'))
        self.assertEqual(opened['prefix'], '')

    def _crash_save(self, payload, phase, recovery=False):
        payload_path = self.root / '.crash-payload.json'
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        program = r'''
import importlib.util, json, os, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('crash_story', sys.argv[1])
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)
w = story.workbench
original = w.api.atomic_write
phase = sys.argv[4]
def write(path, *args, **kwargs):
    name = Path(path).as_posix()
    is_body = name.endswith(('.md', '.txt')) and '/.backups/' not in name
    if phase == 'before-body' and is_body:
        os._exit(73)
    if phase == 'before-meta' and name.endswith('.meta.json'):
        os._exit(73)
    result = original(path, *args, **kwargs)
    if phase == 'after-meta' and name.endswith('.meta.json'):
        os._exit(73)
    return result
w.api.atomic_write = write
w._editor_save(Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text(encoding='utf-8')), recovery=sys.argv[5] == '1')
'''
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', program, str(TOOL), str(self.root), str(payload_path),
                                 phase, '1' if recovery else '0'], capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 73, result.stderr)

    def test_abrupt_exit_preserves_complete_record_at_each_write_boundary(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        formal_before = (self.root / source['path']).read_bytes()
        for phase in ('before-body', 'before-meta', 'after-meta'):
            with self.subTest(phase=phase):
                text = '进程中断前的编辑：' + phase
                self._crash_save({**source, 'text': text}, phase)
                # A fresh server uses only persisted files; no in-memory save state.
                server = w.editor_server(self.root)
                server.server_close()
                rows = [r for r in w._editor_catalog(self.root)['files'] if r['id'].startswith('pending:')]
                pending = next(w._editor_document(self.root, r['id']) for r in rows
                               if w._editor_document(self.root, r['id'])['text'] == text)
                self.assertTrue(pending['needs_recovery'])
                self.assertEqual(pending['chapter'], 1)
                self.assertEqual(pending['prefix'], source['prefix'])
                target = self.root / pending['path'][:-13]
                if target.exists():
                    incomplete = w._editor_document(self.root, 'file:' + target.relative_to(self.root).as_posix())
                    self.assertFalse(incomplete['editable'])
                    self.assertEqual(incomplete['metadata_error']['code'], 'workbench_metadata_pending')
                # Recovery can be saved without making an artificial text change.
                saved = w._editor_save(self.root, pending)
                self.assertEqual(w._editor_document(self.root, saved['id'])['text'], text)
                self.assertFalse(Path(str(self.root / saved['path']) + '.pending.json').exists())
                self.assertEqual((self.root / source['path']).read_bytes(), formal_before)

    def test_interrupted_recovery_keeps_old_backup_and_does_not_overwrite_external_edit(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        payload = {**source, 'recovery_key': '1234567890abcdef', 'text': '前一份恢复文字'}
        first = w._editor_save(self.root, payload, recovery=True)
        self._crash_save({**payload, 'text': '中断前最新文字', 'recovery_sha256': first['recovery_sha256'], 'recovery_metadata_sha256': first['recovery_metadata_sha256']},
                         'before-meta', recovery=True)
        target = self.root / first['path']
        self.assertTrue(any(p.read_text() == payload['text'] for p in
                            (self.root / '.story/drafts/workbench/.backups').glob('*/previous') if p.is_file()))
        target.write_text('外部修订仍应保留')
        pending = w._editor_document(self.root, 'pending:' + first['path'] + '.pending.json')
        self.assertEqual(pending['text'], '中断前最新文字')
        continued = w._editor_save(self.root, {**payload, 'text': '后续自动保存'}, recovery=True)
        self.assertNotEqual(continued['path'], first['path'])
        self.assertEqual(continued['conflict_reason'], 'pending')
        saved = w._editor_save(self.root, pending)
        self.assertEqual(w._editor_document(self.root, saved['id'])['text'], '中断前最新文字')
        self.assertEqual(target.read_text(), '外部修订仍应保留')

    def test_pending_record_corruption_and_second_writer_are_not_silent(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        payload = {**source, 'recovery_key': '1234567890abcdef', 'text': '已暂存的文字'}
        self._crash_save(payload, 'before-body', recovery=True)
        row = next(r for r in w._editor_catalog(self.root)['files'] if r['id'].startswith('pending:'))
        path = self.root / row['path']
        before = path.read_bytes()
        continued = w._editor_save(self.root, {**payload, 'text': '竞争写入'}, recovery=True)
        self.assertTrue(continued['conflict'])
        self.assertEqual(continued['conflict_reason'], 'pending')
        self.assertEqual(path.read_bytes(), before)
        record = json.loads(before)
        record['text'] = '被外部改写的记录'
        path.write_text(json.dumps(record))
        self.assert_story_error('workbench_pending_invalid', w._editor_document, self.root, row['id'])
        self.assertIn(row['id'], [r['id'] for r in w._editor_catalog(self.root)['files']])

    def test_legacy_save_endpoint_also_writes_provenance(self):
        w = story.workbench
        packet = w.snapshot(self.book)
        saved = w._save_candidate(self.root, packet, 1, '兼容入口编辑')
        opened = w._editor_document(self.root, 'file:' + saved['path'])
        self.assertEqual(opened['text'], '兼容入口编辑')
        self.assertEqual(opened['chapter'], 1)
        self.assertTrue(opened['metadata_sha256'])
        self.assertFalse(list((self.root / '.story/drafts/workbench').glob('*.pending.json')))
        unchanged = w._save_candidate(self.root, packet, 1, w._editor_document(self.root, 'formal:1')['text'])
        self.assertTrue(unchanged['path'].startswith('.story/drafts/workbench/'))

    def test_corrupt_metadata_is_readonly_and_not_unregistered(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        saved = w._editor_save(self.root, {**source, 'text': '保存的候选正文'})
        path = self.root / saved['path']
        raw = path.read_bytes()
        metadata = Path(str(path) + '.meta.json')
        for broken in ('{broken', '[]', '{"prefix": null}', '{"chapter": true}', '{"chapter": 1000000000000000000000000000000}'):
            metadata.write_text(broken)
            opened = w._editor_document(self.root, saved['id'])
            self.assertFalse(opened['editable'])
            self.assertIn('来源记录', opened['status'])
            self.assertNotIn('未登记', opened['status'])
            self.assertIn('保存的候选正文', opened['text'])
            self.assertEqual(path.read_bytes(), raw)
            self.assert_story_error('invalid_input', w._editor_save, self.root,
                                    {**opened, 'text': '不可覆盖'})
        metadata.unlink()
        legacy = w._editor_document(self.root, saved['id'])
        self.assertTrue(legacy['editable'])
        self.assertIn('未登记', legacy['status'])

    def test_invalid_editor_chapter_never_reaches_sqlite(self):
        w = story.workbench
        self.assert_story_error('invalid_input', w._editor_document, self.root, 'formal:' + str(10 ** 30))
        source = w._editor_document(self.root, 'formal:1')
        for chapter in (True, -1, 0, 10 ** 30, '1'):
            self.assert_story_error('invalid_input', w._editor_save, self.root,
                                    {**source, 'chapter': chapter, 'text': '恢复稿',
                                     'recovery_key': '1234567890abcdef'}, recovery=True)

    def test_candidate_complete_file_limit_matches_reopen_limit(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        limit = w.AUTHOR_TEXT_LIMIT
        self.assert_story_error('workbench_write_limit', w._editor_save, self.root,
                                {**source, 'text': 'x' * limit})
        text = 'x' * (limit - len(source['prefix'].encode('utf-8')))
        saved = w._editor_save(self.root, {**source, 'text': text})
        self.assertEqual((self.root / saved['path']).stat().st_size, limit)
        self.assertEqual(w._editor_document(self.root, saved['id'])['text'], text)

    def test_metadata_failure_removes_partial_candidate_from_catalog(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        original = w.api.atomic_write
        def write(path, *args, **kwargs):
            if str(path).endswith('.meta.json'):
                raise OSError('simulated metadata failure')
            return original(path, *args, **kwargs)
        with patch.object(w.api, 'atomic_write', side_effect=write):
            error = self.assert_story_error('workbench_save_rolled_back', w._editor_save, self.root,
                                           {**source, 'text': 'partial candidate'})
        self.assertFalse((self.root / error.details['path']).exists())
        self.assertTrue(Path(error.details['incomplete_path']).exists())
        self.assertNotIn('file:' + error.details['path'], [r['id'] for r in w._editor_catalog(self.root)['files']])

    def test_partial_save_reports_paths_when_rollback_cannot_finish(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        original = w.api.atomic_write
        def write(path, *args, **kwargs):
            if str(path).endswith('.meta.json'):
                raise OSError('metadata unavailable')
            return original(path, *args, **kwargs)
        with patch.object(w.api, 'atomic_write', side_effect=write), patch.object(
                w.api, '_retire_bound_file', side_effect=OSError('rollback unavailable')):
            error = self.assert_story_error('workbench_partial_save', w._editor_save, self.root,
                                           {**source, 'text': 'retained partial'})
        self.assertTrue((self.root / error.details['path']).exists())
        self.assertIn('incomplete_path', error.details)
        self.assertIn('rollback_error', error.details)

    def test_recovery_metadata_failure_restores_previous_body_and_metadata(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        payload = {**source, 'text': 'previous recovery', 'recovery_key': '1234567890abcdef'}
        first = w._editor_save(self.root, payload, recovery=True)
        path = self.root / first['path']
        metadata = Path(str(path) + '.meta.json').read_bytes()
        original = w.api.atomic_write
        def write(target, *args, **kwargs):
            if str(target).endswith('.meta.json'):
                raise OSError('simulated metadata failure')
            return original(target, *args, **kwargs)
        with patch.object(w.api, 'atomic_write', side_effect=write):
            self.assert_story_error('workbench_save_rolled_back', w._editor_save, self.root,
                                    {**payload, 'text': 'new recovery', 'recovery_sha256': first['recovery_sha256'],
                                     'recovery_metadata_sha256': first['recovery_metadata_sha256']}, recovery=True)
        self.assertEqual(path.read_text(), payload['text'])
        self.assertEqual(Path(str(path) + '.meta.json').read_bytes(), metadata)

    def test_missing_recovery_is_recreated(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        payload = {**source, 'text': '待保存内容', 'recovery_key': '1234567890abcdef'}
        first = w._editor_save(self.root, payload, recovery=True)
        (self.root / first['path']).unlink()
        second = w._editor_save(self.root, {**payload, 'recovery_sha256': first['recovery_sha256'], 'recovery_metadata_sha256': first['recovery_metadata_sha256']}, recovery=True)
        self.assertEqual((self.root / second['path']).read_text(), '待保存内容')

    def test_unreadable_directory_does_not_hide_formal_chapters(self):
        blocked = self.root / '01_大纲细纲'
        blocked.mkdir()
        original = Path.iterdir
        def scan(path):
            if path == blocked:
                raise PermissionError('test unreadable directory')
            return original(path)
        with patch.object(Path, 'iterdir', scan):
            catalog = story.workbench._editor_catalog(self.root)
        self.assertEqual(len(catalog['chapters']), 2)
        self.assertTrue(any('01_大纲细纲' in warning for warning in catalog['warnings']))
        self.assertTrue(catalog['files'])

    def test_recovery_forks_after_external_edit(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        payload = {**source, 'text': '首次恢复', 'recovery_key': '1234567890abcdef'}
        first = w._editor_save(self.root, payload, recovery=True)
        original = self.root / first['path']
        original.write_text('外部编辑')
        second = w._editor_save(self.root, {**payload, 'text': '页面继续编辑',
                                'recovery_sha256': first['recovery_sha256'], 'recovery_metadata_sha256': first['recovery_metadata_sha256']}, recovery=True)
        self.assertTrue(second['conflict'])
        self.assertNotEqual(second['path'], first['path'])
        self.assertEqual(original.read_text(), '外部编辑')
        self.assertEqual((self.root / second['path']).read_text(), '页面继续编辑')

    @unittest.skipIf(os.name == 'nt', 'Symbolic links require Windows privileges')
    def test_catalog_skips_link_and_reports_warning(self):
        source = story.workbench._editor_document(self.root, 'formal:1')
        (self.root / '.story/drafts/link.md').symlink_to(self.root / source['path'])
        catalog = story.workbench._editor_catalog(self.root)
        self.assertEqual(catalog['total'], 2)
        self.assertTrue(any('link.md' in warning for warning in catalog['warnings']))
        self.assertNotIn('file:.story/drafts/link.md', [r['id'] for r in catalog['files']])

    def test_reload_failure_and_concurrent_input_preserve_editor(self):
        import shutil
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js is required for editor interaction checks')
        page = story.workbench._editor_page(story.workbench.snapshot(self.book), {}, 'test').decode()
        script = page.split('<script>', 1)[1].split('</script>', 1)[0]
        code = script[script.index('async function reloadDocuments'):script.index("$('refresh').onclick")]
        program = r"""
const assert=require('assert');let reloading=false,active='draft',openSequence=0;
const docs=new Map(),messages=[];const note=s=>messages.push(s);
const dirty=d=>d.value!==d.text;const recover=async(d,force)=>{assert.strictEqual(force,true);d.recovered=d.value;};
const crypto={randomUUID:()=> 'key'},show=d=>{active=d.id;},catalog=async()=>{};
let call=async()=>{throw Error('read failed');};
eval(process.argv[1]);
(async()=>{
 const d={id:'draft',text:'old',value:'edited'};docs.set(d.id,d);
 await reloadDocuments(true);assert.strictEqual(docs.get('draft'),d);
 assert(messages.at(-1).includes('失败'));assert.strictEqual(reloading,false);
 let resolve;call=()=>new Promise(r=>{resolve=r;});
 const pending=reloadDocuments(true);await new Promise(r=>setImmediate(r));
 d.value='new input';resolve({id:'draft',text:'disk'});await pending;
 assert.strictEqual(docs.get('draft').value,'new input');
 call=async()=>({id:'draft',text:'disk'});await reloadDocuments(true);
 assert.strictEqual(docs.get('draft').value,'disk');assert.strictEqual(d.recovered,'new input');
 docs.get('draft').saving=true;await reloadDocuments(true);
 assert(messages.at(-1).includes('正在保存'));
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run([node, '-e', program, code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_keeps_lineage_and_heading_on_resave(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        recovered = w._editor_save(self.root, {**source, 'text': '恢复的正文。',
                                   'recovery_key': '1234567890abcdef'}, recovery=True)
        reopened = w._editor_document(self.root, recovered['id'])
        self.assertEqual(reopened['source'], source['id'])
        self.assertEqual(reopened['base_sha256'], source['base_sha256'])
        self.assertEqual(reopened['prefix'], source['prefix'])
        self.assertEqual(reopened['text'], '恢复的正文。')
        for n in range(3):
            w._editor_save(self.root, {**source, 'text': '连续恢复' + str(n),
                           'recovery_key': '1234567890abcdef'}, recovery=True)
        reopened = w._editor_document(self.root, recovered['id'])
        result = w._editor_save(self.root, {**reopened, 'text': '再次修改的正文。'})
        self.assertEqual((self.root / result['path']).read_text(), source['prefix'] + '再次修改的正文。')

    def test_candidate_roundtrip_preserves_formal_and_lineage(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        original = (self.root / source['path']).read_bytes()
        revision = self.book.meta('revision')
        result = w._editor_save(self.root, {**source, 'text': '她重新选择了一条路。'})
        catalog = w._editor_catalog(self.root)
        self.assertIn(result['id'], [r['id'] for r in catalog['files']])
        reopened = w._editor_document(self.root, result['id'])
        self.assertEqual(reopened['text'], '她重新选择了一条路。')
        self.assertEqual(reopened['source'], 'formal:1')
        self.assertEqual(reopened['base_sha256'], source['sha256'])
        self.assertEqual(reopened['comparison']['text'], source['text'])
        second = w._editor_save(self.root, {**reopened, 'text': '她又停了下来。'})
        self.assertNotEqual(second['id'], result['id'])
        self.assertEqual(w._editor_document(self.root, result['id'])['text'], reopened['text'])
        self.assertEqual((self.root / source['path']).read_bytes(), original)
        self.assertEqual(self.book.meta('revision'), revision)

    def test_old_candidates_new_chapter_materials_and_images_are_discoverable(self):
        w = story.workbench
        paths = {'.story/drafts/另一版/第1章 试稿.md': '另一个候选。',
                 '.story/drafts/第3章 下一章.md': '下一章尚未提交。',
                 '01_大纲细纲/第1章 细纲.md': '这是细纲。',
                 '.story/analysis/source/report.md': '这是分析。',
                 '04_封面/封面.png': b'\x89PNG\r\n'}
        for name, content in paths.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content if isinstance(content, bytes) else content.encode())
        files = w._editor_catalog(self.root)['files']
        self.assertTrue(set(paths) <= {r['path'] for r in files})
        old = w._editor_document(self.root, 'file:.story/drafts/另一版/第1章 试稿.md')
        self.assertIn('未登记', old['status'])
        self.assertIsNone(old['base_sha256'])
        future = w._editor_document(self.root, 'file:.story/drafts/第3章 下一章.md')
        result = w._editor_save(self.root, {**future, 'text': '继续写下一章。'})
        self.assertTrue((self.root / result['path']).is_file())
        outline = w._editor_document(self.root, 'file:01_大纲细纲/第1章 细纲.md')
        self.assertEqual(outline['kind'], 'material')
        self.assertNotIn('候选稿', outline['status'])
        cover = w._editor_document(self.root, 'file:04_封面/封面.png')
        self.assertFalse(cover['editable'])
        self.assertTrue(cover['image'].startswith('data:image/png;base64,'))

    def test_candidate_conflicts_are_preserved_and_recovery_survives_reload(self):
        w = story.workbench
        source = w._editor_document(self.root, 'formal:1')
        result = w._editor_save(self.root, {**source, 'text': '候选原文。'})
        opened = w._editor_document(self.root, result['id'])
        (self.root / result['path']).write_text('外部修改。', encoding='utf-8')
        with self.assertRaises(story.StoryError) as error:
            w._editor_save(self.root, {**opened, 'text': '页面修改。'})
        self.assertEqual(error.exception.code, 'stale_snapshot')
        recovery = w._editor_save(self.root, {'id': opened['id'], 'text': '页面修改。',
                                 'title': opened['title'], 'recovery_key': 'test-recovery-0001'}, recovery=True)
        self.assertEqual(w._editor_document(self.root, recovery['id'])['text'], '页面修改。')
        recovery2 = w._editor_save(self.root, {'id': opened['id'], 'text': '页面修改第二次。',
                                  'title': opened['title'], 'recovery_key': 'test-recovery-0001',
                                  'recovery_sha256': recovery['recovery_sha256'],
                                  'recovery_metadata_sha256': recovery['recovery_metadata_sha256']}, recovery=True)
        self.assertEqual(recovery2['id'], recovery['id'])
        self.assertEqual(w._editor_document(self.root, recovery['id'])['text'], '页面修改第二次。')
        self.assertEqual((self.root / result['path']).read_text(encoding='utf-8'), '外部修改。')

    def test_editor_search_pages_and_new_state_are_fresh(self):
        w = story.workbench
        first = w._editor_catalog(self.root, limit=1)
        second = w._editor_catalog(self.root, offset=1, limit=1)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['chapters'][0]['id'], 'formal:2')
        self.assertEqual(second['chapters'][0]['id'], 'formal:1')
        searched = w._editor_catalog(self.root, limit=1, query='第1章')
        self.assertEqual(searched['chapters'][0]['id'], 'formal:1')
        self.commit(3)
        fresh = w._editor_catalog(self.root, limit=1)
        self.assertEqual(fresh['chapters'][0]['id'], 'formal:3')
        self.assertNotEqual(first['snapshot'], fresh['snapshot'])

    def test_editor_cannot_read_unlisted_private_files_or_traversal(self):
        for value in ['file:.story/state.sqlite3', 'file:../outside.txt', 'file:/etc/passwd', 'formal:999']:
            with self.subTest(value=value), self.assertRaises(story.StoryError):
                story.workbench._editor_document(self.root, value)

    def test_reader_hides_only_matching_opening_heading(self):
        split = story.workbench._body_without_heading
        self.assertEqual(split("第2章 雨夜\n\n她来了。\n", 2, "第2章 雨夜"),
                         ("第2章 雨夜\n\n", "她来了。\n"))
        self.assertEqual(split("她念起第2章 雨夜。", 2, "第2章 雨夜"),
                         ("", "她念起第2章 雨夜。"))
        self.assertEqual(split("第3章 雨夜\n正文", 2, "第2章 雨夜")[0], "")

    def test_default_cli_exports_three_column_reader(self):
        process, result = self.cli("workbench-export")
        self.assertEqual(process.returncode, 0, process.stderr)
        page = Path(result["path"]).read_text(encoding="utf-8")
        self.assertIn('class="desk"', page)
        for token in self.body_tokens:
            self.assertIn(token, page)

    def test_reading_export_embeds_verified_text_and_navigation(self):
        result = story.workbench.export(self.book, include_text=True)
        page = Path(result["path"]).read_text(encoding="utf-8")
        for token in self.body_tokens:
            self.assertIn(token, page)
        self.assertIn('href="#chapter-1"', page)
        self.assertIn('id="chapter-2"', page)
        self.assertIn("第一卷 雨#夜%", page)
        self.assertNotIn('<script>alert(', page)

    def test_three_column_reader_script_is_hash_bound(self):
        import base64
        result = story.workbench.export(self.book, include_text=True)
        page = Path(result["path"]).read_text(encoding="utf-8")
        script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
        expected = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        self.assertIn("script-src 'sha256-" + expected + "'", page)
        self.assertIn('aria-label="作品目录"', page)
        self.assertIn('class="manuscript"', page)
        self.assertIn('aria-label="章节信息"', page)
        self.assertIn('data-note="chapter-1"', page)
        self.assertIn('id="overview"', page)

    def test_reader_empty_and_partial_directory_are_explicit(self):
        packet = story.workbench.snapshot(self.book, limit=1)
        reading = story.workbench._reading_text(self.book, packet)
        page = story.workbench.render_html(packet, reading)
        self.assertIn('本页 1 章', page)
        self.assertNotIn('href="#chapter-1"', page)
        self.assertIn('id="chapter-search"', page)
        self.assertIn('id="previous" disabled', page)
        self.assertIn('id="next" disabled', page)
        packet["chapters"]["results"] = []
        page = story.workbench.render_html(packet, {})
        self.assertIn("尚无正式章节", page)
        self.assertIn('id="overview"', page)
        self.assertNotIn('class="chapter-link"', page)

    def test_reader_overview_preserves_delivery_and_coverage(self):
        packet = story.workbench.snapshot(self.book, limit=1)
        page = story.workbench.render_html(packet, story.workbench._reading_text(self.book, packet))
        self.assertIn("已载入 1 / 2 章，本页并非全书", page)
        self.assertIn("本地准备记录 1 份", page)
        self.assertIn("不代表平台已保存", page)
        self.assertIn('id="missing-chapter" hidden', page)
        self.assertNotIn(self.account_id, page)
        packet = story.workbench.snapshot(self.book)
        page = story.workbench.render_html(packet, story.workbench._reading_text(self.book, packet))
        self.assertIn("已载入全部 2 章", page)

    def test_reading_export_rejects_external_changes(self):
        packet = story.workbench.snapshot(self.book)
        path = self.root / packet["chapters"]["results"][0]["path"]
        path.write_text("外部改稿", encoding="utf-8")
        self.assert_story_error("workbench_changed", story.workbench.export,
                                self.book, include_text=True)
        self.assertFalse((self.root / ".story/workbench/index.html").exists())

    def test_html_is_escaped_self_contained_and_omits_complete_prose(self):
        process, result = self.cli("workbench-export", "--overview-only")
        self.assertEqual(process.returncode, 0, process.stderr)
        path = Path(result["path"])
        self.assertEqual(path, self.root / ".story/workbench/index.html")
        self.assertTrue(path.is_file())
        raw = path.read_bytes()
        page = raw.decode("utf-8")

        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertIn(html.escape(self.title, quote=True), page)
        self.assertNotIn('<script>alert("workbench-title")</script>', page)
        self.assertIn("<style", page.lower())
        self.assertNotRegex(page, r"(?i)\b(?:src|href)\s*=\s*['\"]\s*(?:https?:)?//")
        self.assertNotRegex(page, r"(?i)@import\s+url|url\(\s*['\"]?(?:https?:)?//")
        self.assertNotIn("http://", page.lower())
        self.assertNotIn("https://", page.lower())
        for token in (*self.body_tokens, self.account_id, self.remote_book_id):
            self.assertNotIn(token, page)

    def test_output_rejects_escape_and_linked_parent(self):
        outside = self.root.parent / "outside.html"
        self.assert_story_error("path_escape", story.workbench.export, self.book, "../outside.html")
        self.assert_story_error("path_escape", story.workbench.export, self.book, outside)
        self.assertFalse(outside.exists())

        external = self.root.parent / "external-workbench"
        external.mkdir()
        linked = self.root / ".story/workbench"
        try:
            linked.symlink_to(external, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("Directory link creation is unavailable")
            environment = dict(os.environ, STORY_WORKBENCH_LINK=str(linked), STORY_WORKBENCH_TARGET=str(external))
            made = subprocess.run([
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "New-Item -ItemType Junction -Path $env:STORY_WORKBENCH_LINK -Target "
                "$env:STORY_WORKBENCH_TARGET -ErrorAction Stop | Out-Null",
            ], env=environment, capture_output=True,
               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if made.returncode:
                self.skipTest("Neither symlink nor Windows junction creation is available")
        with self.assertRaises(story.StoryError) as caught:
            story.workbench.export(self.book)
        self.assertIn(caught.exception.code, ("linked_path", "path_escape", "safe_export_unavailable"))
        self.assertFalse((external / "index.html").exists())

    def test_invalid_metadata_is_rejected_instead_of_rendered(self):
        self.book.db.execute("UPDATE meta SET value=? WHERE key='revision'",
                             ('\"<img src=x onerror=alert(1)>\"',))
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)

    def test_malformed_schema_json_and_missing_table_are_state_corrupt(self):
        self.book.db.execute("UPDATE meta SET value=? WHERE key='schema'", ("not-json",))
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)
        process, packet = self.cli("workbench-snapshot")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(packet["error"], "state_corrupt", packet)

        self.book.db.execute("UPDATE meta SET value=? WHERE key='schema'",
                             (story.dumps(story.SCHEMA_VERSION),))
        self.book.db.execute("ALTER TABLE world_entities RENAME TO broken_world_entities")
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)

    def test_oversized_integer_metadata_is_rejected_without_a_traceback(self):
        self.book.db.execute("UPDATE meta SET value=? WHERE key='last_chapter'",
                             (str(10 ** 100),))
        self.book.db.commit()
        self.assert_story_error("state_corrupt", story.workbench.snapshot, self.book)

    def test_export_rejects_a_git_tracked_derived_page(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        target = self.root / ".story/workbench/index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("old tracked workbench", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", "--",
                        ".story/workbench/index.html"], check=True)
        self.assert_story_error("workbench_tracked_output", story.workbench.export, self.book)
        self.assertEqual(target.read_text(encoding="utf-8"), "old tracked workbench")

    def test_git_book_requires_ignore_rule_before_export(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.assert_story_error("workbench_not_ignored", story.workbench.export, self.book)
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n/.story/workbench/\n")
        result = story.workbench.export(self.book)
        self.assertEqual(result["git_ignore_status"], "ignored")
        self.assertTrue(Path(result["path"]).is_file())

    def test_git_rule_for_only_index_is_insufficient_for_backups(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n/.story/workbench/index.html\n")
        self.assert_story_error("workbench_not_ignored", story.workbench.export, self.book)

    def test_git_probe_only_rules_do_not_masquerade_as_output_ignores(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        exclude = self.root / ".git/info/exclude"
        with exclude.open("a", encoding="utf-8") as stream:
            stream.write("\n/.story/workbench/.story-ignore-probe\n")
            stream.write("/.story/workbench/.backups/.story-ignore-probe\n")
        self.assert_story_error("workbench_not_ignored", story.workbench.export, self.book)

    def test_git_verification_failure_in_a_repository_fails_closed(self):
        if subprocess.run(["git", "--version"], capture_output=True).returncode:
            self.skipTest("Git is unavailable")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        with patch.object(story.workbench.subprocess, "run", side_effect=FileNotFoundError("no git")):
            self.assert_story_error("workbench_git_check_failed", story.workbench.export, self.book)

    def test_open_browser_receipt_does_not_overstate_failure(self):
        with patch.object(story.workbench.webbrowser, "open", return_value=True):
            opened = story.workbench.export(self.book, open_browser=True)
        self.assertTrue(opened["opened"])
        self.assertIsNone(opened["open_error"])

        with patch.object(story.workbench.webbrowser, "open", side_effect=OSError("no browser")):
            failed = story.workbench.export(self.book, open_browser=True)
        self.assertFalse(failed["opened"])
        self.assertIn("no browser", failed["open_error"])
        self.assertTrue(Path(failed["path"]).is_file())

    def test_cli_snapshot_without_publishing_record_is_read_only(self):
        other = self.root.parent / "无发布记录"
        story.Book.create(other, "无发布记录", "long")
        state = other / ".story/state.sqlite3"
        before = (state.read_bytes(), state.stat().st_mtime_ns)
        ledger = other / ".story/publishing.sqlite3"
        process = subprocess.run(
            [sys.executable, "-B", str(TOOL), "workbench-snapshot", "--book", str(other)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        packet = json.loads(process.stdout)
        self.assertFalse(packet["publish"]["record_exists"])
        self.assertEqual((state.read_bytes(), state.stat().st_mtime_ns), before)
        self.assertFalse(ledger.exists())

    def test_core_change_during_final_publishing_capture_is_retried(self):
        original = story.workbench._publishing_capture
        captures = 0
        changed_revision = None

        def capture(book, limit):
            nonlocal captures, changed_revision
            captures += 1
            if captures == 2:
                concurrent = story.Book(self.root)
                try:
                    concurrent.save_notes([{
                        "id": "concurrent-change", "kind": "hook",
                        "text": "发布摘要读取期间写入的有效状态变化。", "source": "测试",
                        "tags": [], "critical": False, "status": "active", "due": 3,
                    }], concurrent.meta("revision"))
                    changed_revision = concurrent.meta("revision")
                finally:
                    concurrent.close()
            return original(book, limit)

        with patch.object(story.workbench, "_publishing_capture", side_effect=capture):
            packet = story.workbench.snapshot(self.book)

        self.assertGreaterEqual(captures, 4)
        self.assertIsNotNone(changed_revision)
        self.assertEqual(packet["book"]["revision"], changed_revision)
        self.assertEqual(packet["snapshot"]["core_revision"], changed_revision)

    def test_internal_scans_have_hard_limits(self):
        with patch.object(story.workbench, "MAX_MANAGED_ARTIFACTS", 1):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)
        with patch.object(story.workbench, "MAX_PUBLISH_PLANS", 0):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)

        story.publish.export_material(self.book, self.publish_plan["id"])
        with patch.object(story.workbench, "MAX_PUBLISH_RECEIPTS", 0):
            self.assert_story_error("workbench_scan_limit", story.workbench.snapshot, self.book)

    @unittest.skipIf(os.name == "nt", "Windows holds the open database path against replacement")
    def test_read_only_book_detects_state_path_replacement(self):
        self.book.close()
        readonly = story.workbench._ReadOnlyBook(self.root)
        state = self.root / ".story/state.sqlite3"
        displaced = self.root / ".story/state-before-replacement.sqlite3"
        raw = state.read_bytes()
        state.replace(displaced)
        state.write_bytes(raw)
        try:
            self.assert_story_error("workbench_changed", story.workbench.snapshot, readonly)
        finally:
            readonly.close()

    def test_successful_refresh_preserves_previous_complete_page(self):
        first = story.workbench.export(self.book)
        previous = Path(first["path"]).read_bytes()
        self.book.save_notes([{
            "id": "refresh-change", "kind": "hook", "text": "刷新后显示的新事项。",
            "source": "测试", "tags": [], "critical": True, "status": "active", "due": 3,
        }], self.book.meta("revision"))
        second = story.workbench.export(self.book)
        self.assertIsNotNone(second["backup"])
        self.assertEqual(Path(second["backup"]).read_bytes(), previous)
        self.assertNotEqual(Path(second["path"]).read_bytes(), previous)

    def test_atomic_failure_preserves_previous_complete_page(self):
        first = story.workbench.export(self.book)
        target = Path(first["path"])
        previous = target.read_bytes()
        self.book.save_notes([{
            "id": "new-attention", "kind": "hook", "text": "等待作者决定账本交给谁。",
            "source": "作者明确设定", "tags": ["账本"], "critical": True,
            "status": "active", "due": 2,
        }], self.book.meta("revision"))

        original_publish = story._publish_no_replace

        def interrupt_new_page(source, destination):
            source_name = getattr(source, "name", Path(str(source)).name)
            if str(source_name).startswith(".story-tmp-") and Path(str(destination)).name == "index.html":
                raise OSError("simulated interrupted workbench publication")
            return original_publish(source, destination)

        with patch.object(story, "_publish_no_replace", side_effect=interrupt_new_page):
            error = self.assert_story_error("export_io", story.workbench.export, self.book)
        self.assertEqual(Path(error.details["path"]), target)
        self.assertEqual(target.read_bytes(), previous)
        self.assertEqual(list(target.parent.glob(".story-tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
