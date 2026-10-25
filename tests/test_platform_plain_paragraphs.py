"""Platform text has no empty lines, while sources and old evidence stay intact."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import test_platform_drafts as base
from tests.test_platform_draft_sync import UpdateAdapter
from tests import test_workbench_draft_preferences as preferences

story, drafts = base.story, base.drafts
ENCODING = "plain_paragraphs_v1"


class PlainParagraphTests(unittest.TestCase):
    setUp = base.PlatformDraftTests.setUp
    execute = base.PlatformDraftTests.execute
    assert_code = base.PlatformDraftTests.assert_code

    def prepare_mixed_lines(self):
        text = self.fixture.texts[1].replace("\n守门人", "\n \t\n\n守门人") + "\n\n"
        self.fixture.commit(text=text, replace_last=True)
        self.plan = self.fixture.prepare()
        self.frozen = self.plan["manifest"]["chapters"][0]
        return drafts._encoded_content(self.frozen, ENCODING, "fanqie")

    def test_all_blank_line_shapes_are_removed_but_text_indentation_and_markers_are_literal(self):
        source = {"title": " 标题不动 ", "body": "\r\n\t \r\n　　甲  \r\n乙\r\n\r\n\r\n\u3000\n\u00a0\n  # 原文标记\n\t尾段 \n\n"}
        before = copy.deepcopy(source)
        for platform in ("fanqie", "qimao", "qidian"):
            with self.subTest(platform=platform):
                result = drafts._encoded_content(source, ENCODING, platform)
                self.assertEqual(result, {"title": source["title"], "body": "　　甲  \n乙\n  # 原文标记\n\t尾段 "})
                self.assertEqual(drafts._encoded_content(result, ENCODING, platform), result)
        self.assertEqual(source, before)

    def test_empty_body_reports_chapter_in_chinese_before_any_remote_action(self):
        with self.assertRaises(story.StoryError) as caught:
            drafts._encoded_content({"chapter": 46, "title": "标题", "body": "\n \n\t\n\u3000\n"}, ENCODING, "fanqie")
        self.assertEqual(caught.exception.code, "platform_draft_empty_body")
        self.assertIn("第46章", str(caught.exception))
        self.assertEqual(self.adapter.calls, [])

    def test_preview_create_full_readback_and_second_run_share_one_encoded_body(self):
        encoded = self.prepare_mixed_lines()
        before = self.fixture.writing_snapshot()
        preview = drafts.run_plan(self.book, self.plan["id"], body_encoding=ENCODING)
        self.assertEqual(self.adapter.calls, [])
        self.assertFalse((self.fixture.root / drafts.DIRECTORY).exists())
        result = self.execute(body_encoding=ENCODING)
        self.assertTrue(result["all_chapters_accepted_this_run"], result)
        self.assertEqual(self.adapter.remote["1"]["body"], encoded["body"])
        self.assertFalse(any(not line.strip() for line in encoded["body"].split("\n")))
        event = result["recent_events"][-1]
        self.assertEqual(event["encoded_content_sha256"], preview["chapters"][0]["encoded_content_sha256"])
        self.assertEqual(event["observed_sha256"], event["encoded_content_sha256"])
        self.assertNotEqual(event["source_content_sha256"], event["observed_sha256"])
        self.assertFalse(result["all_chapters_verified_this_run"], "Formatting success is not source-byte equality")
        self.assertTrue(self.execute(body_encoding=ENCODING)["all_chapters_accepted_this_run"])
        self.assertEqual(self.adapter.created, 1)
        self.assertEqual(self.fixture.writing_snapshot(), before)
        self.assertFalse(drafts.status(self.book, self.plan["id"])["all_chapters_accepted_this_run"])

    def test_title_and_body_sync_preserves_remote_id_and_updates_only_once(self):
        encoded = self.prepare_mixed_lines()
        self.adapter = UpdateAdapter(self.book)
        self.adapter.seed(self.frozen, title="旧章名", body="旧正文\n\n第二段")
        before = self.fixture.writing_snapshot()
        result = self.execute(body_encoding=ENCODING, sync_drafts=True)
        self.assertTrue(result["all_chapters_accepted_this_run"], result)
        self.assertEqual(result["results"][0]["remote_id"], "existing")
        self.assertEqual(self.adapter.remote["existing"]["title"], encoded["title"])
        self.assertEqual(self.adapter.remote["existing"]["body"], encoded["body"])
        self.assertTrue(self.execute(body_encoding=ENCODING, sync_drafts=True)["ok"])
        self.assertEqual((self.adapter.created, self.adapter.updated), (0, 1))
        self.assertEqual(self.fixture.writing_snapshot(), before)

    def test_markdown_blank_lines_alone_are_already_consistent_and_never_overwritten(self):
        encoded = self.prepare_mixed_lines()
        self.adapter = UpdateAdapter(self.book)
        self.adapter.seed(self.frozen, title=encoded['title'], body=encoded['body'])
        self.assertNotEqual(self.frozen['body'], encoded['body'])
        result = self.execute(body_encoding=ENCODING, comparison_policy='strict', sync_drafts=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['unchanged_chapters'], 1)
        self.assertEqual((self.adapter.created, self.adapter.updated), (0, 0))
        row = result['results'][0]
        self.assertTrue(row['title_matches'])
        self.assertTrue(row['rendered_body_matches'])
        self.assertFalse(row['body_matches'], 'Source blank lines remain in the audit evidence')
        self.assertEqual(row['observed_sha256'], row['encoded_content_sha256'])
        self.assertEqual(row['state'], 'reconciled_equivalent')

    def test_remote_blank_lines_are_a_real_conflict_not_silently_normalized(self):
        self.prepare_mixed_lines()
        self.adapter.seed(self.frozen)
        result = self.execute(body_encoding=ENCODING, reconcile_only=True)
        self.assertEqual(result["results"][0]["state"], "conflict")
        self.assertEqual(result["accepted_chapters"], 0)
        self.assertEqual(self.adapter.created, 0)

    def test_remote_terminal_newline_is_a_difference_and_cannot_select_tolerant_comparison(self):
        encoded = self.prepare_mixed_lines()
        self.adapter.seed(self.frozen, body=encoded['body'] + '\n')
        result = self.execute(body_encoding=ENCODING, reconcile_only=True)
        self.assertEqual(result['results'][0]['state'], 'conflict')
        self.assertEqual(result['accepted_chapters'], 0)
        self.adapter.calls.clear()
        for mode in ({}, {'reconcile_only': True}, {'sync_drafts': True}):
            self.assert_code('platform_draft_comparison_policy_required', self.execute,
                             body_encoding=ENCODING, comparison_policy='fanqie_terminal_lf_v1', **mode)
        self.assertEqual(self.adapter.calls, [])

    def test_lost_update_receipt_requires_readback_and_never_repeats_the_write(self):
        encoded = self.prepare_mixed_lines()
        self.adapter = UpdateAdapter(self.book)
        self.adapter.seed(self.frozen, body="旧正文")
        self.adapter.after_write_error = True
        self.assertEqual(self.execute(body_encoding=ENCODING, sync_drafts=True)["results"][0]["state"], "unknown")
        self.adapter.remote["existing"]["body"] = encoded["body"][:-1]
        self.assertFalse(self.execute(body_encoding=ENCODING, sync_drafts=True)["ok"])
        self.assertEqual(self.adapter.updated, 1)
        self.adapter.remote["existing"]["body"] = encoded["body"]
        recovered = self.execute(body_encoding=ENCODING, reconcile_only=True)
        self.assertTrue(recovered["all_chapters_accepted_this_run"], recovered)
        self.assertEqual((self.adapter.created, self.adapter.updated), (0, 1))

    def test_legacy_encoding_stays_unchanged_and_unknown_attempt_is_not_rewritten(self):
        self.adapter.fail_create = "after"
        result = self.execute(body_encoding="verbatim")
        self.assertEqual(result["results"][0]["state"], "unknown")
        intent = next(self.global_root.glob("*.intent.json"))
        before = intent.read_bytes()
        result = self.execute(body_encoding=ENCODING)
        self.assertFalse(result["ok"])
        self.assertEqual(self.adapter.created, 1)
        self.assertEqual(intent.read_bytes(), before)
        result = self.execute(body_encoding="verbatim", reconcile_only=True)
        self.assertTrue(result["all_chapters_accepted_this_run"])

    def test_plain_format_evidence_is_valid_for_other_platforms(self):
        for platform in ("qimao", "qidian"):
            with self.subTest(platform=platform):
                plan = self.fixture.prepare(platform=platform)
                self.adapter = base.FakeAdapter(self.book)
                result = self.execute(plan, body_encoding=ENCODING)
                self.assertTrue(result["all_chapters_accepted_this_run"], result)
                self.assertTrue(self.execute(plan, body_encoding=ENCODING)["all_chapters_accepted_this_run"])
                self.assertTrue(drafts.status(self.book, plan["id"])["ok"])

    def test_cli_requires_no_blank_format_for_new_writes_but_allows_legacy_readback(self):
        parser = story.parser()
        args = parser.parse_args(["publish-draft-run", "--book", str(self.fixture.root), "--id", self.plan["id"]])
        self.assertEqual(args.body_encoding, ENCODING)
        packet = drafts.run(self.book, args)
        self.assertEqual(packet["body_encoding"], ENCODING)
        for legacy in ("verbatim", "markdown_paragraphs_v1", "markdown_paragraphs_v2"):
            args.body_encoding = legacy
            self.assert_code("platform_draft_body_encoding_required", drafts.run, self.book, args)
        args.body_encoding = "verbatim"
        args.reconcile_only = True
        self.assertEqual(drafts.run(self.book, args)["body_encoding"], "verbatim")
        self.assertEqual(self.adapter.calls, [])


class PlainParagraphHTTPTests(unittest.TestCase):
    setUp = preferences.DraftPreferenceStorageTests.setUp
    start_server = preferences.DraftPreferenceStorageTests.start_server
    stop_server = preferences.DraftPreferenceStorageTests.stop_server
    post = preferences.DraftPreferenceStorageTests.post

    def test_http_preview_uses_forced_encoding_and_rejects_stale_write_clients(self):
        server, thread = self.start_server()
        try:
            payload = {"plan_id": self.plan["id"], "profile_id": self.profile.name}
            status, packet = self.post(server, "preview", payload)
            self.assertEqual(status, 200, packet)
            self.assertEqual(packet["body_encoding"], ENCODING)
            with patch.object(story.platform_drafts, "workbench_start") as start:
                for old in ("verbatim", "markdown_paragraphs_v1", "markdown_paragraphs_v2"):
                    status, packet = self.post(server, "start", {**payload, "body_encoding": old, "execute": True})
                    self.assertEqual(status, 409, packet)
                    self.assertEqual(packet["error"], "platform_draft_body_encoding_required")
                start.assert_not_called()
            for action in ('preview', 'start'):
                with patch.object(story.platform_drafts, 'workbench_' + action) as run:
                    status, packet = self.post(server, action, {**payload,
                        'comparison_policy': 'fanqie_terminal_lf_v1', 'body_encoding': ENCODING})
                    self.assertEqual(status, 409, packet)
                    self.assertEqual(packet['error'], 'platform_draft_comparison_policy_required')
                    run.assert_not_called()
        finally:
            self.stop_server(server, thread)
