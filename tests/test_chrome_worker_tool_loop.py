"""Exercise the exact public tool wrapper emitted for an owned Chrome worker."""
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/story-skill/scripts"))
import story_chrome_worker as worker


class ChromeToolLoopTests(unittest.TestCase):
    def run_wrapper(self, replies):
        config = Path('Chrome 空间/config-"quoted".json').resolve()
        prompt = worker._worker_prompt(Path('worker module.mjs').resolve(), config)
        match = re.search(r"^\x60{3}javascript\n(.+?)\n\x60{3}$", prompt, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "missing runnable host loop in worker instructions")
        harness = """
const fs = require('node:fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [], outputs = [];
const tools = {mcp__node_repl__js: async ({code, timeout_ms}) => {
  calls.push({first:code.includes('await import('), slice:code.includes('runOwnedChromeWorkerSlice('),
    config:code.includes(JSON.stringify(input.config)), timeout_ms});
  const reply = input.replies[calls.length - 1];
  if (!reply) throw Error('unexpected additional call');
  if (reply === 'throw') throw Error('synthetic tool failure');
  if ('content' in reply || reply.isError) return reply;
  return {content:[{type:'text', text:input.prefix + JSON.stringify(reply)}]};
}};
const AsyncFunction = (async function() {}).constructor;
(async () => {
  let error = null;
  try { await new AsyncFunction('tools','text',input.loop)(tools, value=>outputs.push(value)); }
  catch (e) { error = String(e.message); }
  process.stdout.write(JSON.stringify({calls, outputs, error}));
})();
"""
        data = {"loop": match.group(1), "config": str(config), "replies": replies, "prefix": worker.CHECKPOINT_PREFIX}
        result = subprocess.run(["node", "-e", harness], input=json.dumps(data), text=True,
                                capture_output=True, timeout=12, check=True)
        return json.loads(result.stdout)

    def test_busy_directory_runs_across_many_checkpoints_in_one_wrapper_call(self):
        busy = {"done": False, "state": "busy", "phase": "queue_operation", "processed": 1}
        ready = {"done": False, "state": "ready", "phase": "queue_wait", "processed": 2}
        stopped = {"done": True, "state": "stopped", "phase": "cleanup", "processed": 2}
        result = self.run_wrapper([busy] * 36 + [ready, stopped])
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["calls"]), 38)
        self.assertEqual([call["first"] for call in result["calls"]], [True] + [False] * 37)
        self.assertTrue(all(call["slice"] and call["config"] and call["timeout_ms"] == 240000
                            for call in result["calls"]))
        self.assertEqual(len(result["outputs"]), 1)
        self.assertEqual(json.loads(result["outputs"][0].removeprefix(worker.CHECKPOINT_PREFIX)), stopped)

    def test_terminal_worker_failure_stops_and_keeps_the_original_result(self):
        failed = {"done": True, "state": "failed", "phase": "cleanup", "processed": 0,
                  "code": "platform_chrome_worker_host_access_denied"}
        result = self.run_wrapper([failed])
        self.assertEqual(len(result["calls"]), 1)
        self.assertIsNone(result["error"])
        self.assertEqual(json.loads(result["outputs"][0].removeprefix(worker.CHECKPOINT_PREFIX)), failed)

    def test_terminal_summary_excludes_extraneous_tool_text(self):
        stopped = {"done": True, "state": "stopped", "phase": "cleanup", "processed": 1}
        result = self.run_wrapper([{**stopped, "extra": "untrusted text"}])
        self.assertEqual(len(result["calls"]), 1)
        self.assertIsNone(result["error"])
        self.assertEqual(json.loads(result["outputs"][0].removeprefix(worker.CHECKPOINT_PREFIX)), stopped)

    def test_config_rejection_before_worker_start_keeps_its_cause(self):
        config_failure = {"done": True, "state": "failed", "failure_phase": "config_validated",
                          "code": "platform_chrome_worker_invalid_config", "processed": 0}
        result = self.run_wrapper([config_failure])
        self.assertIsNone(result["error"])
        self.assertEqual(json.loads(result["outputs"][0].removeprefix(worker.CHECKPOINT_PREFIX)), config_failure)

    def test_invalid_or_failed_tool_result_never_starts_another_call(self):
        for bad in ("throw", {"isError": True}, {"content": []},
                    {"content": [{"type": "text", "text": worker.CHECKPOINT_PREFIX + "not JSON"}]},
                    {"done": "false", "state": "busy", "phase": "queue_operation", "processed": 1},
                    {"done": False, "state": "failed", "phase": "cleanup", "processed": 1},
                    {"done": False, "state": "busy", "phase": "unexpected", "processed": 1}):
            with self.subTest(bad=bad):
                result = self.run_wrapper([bad])
                self.assertEqual(len(result["calls"]), 1)
                self.assertTrue(result["error"])
                self.assertEqual(result["outputs"], [])
