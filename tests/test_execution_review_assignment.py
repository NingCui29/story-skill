"""Synthetic Codex CLI event-shape tests, not evidence of a real review run."""
import copy
import json
import unittest

import test_execution_review as base


review = base.review


class ReviewAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.f = base.ExecutionReviewTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.receiver = "reviewer-thread"
        self.quote = "独立意见：结尾的回应缺少动作依据。"
        self.input = {"path": "candidate.md", "sha256": self.f.digest(self.f.workspace / "candidate.md")}
        self.packet = {"schema": 1, "assignment_id": "review-1", "task": "review", "input": self.input}

    def dispatch(self, **updates):
        item = {"id": "dispatch-1", "type": "collab_tool_call", "tool": "spawn_agent", "status": "completed",
                "sender_thread_id": "trial-thread", "receiver_thread_ids": [self.receiver],
                "prompt": "请审查指定稿件。\n" + review.REVIEW_ASSIGNMENT + json.dumps(self.packet, ensure_ascii=False),
                "agents_states": {self.receiver: {"status": "running", "message": None}}}
        item.update(updates)
        return {"type": "item.completed", "item": item}

    def returned(self, **updates):
        receipt = {"schema": 1, "assignment_id": self.packet["assignment_id"], "input_sha256": self.input["sha256"]}
        item = {"id": "return-1", "type": "collab_tool_call", "tool": "wait", "status": "completed",
                "sender_thread_id": "trial-thread", "receiver_thread_ids": [self.receiver], "prompt": None,
                "agents_states": {self.receiver: {"status": "completed", "message": review.REVIEW_RETURN + json.dumps(receipt) + "\n" + self.quote}}}
        item.update(updates)
        return {"type": "item.completed", "item": item}

    def configure(self, events=None, dispatch_line=4, return_line=5):
        self.f.trace_events([self.f.command(), *(events or [self.dispatch(), self.returned()])])
        self.f.check_execution()
        self.claim = {"id": "review-current", "kind": "independent_review", "status": "observed",
                      "reason": "关联被分派的具体文件版本与另一代理返回；不认证判断质量。",
                      "review_assignment": {"schema": 1, "receiver_thread_id": self.receiver, "input": copy.deepcopy(self.input),
                          "dispatch": self.f.trace_ref(line=dispatch_line, quote=review.REVIEW_ASSIGNMENT)},
                      "evidence": [self.f.trace_ref(line=return_line, quote=self.quote)]}
        self.f.record["claims"].append(self.claim)

    def assert_unverified(self, expected_code=2):
        result, code = self.f.run_review()
        claim = result["claims"][-1]
        self.assertEqual((code, claim["status"]), (expected_code, "unverified"))
        return result, claim

    def test_native_dispatch_and_return_link_specific_input_version(self):
        self.configure()
        result, code = self.f.run_review()
        proof = result["claims"][-1]["evidence"][0]["review_assignment"]
        self.assertEqual((code, proof["status"], proof["input"]["sha256"]), (0, "observed", self.input["sha256"]))
        self.assertEqual((proof["dispatch_line"], proof["return_line"], proof["receiver_thread_id"]), (4, 5, self.receiver))
        self.assertIn("actual reading", proof["scope"])
        self.assertIn("Semantic correctness of review judgements", result["unverified_scope"])

    def test_native_send_input_can_assign_an_existing_agent(self):
        self.configure([self.dispatch(tool="send_input"), self.returned()])
        result, code = self.f.run_review()
        self.assertEqual((code, result["claims"][-1]["status"]), (0, "observed"))

    def test_wrong_agent_return_cannot_link(self):
        other = "different-agent"
        returned = self.returned(receiver_thread_ids=[other], agents_states={other: self.returned()["item"]["agents_states"][self.receiver]})
        self.configure([self.dispatch(), returned])
        _, claim = self.assert_unverified()
        self.assertTrue(claim["evidence"][0]["returned_other_agent"])

    def test_self_review_is_not_mislabelled_as_independent(self):
        self.receiver = "trial-thread"
        self.configure()
        self.assert_unverified()

    def test_wrong_input_file_or_digest_in_dispatch_is_unverified(self):
        for change in ({"path": "different.md"}, {"sha256": "0" * 64}):
            with self.subTest(change=change):
                original = copy.deepcopy(self.packet)
                self.packet["input"] = {**self.input, **change}
                self.configure()
                self.assert_unverified()
                self.f.record["claims"].pop()
                (self.f.root / "review-check.json").unlink()
                self.packet = original

    def test_input_change_invalidates_even_a_matching_captured_return(self):
        self.configure()
        (self.f.workspace / "candidate.md").write_text("这是审稿返回以后的新稿。\n", encoding="utf-8")
        _, claim = self.assert_unverified(expected_code=1)
        self.assertEqual(claim["evidence_status"], "failed")
        self.assertTrue(any("Stale assigned review input" in issue for issue in claim["issues"]))

    def test_matching_model_record_cannot_fill_missing_native_prompt(self):
        self.configure([self.dispatch(prompt=None), self.returned()])
        self.assert_unverified()

    def test_failed_or_uncompleted_dispatch_does_not_link(self):
        self.configure([self.dispatch(status="failed"), self.returned()])
        self.assert_unverified()

    def test_only_dispatch_or_completion_statement_does_not_link(self):
        self.configure([self.dispatch(), {"type": "item.completed", "item": {"id": "claim-1", "type": "agent_message", "text": self.quote}}])
        self.assert_unverified()

    def test_dispatch_without_a_bound_return_receipt_is_unverified(self):
        self.configure([self.dispatch(), self.returned(agents_states={self.receiver: {"status": "completed", "message": self.quote}})])
        self.assert_unverified()

    def test_return_from_another_assignment_or_draft_is_unverified(self):
        for update in ({"assignment_id": "unrelated-task"}, {"input_sha256": "0" * 64}):
            with self.subTest(update=update):
                receipt = {"schema": 1, "assignment_id": "review-1", "input_sha256": self.input["sha256"], **update}
                returned = self.returned(agents_states={self.receiver: {"status": "completed", "message": review.REVIEW_RETURN + json.dumps(receipt) + "\n" + self.quote}})
                self.configure([self.dispatch(), returned])
                self.assert_unverified()
                self.f.record["claims"].pop()
                (self.f.root / "review-check.json").unlink()

    def test_intervening_task_to_same_receiver_makes_old_return_ambiguous(self):
        later = self.dispatch(id="dispatch-2", tool="send_input", prompt="请检查另一个任务。")
        self.configure([self.dispatch(), later, self.returned()], return_line=6)
        _, claim = self.assert_unverified()
        self.assertIn("intervened", claim["issues"][0])

    def test_reused_assignment_id_cannot_relabel_a_cached_return(self):
        earlier = self.dispatch(id="dispatch-old")
        self.configure([earlier, self.dispatch(), self.returned()], dispatch_line=5, return_line=6)
        _, claim = self.assert_unverified()
        self.assertIn("reused", claim["issues"][0])

    def test_quote_from_other_agent_in_same_wait_cannot_support_receiver(self):
        assigned = self.returned()["item"]["agents_states"][self.receiver]
        assigned["message"] = assigned["message"].replace(self.quote, "另一条判断。")
        returned = self.returned(receiver_thread_ids=[self.receiver, "unrelated-agent"], agents_states={self.receiver: assigned,
            "unrelated-agent": {"status": "completed", "message": self.quote}})
        self.configure([self.dispatch(), returned])
        self.assert_unverified()

    def test_bad_dispatch_reference_is_reported_as_failed_integrity(self):
        self.configure()
        self.claim["review_assignment"]["dispatch"]["sha256"] = "0" * 64
        self.assert_unverified(expected_code=1)

    def test_dispatch_after_return_cannot_link(self):
        self.configure([self.returned(), self.dispatch()], dispatch_line=5, return_line=4)
        self.assert_unverified()

    def test_nonparent_dispatch_cannot_be_attributed_to_captured_task(self):
        self.configure([self.dispatch(sender_thread_id="unrelated-parent"), self.returned()])
        self.assert_unverified()

    def test_receipt_alone_is_not_a_cited_opinion(self):
        returned = self.returned()
        returned["item"]["agents_states"][self.receiver]["message"] = returned["item"]["agents_states"][self.receiver]["message"].splitlines()[0]
        self.configure([self.dispatch(), returned])
        self.claim["evidence"][0]["quote"] = review.REVIEW_RETURN.strip()
        self.assert_unverified()

    def test_input_reference_cannot_escape_workspace(self):
        self.input["path"] = "../outside.md"
        self.configure()
        self.assert_unverified(expected_code=1)


if __name__ == "__main__":
    unittest.main()
