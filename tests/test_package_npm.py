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
MIT_LICENSE = (ROOT / "tests/fixtures/LICENSE").read_bytes()


def mit_payload(version, prefix):
    payload = {name: (prefix + name + "\r\n").encode()
               for name in npm.payload_files(version)}
    for name in npm.skill_names(version):
        payload[name + "/LICENSE"] = MIT_LICENSE
    return payload


class NpmPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-npm-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.version = "0.6.1"
        self.archive = self.root / "story-skill-0.6.1.zip"
        self.checksum = self.root / "release.sha256"
        self.tarball = self.root / "package.tgz"
        self.payload = mit_payload(self.version, "原始字节：")
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
        _, wrapper = npm.wrapper_files(self.version, self.payload)
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

    def test_development_files_require_v0611_and_survive_wrapper_build(self):
        self.version = "0.6.11"
        self.archive = self.root / "story-skill-0.6.11.zip"
        self.payload = mit_payload(self.version, "开发载荷：")
        self.payload["story-skill/scripts/story.py"] = b'VERSION = "0.6.11"\n'
        self.write_zip()
        with patch.object(npm, "npm_pack", side_effect=self.fake_pack):
            result = npm.build(self.archive, self.checksum, self.root / "out")
        references = ("story-skill-plan/references/blurb.md",
                      "story-skill-research/references/reader-validation.md",
                      "story-skill-write/references/punctuation.md",
                      "story-skill/scripts/story_punctuation.py")
        self.assertEqual(len(result["payload_manifest"]), 49)
        for reference in references:
            self.assertEqual(result["payload_manifest"][reference], npm.sha256(self.payload[reference]))
        self.assertEqual(result["package_manifest"]["version"], "0.6.11")

        for reference in references:
            with self.subTest(reference=reference):
                missing = dict(self.payload)
                missing.pop(reference)
                self.write_zip(missing)
                with self.assertRaisesRegex(ValueError, "reviewed layout"):
                    npm.read_release(self.archive, self.checksum)

        self.archive = self.root / "story-skill-0.6.10.zip"
        self.payload["story-skill/scripts/story.py"] = b'VERSION = "0.6.10"\n'
        self.write_zip()
        with self.assertRaisesRegex(ValueError, "reviewed layout"):
            npm.read_release(self.archive, self.checksum)

    def test_v0612_build_preserves_all_reviewed_payload_bytes(self):
        self.version = "0.6.12"
        self.archive = self.root / "story-skill-0.6.12.zip"
        self.payload = mit_payload(self.version, "发布载荷：")
        self.payload["story-skill/scripts/story.py"] = b'VERSION = "0.6.12"\r\n'
        self.write_zip()
        with patch.object(npm, "npm_pack", side_effect=self.fake_pack):
            result = npm.build(self.archive, self.checksum, self.root / "out")
        self.assertEqual(result["version"], "0.6.12")
        self.assertEqual(result["package_manifest"]["version"], "0.6.12")
        self.assertEqual(len(result["payload_manifest"]), 49)
        with tarfile.open(result["tarball"], "r:gz") as bundle:
            for name, raw in self.payload.items():
                self.assertEqual(bundle.extractfile("package/" + name).read(), raw, name)
        self.assertEqual(result, npm.verify_tarball(self.archive, self.checksum, result["tarball"], result))

    def test_v0615_build_preserves_payload_bytes_and_binds_the_new_version(self):
        self.version = "0.6.15"
        self.archive = self.root / "story-skill-0.6.15.zip"
        self.payload = mit_payload(self.version, "发布载荷：")
        self.payload["story-skill/scripts/story.py"] = b'VERSION = "0.6.15"\r\n'
        self.write_zip()
        with patch.object(npm, "npm_pack", side_effect=self.fake_pack):
            result = npm.build(self.archive, self.checksum, self.root / "out")
        self.assertEqual(result["package_manifest"]["version"], "0.6.15")
        self.assertEqual(len(result["payload_manifest"]), 51)
        with tarfile.open(result["tarball"], "r:gz") as bundle:
            for name, raw in self.payload.items():
                self.assertEqual(bundle.extractfile("package/" + name).read(), raw, name)
        self.assertEqual(result, npm.verify_tarball(self.archive, self.checksum, result["tarball"], result))
        with self.assertRaisesRegex(ValueError, "expected build manifest: version"):
            npm.verify_tarball(self.archive, self.checksum, result["tarball"], {**result, "version": "0.6.14"})

    def test_development_builds_preserve_resources_and_reject_them_under_previous_layouts(self):
        for version, count, previous in (("0.6.13", 50, "0.6.12"),
                                         ("0.6.14", 51, "0.6.13")):
            with self.subTest(version=version):
                self.version = version
                self.archive = self.root / f"story-skill-{version}.zip"
                self.payload = mit_payload(version, "开发载荷：")
                self.payload["story-skill/scripts/story.py"] = f'VERSION = "{version}"\r\n'.encode()
                self.write_zip()
                with patch.object(npm, "npm_pack", side_effect=self.fake_pack):
                    result = npm.build(self.archive, self.checksum, self.root / "out")
                self.assertEqual(result["version"], version)
                self.assertEqual(len(result["payload_manifest"]), count)
                with tarfile.open(result["tarball"], "r:gz") as bundle:
                    for name, raw in self.payload.items():
                        self.assertEqual(bundle.extractfile("package/" + name).read(), raw, name)
                self.assertEqual(result, npm.verify_tarball(self.archive, self.checksum, result["tarball"], result))
                self.archive = self.root / f"story-skill-{previous}.zip"
                self.payload["story-skill/scripts/story.py"] = f'VERSION = "{previous}"\n'.encode()
                self.write_zip()
                with self.assertRaisesRegex(ValueError, "reviewed layout"):
                    npm.read_release(self.archive, self.checksum)

    def test_checksum_binds_exact_filename_and_archive_bytes(self):
        correct = self.checksum.read_text(encoding="utf-8")
        for invalid in [correct.replace(self.archive.name, "another.zip"),
                        "0" * 64 + "  " + self.archive.name, correct + correct]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.checksum.write_text(invalid, encoding="utf-8")
                npm.read_release(self.archive, self.checksum)

    def test_custom_license_build_and_verify_preserve_input_license_bytes(self):
        self.version = "0.6.15"
        self.archive = self.root / "story-skill-0.6.15.zip"
        self.payload = mit_payload(self.version, "自定义许可载荷：")
        self.payload["story-skill/scripts/story.py"] = b'VERSION = "0.6.15"\n'
        custom = b"\xef\xbb\xbf" + (ROOT / "LICENSE").read_bytes().replace(b"\n", b"\r\n")
        for name in npm.skill_names(self.version):
            self.payload[name + "/LICENSE"] = custom
        self.write_zip()
        with patch.object(npm, "npm_pack", side_effect=self.fake_pack):
            result = npm.build(self.archive, self.checksum, self.root / "out")
        self.assertEqual(result["package_manifest"]["license"], "SEE LICENSE IN LICENSE")
        self.assertEqual(result["package_manifest"]["files"],
                         list(npm.payload_files(self.version)) + ["LICENSE"])
        self.assertEqual(set(result["wrapper_manifest"]), {"README.md", "package.json", "LICENSE"})
        with tarfile.open(result["tarball"], "r:gz") as bundle:
            self.assertEqual(bundle.extractfile("package/LICENSE").read(), custom)
            for name, raw in self.payload.items():
                self.assertEqual(bundle.extractfile("package/" + name).read(), raw, name)
            readme = bundle.extractfile("package/README.md").read().decode()
        self.assertIn("commercial use requires a separate paid authorization", readme)
        self.assertIn("exact bytes of the input ZIP", readme)
        self.assertNotIn("License: MIT", readme)
        self.assertNotIn("matching GitHub Release ZIP", readme)
        self.assertNotIn("/blob/v0.6.15/", readme)
        self.assertEqual(result, npm.verify_tarball(self.archive, self.checksum, result["tarball"], result))

        for replacement in (None, b"changed license"):
            with self.subTest(replacement=replacement):
                files = self.tar_files()
                if replacement is None:
                    files.pop("package/LICENSE")
                else:
                    files["package/LICENSE"] = replacement
                self.write_tar(files)
                with self.assertRaisesRegex(ValueError, "missing|bytes differ"):
                    self.verify()

    def test_mixed_and_unknown_license_archives_fail_before_pack_or_replacement(self):
        self.write_tar()
        original = dict(self.payload)
        output, previous = self.prepare_existing()
        license_path = "story-skill/LICENSE"
        cases = (
            ({license_path: (ROOT / "LICENSE").read_bytes()}, "mixed skill licenses"),
            ({license_path: MIT_LICENSE.replace(b"without restriction", b"for restricted use")},
             "Unsupported skill license"),
            ({name + "/LICENSE": b"# Story Skill Non-Commercial Use and Commercial Licensing Agreement\n"
              for name in npm.skill_names(self.version)}, "Unsupported skill license"),
            ({license_path: b"\xff"}, "Unsupported skill license"),
        )
        for changes, error in cases:
            with self.subTest(changes=tuple(changes)):
                self.payload = {**original, **changes}
                self.write_zip()
                with patch.object(npm, "npm_pack") as pack:
                    with self.assertRaisesRegex(ValueError, error):
                        npm.build(self.archive, self.checksum, output)
                    pack.assert_not_called()
                with self.assertRaisesRegex(ValueError, error):
                    self.verify()
                self.assert_preserved(output, previous)

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
    def test_historical_mit_wrapper_bytes_are_unchanged_for_input_archives(self):
        expected = {
            "0.6.1": {
                "package.json": "b2f9092688ca6cba246c7bfac990e8379a5fa32ab88cf3697d08df42ba335a93",
                "README.md": "44a2992f2e58ad0cc78afc061c7c994402d8f3fc18c71e929c3b801cbc8b9c72",
            },
            "0.6.15": {
                "package.json": "2114bf62705874e51d29f392d93502a55e7070cea7cc040759564b90e27bbf7e",
                "README.md": "33e057e8d99fbd9e0bade72ee7d9ee14ed57ea0b02a9099378465954489d895c",
            },
        }
        for version, digests in expected.items():
            with self.subTest(version=version):
                payload = mit_payload(version, "历史MIT归档：")
                for name in npm.skill_names(version):
                    payload[name + "/LICENSE"] = MIT_LICENSE.replace(b"\n", b"\r\n")
                manifest, files = npm.wrapper_files(version, payload)
                self.assertEqual(manifest["license"], "MIT")
                self.assertEqual({name: npm.sha256(raw) for name, raw in files.items()}, digests)
                self.assertEqual((manifest, files), npm.wrapper_files(version))

    def test_published_v0612_package_identity_and_wrapper(self):
        manifest, files = npm.wrapper_files("0.6.12")
        self.assertEqual(manifest["name"], "@ningcui29/story-skill")
        self.assertEqual(manifest["repository"]["url"], "https://github.com/NingCui29/story-skill.git")
        self.assertEqual(manifest["files"], list(npm.SUITE_FILES_V0612))
        self.assertEqual(len(manifest["files"]), 49)
        readme = files["README.md"].decode("utf-8")
        self.assertIn("eight sibling skills", readme)
        self.assertIn("`story-skill-publish/`", readme)
        self.assertNotIn("Unreleased development snapshot", readme)
        self.assertIn("/blob/v0.6.12/", readme)
        self.assertIn("固定使用 v0.6.12", readme)
        self.assertIn("For macOS, Linux and Windows", readme)
        self.assertIn("does not log in to author platforms", readme)
        forbidden = ("co" + "dex").encode()
        self.assertNotIn(forbidden, files["README.md"].lower())
        released_manifest, released_files = npm.wrapper_files("0.6.10")
        self.assertEqual(released_manifest["files"], list(npm.SUITE_FILES_V0610))
        self.assertEqual(len(released_manifest["files"]), 45)
        self.assertIn("固定使用 v0.6.10", released_files["README.md"].decode("utf-8"))
        self.assertNotIn("Unreleased development snapshot", released_files["README.md"].decode("utf-8"))
        published_manifest, published_files = npm.wrapper_files("0.6.9")
        self.assertEqual(published_manifest["files"], list(npm.SUITE_FILES_V069))
        self.assertEqual(len(published_manifest["files"]), 44)
        self.assertIn("固定使用 v0.6.9", published_files["README.md"].decode("utf-8"))
        old_manifest, old_files = npm.wrapper_files("0.6.8")
        self.assertEqual(old_manifest["files"], list(npm.SUITE_FILES_V065))
        self.assertEqual(len(old_manifest["files"]), 40)
        self.assertIn("固定使用 v0.6.8", old_files["README.md"].decode("utf-8"))

    def test_current_and_development_wrappers_preserve_layout_and_release_status(self):
        for version, reviewed, count in (("0.6.13", npm.SUITE_FILES_V0613, 50),
                                         ("0.6.14", npm.SUITE_FILES_V0614, 51),
                                         ("0.6.15", npm.SUITE_FILES_V0615, 51),
                                         ("0.6.16", npm.SUITE_FILES_V0616, 51)):
            with self.subTest(version=version):
                manifest, files = npm.wrapper_files(version)
                self.assertEqual(manifest["version"], version)
                self.assertEqual(manifest["files"], list(reviewed))
                self.assertEqual(len(manifest["files"]), count)
                self.assertIn("story-skill-plan/references/outline.md", manifest["files"])
                self.assertEqual("story-skill/references/workbench.md" in manifest["files"],
                                 version in ("0.6.14", "0.6.15", "0.6.16"))
                readme = files["README.md"].decode("utf-8")
                if version == "0.6.13":
                    self.assertIn("Unreleased development snapshot", readme)
                    self.assertNotIn(f"/blob/v{version}/", readme)
                else:
                    self.assertNotIn("Unreleased development snapshot", readme)
                    self.assertIn(f"/blob/v{version}/", readme)

    def test_only_current_release_family_has_reviewed_layout(self):
        layouts = {"0.6.0": npm.SUITE_FILES_V060, "0.6.1": npm.SUITE_FILES_V061, "0.6.2": npm.SUITE_FILES_V061, "0.6.3": npm.SUITE_FILES_V061, "0.6.4": npm.SUITE_FILES_V061, "0.6.5": npm.SUITE_FILES_V065, "0.6.6": npm.SUITE_FILES_V065, "0.6.7": npm.SUITE_FILES_V065, "0.6.8": npm.SUITE_FILES_V065, "0.6.9": npm.SUITE_FILES_V069, "0.6.10": npm.SUITE_FILES_V0610, "0.6.11": npm.SUITE_FILES_V0611, "0.6.12": npm.SUITE_FILES_V0612, "0.6.13": npm.SUITE_FILES_V0613, "0.6.14": npm.SUITE_FILES_V0614, "0.6.15": npm.SUITE_FILES_V0615, "0.6.16": npm.SUITE_FILES_V0616}
        skill_layouts = {"0.6.0": npm.SKILL_NAMES_V060, "0.6.1": npm.SKILL_NAMES_V061, "0.6.2": npm.SKILL_NAMES_V061, "0.6.3": npm.SKILL_NAMES_V061, "0.6.4": npm.SKILL_NAMES_V061, "0.6.5": npm.SKILL_NAMES_V061, "0.6.6": npm.SKILL_NAMES_V061, "0.6.7": npm.SKILL_NAMES_V061, "0.6.8": npm.SKILL_NAMES_V061, "0.6.9": npm.SKILL_NAMES_V069, "0.6.10": npm.SKILL_NAMES_V0610, "0.6.11": npm.SKILL_NAMES_V0611, "0.6.12": npm.SKILL_NAMES_V0612, "0.6.13": npm.SKILL_NAMES_V0613, "0.6.14": npm.SKILL_NAMES_V0614, "0.6.15": npm.SKILL_NAMES_V0615, "0.6.16": npm.SKILL_NAMES_V0616}
        for version, expected in layouts.items():
            self.assertEqual(npm.payload_files(version), expected)
            self.assertEqual(npm.skill_names(version), skill_layouts[version])
            self.assertEqual(npm.package_identity(version), (npm.NAME, npm.REPOSITORY))
        self.assertEqual(len(npm.SUITE_FILES_V060), 38)
        self.assertNotIn("story-skill/scripts/story_workbench.py", npm.SUITE_FILES_V060)
        self.assertEqual(len(npm.SUITE_FILES_V065), 40)
        self.assertEqual(npm.payload_files("0.6.8"), npm.SUITE_FILES_V065)
        self.assertEqual(len(npm.SUITE_FILES_V069), 44)
        self.assertEqual(npm.payload_files("0.6.9"), npm.SUITE_FILES_V069)
        self.assertEqual(len(npm.SUITE_FILES), 51)
        self.assertEqual(npm.SUITE_FILES, npm.SUITE_FILES_V0616)
        self.assertEqual(npm.SUITE_FILES_V0612, npm.SUITE_FILES_V0611)
        self.assertEqual(len(npm.SUITE_FILES_V0612), 49)
        self.assertNotIn("story-skill-plan/references/outline.md", npm.SUITE_FILES_V0612)
        self.assertEqual(set(npm.SUITE_FILES_V0613) - set(npm.SUITE_FILES_V0612), {
            "story-skill-plan/references/outline.md",
        })
        self.assertEqual(npm.SKILL_NAMES, npm.SKILL_NAMES_V0616)
        self.assertEqual(len(npm.SUITE_FILES_V0613), 50)
        self.assertNotIn("story-skill/references/workbench.md", npm.SUITE_FILES_V0613)
        self.assertEqual(set(npm.SUITE_FILES_V0614) - set(npm.SUITE_FILES_V0613), {
            "story-skill/references/workbench.md",
        })
        self.assertEqual(npm.SKILL_NAMES_V0614, npm.SKILL_NAMES_V0613)
        self.assertEqual(npm.SUITE_FILES_V0615, npm.SUITE_FILES_V0614)
        self.assertEqual(npm.SKILL_NAMES_V0615, npm.SKILL_NAMES_V0614)
        self.assertEqual(len(npm.SKILL_NAMES_V0615), 8)
        self.assertEqual(npm.SKILL_NAMES_V0612, npm.SKILL_NAMES_V0611)
        self.assertEqual(len(npm.SUITE_FILES_V0610), 45)
        self.assertEqual(set(npm.SUITE_FILES_V0611) - set(npm.SUITE_FILES_V0610), {
            "story-skill-plan/references/blurb.md",
            "story-skill-research/references/reader-validation.md",
            "story-skill-write/references/punctuation.md",
            "story-skill/scripts/story_punctuation.py",
        })
        self.assertIn("story-skill/scripts/story_workbench.py", npm.SUITE_FILES)
        for version in ("0.5.11", "0.6.00", "0.6.17", "0.6.99", "1.0.0"):
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
        self.assertEqual(npm.SUITE_FILES_V065, zip_package.SUITE_FILES_V065)
        self.assertEqual(npm.SUITE_FILES_V069, zip_package.SUITE_FILES_V069)
        self.assertEqual(npm.SUITE_FILES_V0610, zip_package.SUITE_FILES_V0610)
        self.assertEqual(npm.SUITE_FILES_V0611, zip_package.SUITE_FILES_V0611)
        self.assertEqual(npm.SUITE_FILES_V0612, zip_package.SUITE_FILES_V0612)
        self.assertEqual(npm.SUITE_FILES_V0613, zip_package.SUITE_FILES_V0613)
        self.assertEqual(npm.SUITE_FILES_V0614, zip_package.SUITE_FILES_V0614)
        self.assertEqual(npm.SUITE_FILES_V0615, zip_package.SUITE_FILES_V0615)
        self.assertEqual(npm.SKILL_NAMES, zip_package.SKILL_NAMES)
        self.assertEqual(npm.SKILL_NAMES_V060, zip_package.SKILL_NAMES_V060)
        self.assertEqual(npm.SKILL_NAMES_V061, zip_package.SKILL_NAMES_V061)
        for version in ("0.6.0", "0.6.1", "0.6.4", "0.6.5", "0.6.6", "0.6.7", "0.6.8", "0.6.9", "0.6.10", "0.6.11", "0.6.12", "0.6.13", "0.6.14", "0.6.15"):
            self.assertEqual(npm.payload_files(version), zip_package.suite_files(version))


if __name__ == "__main__":
    unittest.main()
