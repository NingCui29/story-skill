import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
import urllib.error

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("package_sync_test", SCRIPTS / "sync_packages.py")
    sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync)
finally:
    sys.path.remove(str(SCRIPTS))


class PackageSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="story-sync-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {
            "NODE_AUTH_TOKEN": "fake-test-token", "GITHUB_REPOSITORY": sync.REPOSITORY}))

    def pipeline(self):
        built = {"name": sync.NAME, "version": "0.6.0", "tarball": str(self.root / "built.tgz")}
        self.stack.enter_context(patch.object(sync, "release_files", return_value=(
            {"html_url": "https://github.com/NingCui29/story-skill/releases/tag/v0.6.0"},
            self.root / "release.zip", self.root / "release.sha256")))
        self.stack.enter_context(patch.object(sync.package_npm, "build", return_value=built))
        self.registry = self.stack.enter_context(patch.object(sync, "registry_version"))
        self.npm = self.stack.enter_context(patch.object(sync, "npm_command", return_value=SimpleNamespace(
            returncode=0, stdout=json.dumps("github-actions[bot]"), stderr="")))
        self.verify = self.stack.enter_context(patch.object(sync, "verify_download", return_value=(
            self.root / "verified.tgz", {"ok": True}, "sha512-verified")))
        self.stack.enter_context(patch.object(sync, "runtime_smoke", return_value={"ok": True}))
        self.stack.enter_context(patch.object(sync, "read_json", return_value={
            "html_url": "https://github.com/users/NingCui29/packages/npm/package/story-skill",
            "visibility": "public", "repository": {"full_name": sync.REPOSITORY}}))
        self.stack.enter_context(patch.object(sync.time, "sleep"))

    def test_redirect_removes_registry_credential_from_signed_asset_host(self):
        requests = []

        def opened(request, **kwargs):
            requests.append(request)
            if len(requests) == 1:
                raise urllib.error.HTTPError(request.full_url, 302, "redirect", {
                    "Location": "https://release-assets.githubusercontent.com/package.tgz?signature=test"}, None)
            return io.BytesIO(b"verified bytes")

        opener = SimpleNamespace(open=opened)
        with patch.object(sync.urllib.request, "build_opener", return_value=opener):
            raw = sync.read_url("https://npm.pkg.github.com/download/test", "fake", "npm.pkg.github.com")
        self.assertEqual(raw, b"verified bytes")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer fake")
        self.assertIsNone(requests[1].get_header("Authorization"))

    def test_invalid_tag_stops_before_network_or_build(self):
        with patch.object(sync, "release_files") as fetch:
            with self.assertRaises(ValueError):
                sync.sync("v0.6.0; echo bad", self.root)
        fetch.assert_not_called()

    def test_prepare_only_never_reads_or_writes_registry(self):
        self.pipeline()
        with patch.dict(os.environ, {"NODE_AUTH_TOKEN": ""}):
            result = sync.sync("v0.6.0", self.root, prepare_only=True)
        self.assertTrue(result["ok"])
        self.npm.assert_not_called()
        self.registry.assert_not_called()

    def test_prior_release_family_is_rejected_before_network(self):
        with patch.object(sync, "release_files") as fetch, patch.object(sync, "npm_command") as npm:
            with self.assertRaisesRegex(ValueError, "no reviewed payload layout"):
                sync.sync("v0.5.11", self.root)
        fetch.assert_not_called()
        npm.assert_not_called()


    def test_foreign_repository_stops_before_network_or_publication(self):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/story-skill"}), patch.object(sync, "release_files") as fetch:
            with self.assertRaisesRegex(ValueError, "restricted"):
                sync.sync("v0.6.0", self.root)
        fetch.assert_not_called()

    def test_registry_query_uses_current_scope_and_host_bound_credential(self):
        with patch.object(sync, "read_json", return_value={"versions": {"0.6.0": {"version": "0.6.0"}}}) as read:
            self.assertEqual(sync.registry_version("0.6.0", "test-token"), {"version": "0.6.0"})
        read.assert_called_once_with("https://npm.pkg.github.com/@ningcui29%2fstory-skill", "test-token", "npm.pkg.github.com")

    def test_current_download_rejects_wrong_scope_or_repository_before_fetching_tarball(self):
        built = {"version": "0.6.0"}
        valid = {"name": "@ningcui29/story-skill", "version": "0.6.0",
                 "repository": {"url": "https://github.com/NingCui29/story-skill.git"}}
        cases = [{**valid, "name": "@other/story-skill"},
                 {**valid, "repository": {"url": "https://github.com/Other/story-skill.git"}}]
        for metadata in cases:
            with self.subTest(metadata=metadata), patch.object(sync, "read_url") as read:
                with self.assertRaises(ValueError):
                    sync.verify_download(metadata, built, self.root / "release.zip", self.root / "checksum", self.root, "test-token")
                read.assert_not_called()

    def test_wrong_built_identity_stops_before_registry_access(self):
        self.pipeline()
        for wrong in ({"name": "@other/story-skill", "version": "0.6.0"},
                      {"name": sync.NAME, "version": "0.6.1"}):
            with self.subTest(wrong=wrong), patch.object(sync.package_npm, "build", return_value=wrong):
                with self.assertRaisesRegex(ValueError, "disagree"):
                    sync.sync("v0.6.0", self.root)
        self.npm.assert_not_called()
        self.registry.assert_not_called()

    def test_failed_authentication_never_treats_package_as_absent(self):
        self.pipeline()
        self.npm.return_value = SimpleNamespace(returncode=1, stdout="", stderr="401")
        with self.assertRaisesRegex(ValueError, "authentication"):
            sync.sync("v0.6.0", self.root)
        self.registry.assert_not_called()
        self.assertEqual(len(self.npm.call_args_list), 1)

    def test_existing_matching_version_is_verified_without_republishing(self):
        self.pipeline()
        self.registry.return_value = {"name": sync.NAME, "version": "0.6.0"}
        result = sync.sync("v0.6.0", self.root)
        self.assertTrue(result["already_published"])
        self.assertTrue(result["ok"])
        self.verify.assert_called_once()
        self.assertEqual([c.args[0][0] for c in self.npm.call_args_list], ["whoami"])

    def test_existing_different_payload_is_never_overwritten(self):
        self.pipeline()
        self.registry.return_value = {"name": sync.NAME, "version": "0.6.0"}
        self.verify.side_effect = ValueError("payload mismatch")
        with self.assertRaisesRegex(ValueError, "payload mismatch"):
            sync.sync("v0.6.0", self.root)
        self.assertEqual([c.args[0][0] for c in self.npm.call_args_list], ["whoami"])

    def test_uncertain_publish_reads_back_without_a_second_publish(self):
        self.pipeline()
        self.registry.side_effect = [None, {"name": sync.NAME, "version": "0.6.0"}]
        self.npm.side_effect = [SimpleNamespace(returncode=0, stdout='"github-actions[bot]"', stderr=""),
                               SimpleNamespace(returncode=1, stdout="", stderr="response lost")]
        result = sync.sync("v0.6.0", self.root)
        self.assertTrue(result["ok"])
        self.assertFalse(result["already_published"])
        self.assertEqual([c.args[0][0] for c in self.npm.call_args_list], ["whoami", "publish"])

    def test_publish_timeout_also_reads_back_without_a_second_publish(self):
        self.pipeline()
        self.registry.side_effect = [None, {"name": sync.NAME, "version": "0.6.0"}]
        self.npm.side_effect = [SimpleNamespace(returncode=0, stdout='"github-actions[bot]"', stderr=""),
                               subprocess.TimeoutExpired(["npm", "publish"], 180)]
        self.assertTrue(sync.sync("v0.6.0", self.root)["ok"])
        self.assertEqual([c.args[0][0] for c in self.npm.call_args_list], ["whoami", "publish"])

    def smoke_archive(self, version="0.6.1", names=None, changed=None):
        archive = self.root / "suite.tgz"
        root = SCRIPTS.parent / "skills"
        with tarfile.open(archive, "w:gz") as bundle:
            for name in (sync.package_npm.payload_files(version) if names is None else names):
                raw = changed if name == "story-skill-plan/SKILL.md" and changed is not None else (root / name).read_bytes()
                member = tarfile.TarInfo("package/" + name)
                member.size = len(raw)
                bundle.addfile(member, io.BytesIO(raw))
        return archive

    def test_runtime_smoke_installs_all_suite_dependencies_before_execution(self):
        archive = self.smoke_archive()
        results = [SimpleNamespace(returncode=0, stdout="0.6.1\n"), SimpleNamespace(returncode=0, stdout="help"),
                   SimpleNamespace(returncode=0, stdout="{}"), SimpleNamespace(returncode=0, stdout='{"last_chapter":0}')]

        def execute(arguments, **kwargs):
            suite = Path(arguments[4]).parents[2]
            self.assertEqual({p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file()},
                             set(sync.package_npm.payload_files("0.6.1")))
            return results.pop(0)

        with patch.object(sync.subprocess, "run", side_effect=execute):
            result = sync.runtime_smoke(archive, "0.6.1")
        self.assertEqual(result["skill_files"], 39)
        self.assertEqual(set(result["skills"]), set(sync.package_npm.SKILL_NAMES))
        self.assertTrue(result["temporary_book_removed"])

    def test_runtime_smoke_preserves_published_v060_38_file_contract(self):
        archive = self.smoke_archive("0.6.0")
        results = [SimpleNamespace(returncode=0, stdout="0.6.0\n"), SimpleNamespace(returncode=0, stdout="help"),
                   SimpleNamespace(returncode=0, stdout="{}"), SimpleNamespace(returncode=0, stdout='{"last_chapter":0}')]

        def execute(arguments, **kwargs):
            suite = Path(arguments[4]).parents[2]
            files = {p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file()}
            self.assertEqual(files, set(sync.package_npm.payload_files("0.6.0")))
            self.assertNotIn("story-skill/scripts/story_workbench.py", files)
            return results.pop(0)

        with patch.object(sync.subprocess, "run", side_effect=execute):
            result = sync.runtime_smoke(archive, "0.6.0")
        self.assertEqual(result["skill_files"], 38)


    def test_runtime_smoke_rejects_partial_suite_before_execution(self):
        archive = self.smoke_archive(names=[name for name in sync.package_npm.SUITE_FILES
                                            if name.startswith("story-skill/")])
        with patch.object(sync.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "missing required skill dependencies"):
                sync.runtime_smoke(archive, "0.6.1")
        execute.assert_not_called()

    def test_runtime_smoke_rejects_broken_sibling_link_before_execution(self):
        archive = self.smoke_archive(changed=b"[missing shared rules](../story-skill/missing.md)")
        with patch.object(sync.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "broken local dependency"):
                sync.runtime_smoke(archive, "0.6.1")
        execute.assert_not_called()



if __name__ == "__main__":
    unittest.main()
