"""DeepSeek Harness bundle: manifest, one-level skills, patch and build safety."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = load("story_skill_package", ROOT / "scripts/package.py")
bundle = load("story_skill_dsh_package", ROOT / "scripts/package_dsh.py")
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def frontmatter(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError("skill is missing opening frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise AssertionError("skill is missing closing frontmatter") from None
    fields = {}
    for line in lines[1:end]:
        if not line or line.startswith((" ", "\t", "#")):
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"unsupported frontmatter line: {line}")
        fields[key.strip()] = value.strip()
    return fields


class DshBundleBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="story-dsh-bundle-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "dsh-plugin"

    def build(self, output=None, force=False):
        return bundle.build(output or self.output, force)

    def test_build_is_a_complete_one_level_dsh_package(self):
        result = self.build()
        self.assertTrue(result["ok"])
        self.assertEqual(result["package"], "story-skill-dsh")
        self.assertEqual(result["files"], 81)
        self.assertEqual(result["skill_files"], 73)
        self.assertEqual(len(result["skills"]), 8)
        self.assertTrue(Path(result["path"]).is_dir())
        self.assertTrue(Path(result["receipt"]).is_file())

        manifest = json.loads((self.output / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "story-skill-dsh")
        self.assertEqual(manifest["version"], result["version"])
        self.assertEqual(manifest["dsh"]["bundle"]["patch"], "./cordis.patch.yml")
        self.assertIn("skills/**", manifest["files"])
        self.assertTrue((self.output / "cordis.patch.yml").is_file())
        self.assertTrue((self.output / "index.js").is_file())

        patch = (self.output / "cordis.patch.yml").read_text(encoding="utf-8")
        for required in ("story-skill-dsh", "@deepseek-ai/dsh-skill-filesystem",
                         "providerName: story-skill", "includeDefaultRoots: false",
                         "watch: false", "bundledSkillDir"):
            self.assertIn(required, patch)

        for locale in ("en.json", "zh.json"):
            meta = json.loads((self.output / "locale" / locale).read_text(encoding="utf-8"))["meta"]
            self.assertTrue(meta["title"])
            self.assertTrue(meta["description"])
        self.assertTrue((self.output / "icon.svg").read_bytes().startswith(b"<svg"))

        skills = self.output / "skills"
        self.assertEqual({path.name for path in skills.iterdir() if path.is_dir()}, set(result["skills"]))
        self.assertEqual(len(list(skills.rglob("SKILL.md"))), 8)
        self.assertEqual(sum(path.is_file() for path in skills.rglob("*")), 73)
        for path in skills.glob("*/SKILL.md"):
            fields = frontmatter(path)
            self.assertEqual(fields["name"], path.parent.name)
            self.assertRegex(fields["name"], NAME)
            self.assertTrue(fields["description"])
            self.assertLessEqual(len(fields["description"]), 500)
        self.assertFalse(any(path.is_symlink() for path in self.output.rglob("*")))

        receipt = json.loads(Path(result["receipt"]).read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema"], 1)
        self.assertEqual(receipt["sha256"], result["sha256"])
        self.assertEqual(len(receipt["manifest"]), 81)
        self.assertEqual(receipt["manifest"]["skills/story-skill/SKILL.md"],
                         hashlib.sha256((self.output / "skills/story-skill/SKILL.md").read_bytes()).hexdigest())

    def test_archive_contains_the_package_root(self):
        archive = self.root / "story-skill-dsh-test.zip"
        result = bundle.build(self.output, archive=archive)
        self.assertEqual(result["archive"], str(archive))
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as bundle_file:
            names = bundle_file.namelist()
            self.assertIn("package.json", names)
            self.assertIn("cordis.patch.yml", names)
            self.assertIn("skills/story-skill/SKILL.md", names)
            self.assertEqual(len(names), 81)
            self.assertEqual(bundle_file.testzip(), None)
        self.assertEqual(len(result["archive_sha256"]), 64)

    def test_existing_output_requires_force_and_rebuild_is_stable(self):
        first = self.build()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.build()
        second = self.build(force=True)
        self.assertEqual(second["sha256"], first["sha256"])
        self.assertTrue(self.output.is_dir())

    def test_output_inside_sources_is_rejected(self):
        for output in (ROOT / "dsh" / "bundle", ROOT / "skills" / "bundle", ROOT):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, "outside|contain"):
                    self.build(output)

    def test_output_inside_dsh_home_is_rejected(self):
        dsh_home = self.root / "dsh-home"
        dsh_home.mkdir()
        with patch.dict(os.environ, {"DSH_HOME": str(dsh_home)}):
            with self.assertRaisesRegex(ValueError, "DSH_HOME"):
                self.build(dsh_home / "bundle")

    def test_package_version_tracks_the_runtime(self):
        version = package.source_version((ROOT / "skills/story-skill/scripts/story.py").read_bytes())
        manifest = json.loads((ROOT / "dsh/package.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], version)


if __name__ == "__main__":
    unittest.main()