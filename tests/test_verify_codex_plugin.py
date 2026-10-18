import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_codex_plugin", ROOT / "scripts/verify_codex_plugin.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class CodexPluginEvidenceTests(unittest.TestCase):
    def test_hash_comparison_detects_changed_missing_and_extra_files(self):
        result = verify.compare_files({"same": "a", "changed": "a", "missing": "a"},
                                      {"same": "a", "changed": "b", "extra": "a"})
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["changed"], ["changed"])
        self.assertEqual(result["missing"], ["missing"])
        self.assertEqual(result["extra"], ["extra"])

    def test_dependencies_include_sibling_skills_and_reference_links(self):
        with tempfile.TemporaryDirectory(prefix="story-plugin-links-") as temporary:
            root = Path(temporary).resolve()
            first = root / "skills/first"
            second = root / "skills/second"
            first.mkdir(parents=True)
            second.mkdir()
            (second / "SKILL.md").write_text("# Other skill\n", encoding="utf-8")
            (first / "SKILL.md").write_text(
                "[sibling](../second/SKILL.md#anchor)\n[unknown][ref]\n"
                "[ref]: missing.md\n[external](https://example.com/missing.md)\n"
                "`[sample](code-only.md)`\n", encoding="utf-8")
            result = verify.local_markdown_links(root)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["links_checked"], 2)
            self.assertEqual(result["missing"], [{"source": "skills/first/SKILL.md", "target": "missing.md"}])
            self.assertEqual(result["outside_plugin"], [])

    def test_existing_dependency_outside_plugin_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="story-plugin-escape-") as temporary:
            root = Path(temporary).resolve() / "plugin"
            skill = root / "skills/first"
            skill.mkdir(parents=True)
            (root.parent / "outside.md").write_text("must not be a plugin dependency", encoding="utf-8")
            (skill / "SKILL.md").write_text("[outside](../../../outside.md)\n", encoding="utf-8")
            result = verify.local_markdown_links(root)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["missing"], [])
            self.assertEqual(len(result["outside_plugin"]), 1)

    def test_inventory_preserves_all_plugin_files_and_rejects_links(self):
        with tempfile.TemporaryDirectory(prefix="story-plugin-inventory-") as temporary:
            root = Path(temporary).resolve()
            path = root / "plugin.json"
            path.write_bytes(b"{}")
            (root / "__pycache__").mkdir()
            (root / "__pycache__/module.pyc").write_bytes(b"unexpected plugin mutation")
            actual = verify.inventory(root)
            self.assertEqual(actual["plugin.json"], hashlib.sha256(b"{}").hexdigest())
            self.assertIn("__pycache__/module.pyc", actual)
            self.assertNotIn("__pycache__/module.pyc", verify.inventory(root, source=True))
            try:
                (root / "linked").symlink_to(path)
            except OSError as error:
                self.skipTest(f"Native symlinks unavailable: {error}")
            with self.assertRaisesRegex(ValueError, "Linked plugin content"):
                verify.inventory(root)

    def test_installed_plugin_requires_one_own_cache_manifest(self):
        with tempfile.TemporaryDirectory(prefix="story-plugin-cache-") as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(verify.VerificationFailure, "found 0"):
                verify.installed_plugin(root)
            manifests = []
            for version in ("1", "2"):
                manifest = root / f"plugins/cache/local/story-skill/{version}/.codex-plugin/plugin.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text('{"name":"story-skill"}', encoding="utf-8")
                manifests.append(manifest)
            with self.assertRaisesRegex(verify.VerificationFailure, "found 2"):
                verify.installed_plugin(root)
            manifests[1].unlink()
            self.assertEqual(verify.installed_plugin(root), manifests[0].parent.parent)

    def test_failed_report_preserves_existing_success_and_rejects_redirect(self):
        with tempfile.TemporaryDirectory(prefix="story-plugin-report-") as temporary:
            root = Path(temporary).resolve()
            report = root / "verification.json"
            verify.write_report({"ok": True}, report)
            verify.write_report({"ok": False}, report)
            self.assertTrue(json.loads(report.read_text())["ok"])
            alternate = root / "verification.failed.json"
            self.assertFalse(json.loads(alternate.read_text())["ok"])
            alternate.unlink()
            external = root / "external.json"
            external.write_text("preserved", encoding="utf-8")
            try:
                alternate.symlink_to(external)
            except OSError as error:
                self.skipTest(f"Native symlinks unavailable: {error}")
            with self.assertRaisesRegex(ValueError, "linked report output"):
                verify.write_report({"ok": False}, report)
            self.assertEqual(external.read_text(), "preserved")

    def test_unavailable_codex_returns_failure_without_starting_any_process(self):
        with tempfile.TemporaryDirectory(prefix="story-plugin-unavailable-") as temporary:
            root = Path(temporary).resolve()
            plugin = root / "plugins/story-skill/skills"
            plugin.mkdir(parents=True)
            with patch.object(verify, "ROOT", root / "plugins/story-skill"), \
                    patch.object(verify, "expected_source_payload", return_value=({}, [])), \
                    patch.object(verify.shutil, "which", return_value=None), \
                    patch.object(verify, "run") as run:
                report = verify.verify(root)
            self.assertFalse(report["ok"])
            self.assertIn("Codex CLI is unavailable", report["error"])
            self.assertFalse(report["isolation"]["model_turn_started"])
            run.assert_not_called()

    def test_ui_prompt_requires_its_discovered_namespaced_skill(self):
        item = {"name": "story-skill:story-skill-plan", "interface": {
            "defaultPrompt": "使用 $story-skill:story-skill-plan 规划一本书。"}}
        result = verify.validate_ui_prompts([item])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["references"][item["name"]], ["$story-skill:story-skill-plan"])
        for prompt in ("使用 $story-skill-plan 规划一本书。",
                       "使用 $story-skill:story-skill-write 规划一本书。",
                       "使用 $story-skill:story-skill-plan，然后调用 $story-skill。", ""):
            with self.subTest(prompt=prompt):
                item["interface"]["defaultPrompt"] = prompt
                with self.assertRaises(verify.VerificationFailure):
                    verify.validate_ui_prompts([item])

    def test_source_comparison_only_accepts_the_eight_ui_adaptations(self):
        source, adapted = verify.expected_source_payload()
        self.assertEqual(len(source), 52)
        self.assertEqual(adapted, sorted(f"{name}/agents/openai.yaml" for name in verify.SKILL_NAMES))
        for name in source:
            if name not in adapted:
                self.assertEqual(source[name], verify.digest(ROOT / "skills" / name))


if __name__ == "__main__":
    unittest.main()
