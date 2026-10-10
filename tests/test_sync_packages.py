import importlib.util
import hashlib
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

    def release_fixture(self):
        payloads = {"story-skill-0.6.15.zip": b"published release bytes"}
        payloads["story-skill-0.6.15.zip.sha256"] = (
            hashlib.sha256(payloads["story-skill-0.6.15.zip"]).hexdigest()
            + "  story-skill-0.6.15.zip\n").encode()
        assets = [{"id": index, "name": name, "state": "uploaded", "size": len(raw),
                   "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                   "url": sync.API + f"/releases/assets/{index}",
                   "browser_download_url": f"https://github.com/{sync.REPOSITORY}/releases/download/v0.6.15/{name}"}
                  for index, (name, raw) in enumerate(payloads.items(), 1)]
        return {"draft": False, "prerelease": False, "tag_name": "v0.6.15", "assets": assets}, payloads

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
        self.runtime = self.stack.enter_context(patch.object(sync, "runtime_smoke", return_value={"ok": True}))
        self.metadata = self.stack.enter_context(patch.object(sync, "read_json", return_value={
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

    def test_json_api_version_header_keeps_credentials_and_version_on_api_host(self):
        for options, expected_version in (({}, "2026-03-10"),
                                           ({"api_version": "2022-11-28"}, "2022-11-28")):
            with self.subTest(api_version=expected_version):
                requests = []

                def opened(request, **kwargs):
                    requests.append(request)
                    if len(requests) == 1:
                        raise urllib.error.HTTPError(request.full_url, 302, "redirect", {
                            "Location": "https://release-assets.githubusercontent.com/metadata.json"}, None)
                    return io.BytesIO(b'{"verified":true}')

                with patch.object(sync.urllib.request, "build_opener", return_value=SimpleNamespace(open=opened)):
                    result = sync.read_json(sync.API, "test-token", "api.github.com", **options)
                self.assertEqual(result, {"verified": True})
                self.assertEqual(requests[0].get_header("X-github-api-version"), expected_version)
                self.assertEqual(requests[0].get_header("Accept"), "application/vnd.github+json")
                self.assertEqual(requests[0].get_header("Authorization"), "Bearer test-token")
                self.assertIsNone(requests[1].get_header("X-github-api-version"))
                self.assertIsNone(requests[1].get_header("Authorization"))

    def test_private_release_uses_authenticated_asset_api_and_drops_token_on_redirects(self):
        release, payloads = self.release_fixture()
        requests = []
        urls = {asset["url"]: asset for asset in release["assets"]}

        def opened(request, **kwargs):
            requests.append(request)
            if request.full_url in urls:
                asset = urls[request.full_url]
                self.assertEqual(request.get_header("Authorization"), "Bearer private-test-token")
                self.assertEqual(request.get_header("Accept"), "application/octet-stream")
                self.assertEqual(request.get_header("X-github-api-version"), "2026-03-10")
                raise urllib.error.HTTPError(request.full_url, 302, "redirect", {
                    "Location": asset["browser_download_url"]}, None)
            self.assertIsNone(request.get_header("Authorization"))
            name = request.full_url.rsplit("/", 1)[1]
            if request.full_url.startswith("https://github.com/"):
                raise urllib.error.HTTPError(request.full_url, 302, "redirect", {
                    "Location": "https://release-assets.githubusercontent.com/" + name}, None)
            return io.BytesIO(payloads[name])

        with patch.object(sync, "read_json", return_value=release), patch.object(
                sync.urllib.request, "build_opener", return_value=SimpleNamespace(open=opened)):
            _, archive, checksum = sync.release_files("v0.6.15", self.root / "release", "private-test-token")
        self.assertEqual(archive.read_bytes(), payloads[archive.name])
        self.assertEqual(checksum.read_bytes(), payloads[checksum.name])
        self.assertEqual(len(requests), 6)

    def test_public_prepare_release_keeps_anonymous_browser_downloads(self):
        release, payloads = self.release_fixture()
        requests = []

        def opened(request, **kwargs):
            requests.append(request)
            self.assertIsNone(request.get_header("Authorization"))
            self.assertTrue(request.full_url.startswith(f"https://github.com/{sync.REPOSITORY}/releases/download/v0.6.15/"))
            return io.BytesIO(payloads[request.full_url.rsplit("/", 1)[1]])

        with patch.object(sync, "read_json", return_value=release), patch.object(
                sync.urllib.request, "build_opener", return_value=SimpleNamespace(open=opened)):
            _, archive, checksum = sync.release_files("v0.6.15", self.root / "release")
        self.assertEqual(archive.read_bytes(), payloads[archive.name])
        self.assertEqual(checksum.read_bytes(), payloads[checksum.name])
        self.assertEqual(len(requests), 2)

    def test_private_release_rejects_unbound_asset_api_identity_before_download(self):
        release, _ = self.release_fixture()
        asset = release["assets"][0]
        for changed in ({"url": "https://api.github.com/repos/Other/story-skill/releases/assets/1"},
                        {"url": sync.API + "/releases/assets/2"},
                        {"url": asset["url"] + "?redirect=other"},
                        {"id": "1"}, {"id": True}, {"id": 0}):
            with self.subTest(changed=changed):
                invalid = {**release, "assets": [{**asset, **changed}, release["assets"][1]]}
                with patch.object(sync, "read_json", return_value=invalid), patch.object(sync, "read_url") as fetch:
                    with self.assertRaisesRegex(ValueError, "asset API URL"):
                        sync.release_files("v0.6.15", self.root / "release", "private-test-token")
                fetch.assert_not_called()

    def test_private_release_still_rejects_wrong_asset_size_or_server_digest(self):
        release, payloads = self.release_fixture()
        asset = release["assets"][0]
        for changed, message in (({"size": asset["size"] + 1}, "size changed"),
                                 ({"digest": "sha256:" + "0" * 64}, "server digest")):
            with self.subTest(changed=changed):
                invalid = {**release, "assets": [{**asset, **changed}, release["assets"][1]]}
                with patch.object(sync, "read_json", return_value=invalid), patch.object(
                        sync, "read_url", return_value=payloads[asset["name"]]):
                    with self.assertRaisesRegex(ValueError, message):
                        sync.release_files("v0.6.15", self.root / "release", "private-test-token")

    def test_authenticated_asset_redirect_rejects_untrusted_or_non_https_hosts(self):
        for location in ("http://release-assets.githubusercontent.com/package.zip", "https://example.com/package.zip"):
            with self.subTest(location=location):
                requests = []

                def opened(request, **kwargs):
                    requests.append(request)
                    raise urllib.error.HTTPError(request.full_url, 302, "redirect", {"Location": location}, None)

                with patch.object(sync.urllib.request, "build_opener", return_value=SimpleNamespace(open=opened)):
                    with self.assertRaisesRegex(ValueError, "Unexpected download host"):
                        sync.read_url(sync.API + "/releases/assets/1", "private-test-token", "api.github.com",
                                      accept="application/octet-stream")
                self.assertEqual(len(requests), 1)

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
        self.assertEqual(result["package_metadata_api_version"], "2026-03-10")
        self.assertEqual(result["association_status"], "verified")
        self.assertEqual(self.metadata.call_count, 1)

    def test_missing_current_repository_metadata_requires_verified_legacy_link(self):
        self.pipeline()
        self.registry.return_value = {"name": sync.NAME, "version": "0.6.0"}
        linked = dict(self.metadata.return_value)
        basic = {key: value for key, value in linked.items() if key != "repository"}
        for absent in ({}, {"repository": None}, {"repository": {}},
                       {"repository": {"full_name": None}}, {"repository": {"full_name": ""}}):
            with self.subTest(current=absent):
                self.metadata.reset_mock()
                self.metadata.side_effect = [{**basic, **absent}, linked]
                result = sync.sync("v0.6.0", self.root)
                self.assertTrue(result["ok"])
                self.assertEqual(result["linked_repository"], sync.REPOSITORY)
                self.assertEqual(result["association_status"], "verified")
                self.assertEqual(result["package_metadata_api_version"], "2022-11-28")
                self.assertEqual([call.kwargs["api_version"] for call in self.metadata.call_args_list],
                                 ["2026-03-10", "2022-11-28"])
                self.assertTrue(all(call.args == (
                    "https://api.github.com/users/NingCui29/packages/npm/story-skill",
                    "fake-test-token", "api.github.com") for call in self.metadata.call_args_list))
        self.assertTrue(all(call.args[0][0] == "whoami" for call in self.npm.call_args_list))

    def test_explicit_foreign_current_repository_fails_without_legacy_fallback(self):
        self.pipeline()
        self.registry.return_value = {"name": sync.NAME, "version": "0.6.0"}
        self.metadata.side_effect = [{**self.metadata.return_value,
                                      "repository": {"full_name": "Other/story-skill"}},
                                     self.metadata.return_value]
        with self.assertRaisesRegex(ValueError, "not linked to the expected repository"):
            sync.sync("v0.6.0", self.root)
        self.assertEqual(self.metadata.call_count, 1)

    def test_missing_legacy_repository_records_unavailable_without_inventing_a_link(self):
        self.pipeline()
        self.registry.return_value = {"name": sync.NAME, "version": "0.6.0",
                                      "repository": {"url": "https://github.com/" + sync.REPOSITORY + ".git"}}
        basic = {key: value for key, value in self.metadata.return_value.items() if key != "repository"}
        for absent in ({}, {"repository": None}, {"repository": {}},
                       {"repository": {"full_name": None}}, {"repository": {"full_name": ""}}):
            with self.subTest(legacy=absent):
                self.metadata.reset_mock()
                self.verify.reset_mock()
                self.runtime.reset_mock()
                self.metadata.side_effect = [basic, {**basic, **absent}]
                result = sync.sync("v0.6.0", self.root)
                self.assertTrue(result["ok"])
                self.assertEqual(result["association_status"], "unavailable")
                self.assertIsNone(result["linked_repository"])
                self.assertEqual(result["package_metadata_api_version"], "2022-11-28")
                self.assertEqual(result["package_url"], basic["html_url"])
                self.assertEqual(result["visibility"], "public")
                self.assertTrue(result["downloaded_package"]["ok"])
                self.assertEqual(result["downloaded_integrity"], "sha512-verified")
                self.assertTrue(result["runtime"]["ok"])
                self.verify.assert_called_once()
                self.runtime.assert_called_once()
                self.assertEqual(self.metadata.call_count, 2)
        self.assertTrue(all(call.args[0][0] == "whoami" for call in self.npm.call_args_list))

    def test_explicit_foreign_legacy_repository_still_fails(self):
        self.pipeline()
        self.registry.return_value = {"name": sync.NAME, "version": "0.6.0"}
        basic = {key: value for key, value in self.metadata.return_value.items() if key != "repository"}
        self.metadata.side_effect = [basic, {**basic, "repository": {"full_name": "Other/story-skill"}}]
        with self.assertRaisesRegex(ValueError, "not linked to the expected repository"):
            sync.sync("v0.6.0", self.root)
        self.assertEqual(self.metadata.call_count, 2)

    def test_malformed_repository_or_api_metadata_is_not_treated_as_missing(self):
        self.pipeline()
        self.registry.return_value = {"name": sync.NAME, "version": "0.6.0"}
        for repository in (sync.REPOSITORY, [], {"full_name": False}, {"full_name": 123}):
            with self.subTest(repository=repository):
                self.metadata.reset_mock()
                self.metadata.side_effect = [{**self.metadata.return_value, "repository": repository},
                                             self.metadata.return_value]
                with self.assertRaises(ValueError):
                    sync.sync("v0.6.0", self.root)
                self.assertEqual(self.metadata.call_count, 1)
        basic = {key: value for key, value in self.metadata.return_value.items() if key != "repository"}
        invalid_metadata = [None, [], {}, {"html_url": basic["html_url"]}, {"visibility": "public"},
                            {**basic, "html_url": None}, {**basic, "html_url": ""},
                            {**basic, "html_url": "https://example.com/story-skill"},
                            {**basic, "html_url": basic["html_url"] + "?redirect=other"},
                            {**basic, "html_url": basic["html_url"].replace("NingCui29", "Other")},
                            {**basic, "visibility": None}, {**basic, "visibility": "unknown"}]
        for invalid in invalid_metadata:
            for prefix in ([], [basic]):
                with self.subTest(metadata=invalid, legacy=bool(prefix)):
                    self.metadata.reset_mock()
                    self.metadata.side_effect = prefix + [invalid, self.metadata.return_value]
                    with self.assertRaisesRegex(ValueError, "invalid URL or visibility"):
                        sync.sync("v0.6.0", self.root)
                    self.assertEqual(self.metadata.call_count, len(prefix) + 1)

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

    def smoke_archive(self, version="0.6.5", names=None, changed=None):
        archive = self.root / "suite.tgz"
        root = SCRIPTS.parent / "skills"
        with tarfile.open(archive, "w:gz") as bundle:
            for name in (sync.package_npm.payload_files(version) if names is None else names):
                raw = (root / name).read_bytes()
                if version != "0.6.15" and name.endswith("/SKILL.md"):
                    # Historical fixtures cannot contain links added to current source rules.
                    raw = b"# Historical layout fixture\n[shared runtime](../story-skill/scripts/story.py)\n"
                if name == "story-skill/scripts/story.py":
                    raw = f'VERSION = "{version}"\n'.encode()
                if name == "story-skill-plan/SKILL.md" and changed is not None:
                    raw = changed
                member = tarfile.TarInfo("package/" + name)
                member.size = len(raw)
                bundle.addfile(member, io.BytesIO(raw))
        return archive

    def test_runtime_smoke_installs_all_suite_dependencies_before_execution(self):
        archive = self.smoke_archive()
        results = [SimpleNamespace(returncode=0, stdout="0.6.5\n"), SimpleNamespace(returncode=0, stdout="help"),
                   SimpleNamespace(returncode=0, stdout="{}"), SimpleNamespace(returncode=0, stdout='{"last_chapter":0}')]

        def execute(arguments, **kwargs):
            suite = Path(arguments[4]).parents[2]
            self.assertEqual({p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file()},
                             set(sync.package_npm.payload_files("0.6.5")))
            return results.pop(0)

        with patch.object(sync.subprocess, "run", side_effect=execute):
            result = sync.runtime_smoke(archive, "0.6.5")
        self.assertEqual(result["skill_files"], 40)
        self.assertEqual(set(result["skills"]), set(sync.package_npm.SKILL_NAMES_V061))
        self.assertTrue(result["temporary_book_removed"])

    def test_runtime_smoke_published_v069_checks_all_44_files(self):
        archive = self.smoke_archive("0.6.9")
        results = [SimpleNamespace(returncode=0, stdout="0.6.9\n"), SimpleNamespace(returncode=0, stdout="help"),
                   SimpleNamespace(returncode=0, stdout="{}"), SimpleNamespace(returncode=0, stdout='{"last_chapter":0}')]

        def execute(arguments, **kwargs):
            suite = Path(arguments[4]).parents[2]
            self.assertEqual({p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file()},
                             set(sync.package_npm.payload_files("0.6.9")))
            return results.pop(0)

        with patch.object(sync.subprocess, "run", side_effect=execute):
            result = sync.runtime_smoke(archive, "0.6.9")
        self.assertEqual(result["skill_files"], 44)
        self.assertEqual(set(result["skills"]), set(sync.package_npm.SKILL_NAMES_V069))

    def test_runtime_smoke_published_v0610_checks_all_45_files(self):
        archive = self.smoke_archive("0.6.10")
        results = [SimpleNamespace(returncode=0, stdout="0.6.10\n"), SimpleNamespace(returncode=0, stdout="help"),
                   SimpleNamespace(returncode=0, stdout="{}"), SimpleNamespace(returncode=0, stdout='{"last_chapter":0}')]

        def execute(arguments, **kwargs):
            suite = Path(arguments[4]).parents[2]
            self.assertEqual({p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file()},
                             set(sync.package_npm.payload_files("0.6.10")))
            self.assertTrue((suite / "story-skill-write/references/content-review.md").is_file())
            return results.pop(0)

        with patch.object(sync.subprocess, "run", side_effect=execute):
            result = sync.runtime_smoke(archive, "0.6.10")
        self.assertEqual(result["skill_files"], 45)
        self.assertEqual(set(result["skills"]), set(sync.package_npm.SKILL_NAMES_V0610))

    def test_runtime_smoke_candidate_v0611_checks_reader_validation_punctuation_and_all_49_files(self):
        archive = self.smoke_archive("0.6.11")
        results = [SimpleNamespace(returncode=0, stdout="0.6.11\n"), SimpleNamespace(returncode=0, stdout="help"),
                   SimpleNamespace(returncode=0, stdout="{}"), SimpleNamespace(returncode=0, stdout='{"last_chapter":0}')]

        def execute(arguments, **kwargs):
            suite = Path(arguments[4]).parents[2]
            self.assertEqual({p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file()},
                             set(sync.package_npm.payload_files("0.6.11")))
            self.assertTrue((suite / "story-skill-research/references/reader-validation.md").is_file())
            self.assertTrue((suite / "story-skill/scripts/story_punctuation.py").is_file())
            self.assertTrue((suite / "story-skill-write/references/punctuation.md").is_file())
            return results.pop(0)

        with patch.object(sync.subprocess, "run", side_effect=execute):
            result = sync.runtime_smoke(archive, "0.6.11")
        self.assertEqual(result["skill_files"], 49)
        self.assertEqual(set(result["skills"]), set(sync.package_npm.SKILL_NAMES_V0611))

    def test_runtime_smoke_v0612_through_current_v0615_preserve_versioned_file_layouts(self):
        for version, count, skills in (("0.6.12", 49, sync.package_npm.SKILL_NAMES_V0612),
                                       ("0.6.13", 50, sync.package_npm.SKILL_NAMES_V0613),
                                       ("0.6.14", 51, sync.package_npm.SKILL_NAMES_V0614),
                                       ("0.6.15", 51, sync.package_npm.SKILL_NAMES_V0615)):
            with self.subTest(version=version):
                archive = self.smoke_archive(version)
                results = [SimpleNamespace(returncode=0, stdout=version + "\n"),
                           SimpleNamespace(returncode=0, stdout="help"),
                           SimpleNamespace(returncode=0, stdout="{}"),
                           SimpleNamespace(returncode=0, stdout='{"last_chapter":0}')]

                def execute(arguments, **kwargs):
                    suite = Path(arguments[4]).parents[2]
                    self.assertEqual({p.relative_to(suite).as_posix() for p in suite.rglob("*") if p.is_file()},
                                     set(sync.package_npm.payload_files(version)))
                    self.assertTrue((suite / "story-skill-research/references/reader-validation.md").is_file())
                    self.assertTrue((suite / "story-skill/scripts/story_punctuation.py").is_file())
                    self.assertTrue((suite / "story-skill-write/references/punctuation.md").is_file())
                    self.assertEqual((suite / "story-skill-plan/references/outline.md").is_file(),
                                     version in ("0.6.13", "0.6.14", "0.6.15"))
                    self.assertEqual((suite / "story-skill/references/workbench.md").is_file(),
                                     version in ("0.6.14", "0.6.15"))
                    return results.pop(0)

                with patch.object(sync.subprocess, "run", side_effect=execute):
                    result = sync.runtime_smoke(archive, version)
                self.assertEqual(result["skill_files"], count)
                self.assertEqual(set(result["skills"]), set(skills))

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
        archive = self.smoke_archive(names=[name for name in sync.package_npm.payload_files("0.6.5")
                                            if name.startswith("story-skill/")])
        with patch.object(sync.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "missing required skill dependencies"):
                sync.runtime_smoke(archive, "0.6.5")
        execute.assert_not_called()

    def test_runtime_smoke_rejects_broken_sibling_link_before_execution(self):
        archive = self.smoke_archive(changed=b"[missing shared rules](../story-skill/missing.md)")
        with patch.object(sync.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "broken local dependency"):
                sync.runtime_smoke(archive, "0.6.5")
        execute.assert_not_called()



if __name__ == "__main__":
    unittest.main()
