import copy
import errno
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_package_npm", ROOT / "scripts/package_npm.py")
npm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(npm)


class NpmPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-npm-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.version = "0.6.1"
        self.archive = self.root / "story-skill-0.6.1.zip"
        self.checksum = self.root / "release.sha256"
        self.tarball = self.root / "package.tgz"
        self.payload = {name: ("原始字节：" + name + "\r\n").encode() for name in npm.SUITE_FILES}
        self.payload["story-skill/scripts/story.py"] = (
            b'VERSION = "0.6.1"\r\nraise RuntimeError("never execute archive code")\n')
        self.write_zip()

    def write_zip(self, payload=None, extra=None):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w") as bundle:
                for name, raw in (self.payload if payload is None else payload).items():
                    bundle.writestr(name, raw)
                if extra:
                    bundle.writestr(*extra)
        self.checksum.write_text(npm.sha256(self.archive.read_bytes()) + "  " + self.archive.name + "\n",
                                 encoding="utf-8")

    def tar_files(self):
        _, wrapper = npm.wrapper_files(self.version)
        return {"package/" + name: raw for name, raw in {**self.payload, **wrapper}.items()}

    def write_tar(self, files=None, extra=None, destination=None):
        destination = destination or self.tarball
        with tarfile.open(destination, "w:gz") as bundle:
            for name, raw in (self.tar_files() if files is None else files).items():
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                bundle.addfile(member, io.BytesIO(raw))
            if extra:
                member, raw = extra
                bundle.addfile(member, io.BytesIO(raw) if raw is not None else None)
        return destination

    def verify(self, expected=None):
        return npm.verify_tarball(self.archive, self.checksum, self.tarball, expected)

    def fake_pack(self, stage, destination):
        self.assertEqual({name: (stage / name).read_bytes() for name in self.payload}, self.payload)
        files = {"package/" + p.relative_to(stage).as_posix(): p.read_bytes()
                 for p in stage.rglob("*") if p.is_file()}
        name = npm.wrapper_files(self.version)[0]["name"]
        filename = name.removeprefix("@").replace("/", "-") + f"-{self.version}.tgz"
        path = self.write_tar(files, destination=destination / filename)
        return {"name": name, "version": self.version, "filename": filename,
                "integrity": npm.integrity(path.read_bytes())}

    def prepare_existing(self):
        output = self.root / "out"
        output.mkdir()
        name = npm.wrapper_files(self.version)[0]["name"]
        previous = output / (name.removeprefix("@").replace("/", "-") + f"-{self.version}.tgz")
        previous.write_bytes(b"previous reviewed tarball")
        return output, previous

    def assert_preserved(self, output, previous):
        self.assertEqual(previous.read_bytes(), b"previous reviewed tarball")
        self.assertEqual(list(output.glob(".story-npm-stage-*")), [])

    def test_read_release_preserves_crlf_without_executing_payload(self):
        payload, version, checksum = npm.read_release(self.archive, self.checksum)
        self.assertEqual(payload, self.payload)
        self.assertEqual(version, self.version)
        self.assertEqual(checksum, hashlib.sha256(self.archive.read_bytes()).hexdigest())



    def test_checksum_binds_exact_filename_and_archive_bytes(self):
        correct = self.checksum.read_text(encoding="utf-8")
        for invalid in [correct.replace(self.archive.name, "another.zip"),
                        "0" * 64 + "  " + self.archive.name, correct + correct]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.checksum.write_text(invalid, encoding="utf-8")
                npm.read_release(self.archive, self.checksum)

    def test_zip_rejects_extra_missing_duplicate_and_linked_members(self):
        link = zipfile.ZipInfo("story-skill/scripts/story_world.py")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        removed = dict(self.payload)
        removed.pop(link.filename)
        cases = [(self.payload, ("story-skill/../../escape", b"bad")),
                 (removed, None), (self.payload, ("story-skill/SKILL.md", b"duplicate")),
                 (removed, (link, b"elsewhere"))]
        for payload, extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.write_zip(payload, extra)
                npm.read_release(self.archive, self.checksum)

    def test_version_must_be_single_literal_and_match_archive_name(self):
        for code in [b'VERSION = str("0.6.1")', b'VERSION = "99.0.0"',
                     b'VERSION = "0.6.1"\nVERSION = "0.6.1"']:
            with self.subTest(code=code), self.assertRaises(ValueError):
                self.payload["story-skill/scripts/story.py"] = code
                self.write_zip()
                npm.read_release(self.archive, self.checksum)

    def test_tarball_verification_returns_payload_and_wrapper_evidence(self):
        self.write_tar()
        result = self.verify()
        self.assertTrue(result["ok"])
        self.assertEqual(result["sha256"], npm.sha256(self.tarball.read_bytes()))
        self.assertEqual(result["integrity"], npm.integrity(self.tarball.read_bytes()))
        self.assertEqual(result["payload_manifest"], {k: npm.sha256(v) for k, v in self.payload.items()})
        self.assertEqual(set(result["wrapper_manifest"]), {"README.md", "package.json"})
        manifest = result["package_manifest"]
        self.assertEqual(manifest["files"], list(npm.payload_files(self.version)))
        self.assertEqual(manifest["publishConfig"]["registry"], "https://npm.pkg.github.com")
        self.assertTrue({"scripts", "bin", "dependencies", "devDependencies", "main"}.isdisjoint(manifest))

    def test_tarball_rejects_changed_bytes_and_newline_normalization(self):
        for replacement in [b"X" + self.payload["story-skill/SKILL.md"][1:],
                            self.payload["story-skill/SKILL.md"].replace(b"\r\n", b"\n")]:
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, "bytes differ"):
                files = self.tar_files()
                files["package/story-skill/SKILL.md"] = replacement
                self.write_tar(files)
                self.verify()

    def test_tarball_rejects_missing_extra_duplicate_and_links(self):
        duplicate = tarfile.TarInfo("package/story-skill/SKILL.md")
        duplicate.size = 1
        link = tarfile.TarInfo("package/linked")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        hardlink = tarfile.TarInfo("package/hardlink")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "package/story-skill/SKILL.md"
        outside = tarfile.TarInfo("../escape")
        outside.size = 1
        cases = [(duplicate, b"x"), (link, None), (hardlink, None), (outside, b"x")]
        for extra in cases:
            with self.subTest(name=extra[0].name), self.assertRaises(ValueError):
                self.write_tar(extra=extra)
                self.verify()
        files = self.tar_files()
        files.pop("package/story-skill/LICENSE")
        self.write_tar(files)
        with self.assertRaisesRegex(ValueError, "missing"):
            self.verify()

    def test_tarball_cannot_inject_install_hook_into_wrapper(self):
        files = self.tar_files()
        package = json.loads(files["package/package.json"])
        package["scripts"] = {"postinstall": "unwanted side effects"}
        files["package/package.json"] = json.dumps(package).encode()
        self.write_tar(files)
        with self.assertRaisesRegex(ValueError, "bytes differ"):
            self.verify()

    def test_expected_build_manifest_binds_identity_payload_and_wrapper(self):
        self.write_tar()
        original = self.verify()
        path = self.root / "build.json"
        path.write_text(json.dumps(original), encoding="utf-8")
        self.assertTrue(self.verify(path)["ok"])
        for key in ["ok", "name", "version", "registry", "archive_sha256",
                    "payload_manifest", "wrapper_manifest", "package_manifest"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                changed = copy.deepcopy(original)
                changed[key] = None
                self.verify(changed)

    def test_equivalent_remote_gzip_returns_actual_integrity_without_requiring_local_container_hash(self):
        self.write_tar()
        original = self.verify()
        self.tarball.write_bytes(gzip.compress(gzip.decompress(self.tarball.read_bytes()), mtime=123))
        downloaded = self.verify(original)
        self.assertNotEqual(downloaded["sha256"], original["sha256"])
        self.assertNotEqual(downloaded["integrity"], original["integrity"])
        self.assertEqual(downloaded["payload_manifest"], original["payload_manifest"])
        self.assertEqual(downloaded["wrapper_manifest"], original["wrapper_manifest"])

    def test_build_publishes_only_after_verification(self):
        output, _ = self.prepare_existing()
        with patch.object(npm, "npm_pack", side_effect=self.fake_pack):
            result = npm.build(self.archive, self.checksum, output)
        self.assertEqual(Path(result["tarball"]).parent, output)
        self.assertEqual(result, npm.verify_tarball(self.archive, self.checksum, result["tarball"], result))
        self.assertEqual(list(output.glob(".story-npm-stage-*")), [])

    def test_failed_pack_preserves_existing_tarball(self):
        output, previous = self.prepare_existing()

        def fail(stage, destination):
            (destination / previous.name).write_bytes(b"partial archive")
            raise OSError(errno.ENOSPC, "simulated disk full")

        with patch.object(npm, "npm_pack", side_effect=fail), self.assertRaises(OSError):
            npm.build(self.archive, self.checksum, output)
        self.assert_preserved(output, previous)

    def test_failed_tar_validation_preserves_existing_tarball(self):
        output, previous = self.prepare_existing()

        def invalid(stage, destination):
            receipt = self.fake_pack(stage, destination)
            self.write_tar({"package/extra": b"not the skill"}, destination=destination / receipt["filename"])
            return receipt

        with patch.object(npm, "npm_pack", side_effect=invalid), self.assertRaises(ValueError):
            npm.build(self.archive, self.checksum, output)
        self.assert_preserved(output, previous)

    def test_failed_replace_preserves_existing_tarball(self):
        output, previous = self.prepare_existing()
        with patch.object(npm, "npm_pack", side_effect=self.fake_pack), patch.object(
                npm.os, "replace", side_effect=PermissionError("locked destination")), self.assertRaises(PermissionError):
            npm.build(self.archive, self.checksum, output)
        self.assert_preserved(output, previous)

    def test_npm_pack_disables_scripts_and_uses_structured_arguments(self):
        with patch.object(npm, "npm_command", return_value=["node", "npm-cli.js"]), patch.object(
                npm.subprocess, "run", return_value=types.SimpleNamespace(returncode=0, stdout='[{}]', stderr="")) as run:
            npm.npm_pack(self.root, self.root / "directory with spaces & literal chars")
        self.assertEqual(run.call_args.args[0], ["node", "npm-cli.js", "pack", "--json", "--ignore-scripts",
                                               "--pack-destination", str(self.root / "directory with spaces & literal chars")])
        self.assertNotIn("shell", run.call_args.kwargs)


    def test_npm_12_keyed_receipt_is_checked_against_its_package_name(self):
        receipt = {"name": "@ningcui29/story-skill", "version": "0.6.0"}
        def invoke(value):
            with patch.object(npm, "npm_command", return_value=["npm"]), patch.object(
                    npm.subprocess, "run", return_value=types.SimpleNamespace(
                        returncode=0, stdout=json.dumps(value), stderr="")):
                return npm.npm_pack(self.root, self.root / "packed")
        self.assertEqual(invoke({receipt["name"]: receipt}), receipt)
        for invalid in ({"different": receipt}, {"error": {"message": "failed"}},
                        {receipt["name"]: receipt, "extra": receipt}):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                invoke(invalid)


class NpmSuiteTests(unittest.TestCase):
    def test_current_package_identity_and_wrapper(self):
        manifest, files = npm.wrapper_files("0.6.1")
        self.assertEqual(manifest["name"], "@ningcui29/story-skill")
        self.assertEqual(manifest["repository"]["url"], "https://github.com/NingCui29/story-skill.git")
        self.assertEqual(manifest["files"], list(npm.SUITE_FILES))
        self.assertEqual(len(npm.SUITE_FILES), 39)
        readme = files["README.md"].decode("utf-8")
        self.assertIn("eight sibling skills", readme)
        self.assertIn("`story-skill-publish/`", readme)
        self.assertIn("固定使用 v0.6.1", readme)
        self.assertIn("For macOS, Linux and Windows", readme)
        self.assertIn("does not log in to author platforms", readme)
        forbidden = ("co" + "dex").encode()
        self.assertNotIn(forbidden, files["README.md"].lower())

    def test_only_current_release_family_has_reviewed_layout(self):
        layouts = {"0.6.0": npm.SUITE_FILES_V060, "0.6.1": npm.SUITE_FILES_V061}
        skill_layouts = {"0.6.0": npm.SKILL_NAMES_V060, "0.6.1": npm.SKILL_NAMES_V061}
        for version, expected in layouts.items():
            self.assertEqual(npm.payload_files(version), expected)
            self.assertEqual(npm.skill_names(version), skill_layouts[version])
            self.assertEqual(npm.package_identity(version), (npm.NAME, npm.REPOSITORY))
        self.assertEqual(len(npm.SUITE_FILES_V060), 38)
        self.assertNotIn("story-skill/scripts/story_workbench.py", npm.SUITE_FILES_V060)
        self.assertEqual(len(npm.SUITE_FILES), 39)
        self.assertEqual(npm.SUITE_FILES, npm.SUITE_FILES_V061)
        self.assertEqual(npm.SKILL_NAMES, npm.SKILL_NAMES_V061)
        self.assertIn("story-skill/scripts/story_workbench.py", npm.SUITE_FILES)
        for version in ("0.5.11", "0.6.00", "0.6.2", "0.6.99", "1.0.0"):
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed payload layout"):
                npm.payload_files(version)
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "no reviewed payload layout"):
                npm.skill_names(version)

    def test_zip_and_npm_share_one_manifest(self):
        spec = importlib.util.spec_from_file_location("zip_suite_manifest", ROOT / "scripts/package.py")
        zip_package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(zip_package)
        self.assertEqual(npm.SUITE_FILES, zip_package.SUITE_FILES)
        self.assertEqual(npm.SUITE_FILES_V060, zip_package.SUITE_FILES_V060)
        self.assertEqual(npm.SUITE_FILES_V061, zip_package.SUITE_FILES_V061)
        self.assertEqual(npm.SKILL_NAMES, zip_package.SKILL_NAMES)
        self.assertEqual(npm.SKILL_NAMES_V060, zip_package.SKILL_NAMES_V060)
        self.assertEqual(npm.SKILL_NAMES_V061, zip_package.SKILL_NAMES_V061)
        for version in ("0.6.0", "0.6.1"):
            self.assertEqual(npm.payload_files(version), zip_package.suite_files(version))


if __name__ == "__main__":
    unittest.main()
