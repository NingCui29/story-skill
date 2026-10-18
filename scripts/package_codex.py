#!/usr/bin/env python3
"""Build a bounded Codex plugin and a self-contained local marketplace bundle."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("story_suite_package", ROOT / "scripts/package.py")
suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suite)

PLUGIN_NAME = "story-skill"
MARKETPLACE_NAME = "story-skill-local"
PLUGIN_PREFIX = "plugins/story-skill/"
MANIFEST = ".codex-plugin/plugin.json"
ICON = "assets/story-skill.svg"


def _ordinary_file(relative):
    """Read one explicit wrapper file, rejecting links in its source path."""
    root = Path(ROOT).absolute()
    path = root
    for component in PurePosixPath(relative).parts:
        path = path / component
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"Plugin source file is missing: {relative}")
        if suite.linked(path):
            raise ValueError(f"Refusing linked plugin source: {relative}")
    if not path.is_file():
        raise ValueError(f"Plugin source must be an ordinary file: {relative}")
    return path.read_bytes()


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Plugin metadata contains a duplicate key: {key}")
        result[key] = value
    return result


def _relative_reference(value, label):
    if (not isinstance(value, str) or not value.startswith("./") or
            "\\" in value or "\x00" in value):
        raise ValueError(f"{label} must be a local relative path beginning with ./")
    relative = PurePosixPath(value[2:])
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} must stay inside the plugin directory")
    return relative.as_posix()


def plugin_skill_entries(source):
    """Namespace only Codex UI prompt references; preserve all other source bytes."""
    names = sorted(suite.SKILL_NAMES, key=lambda name: (-len(name), name))
    reference = re.compile(
        rb"(?<![A-Za-z0-9_:$-])\$(" + b"|".join(re.escape(name.encode("ascii")) for name in names)
        + rb")(?![A-Za-z0-9_:-])")
    prompt_line = re.compile(rb"(?m)^([ \t]*default_prompt:[ \t]*)([^\r\n]*)(\r?\n|$)")
    ui_paths = {name + "/agents/openai.yaml" for name in names}

    def adapt_prompt(match):
        value = reference.sub(lambda token: b"$" + PLUGIN_NAME.encode("ascii") + b":" + token[1], match[2])
        return match[1] + value + match[3]

    return [(name, prompt_line.sub(adapt_prompt, raw) if name in ui_paths else raw) for name, raw in source]


def _plugin_snapshot():
    """Snapshot the reviewed skills, adapting UI references for plugin loading."""
    source = suite.source_entries()
    adapted = plugin_skill_entries(source)
    version = suite.source_version(dict(source)["story-skill/scripts/story.py"])
    wrapper = {name: _ordinary_file(name) for name in (MANIFEST, ICON, "LICENSE")}
    try:
        manifest = json.loads(wrapper[MANIFEST].decode("utf-8"), object_pairs_hook=_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Plugin metadata must be valid UTF-8 JSON") from error
    if not isinstance(manifest, dict) or manifest.get("name") != PLUGIN_NAME:
        raise ValueError("Plugin metadata name must be story-skill")
    if manifest.get("version") != version:
        raise ValueError("Plugin metadata version must match the packaged runtime version")
    skills = _relative_reference(manifest.get("skills"), "Plugin skills")
    if skills != "skills" or manifest["skills"] != "./skills/":
        raise ValueError("Plugin skills must be ./skills/ and preserve all sibling skills")
    interface = manifest.get("interface")
    if not isinstance(interface, dict) or not isinstance(interface.get("displayName"), str):
        raise ValueError("Plugin metadata must include interface.displayName")
    for field in ("composerIcon", "logo"):
        relative = _relative_reference(interface.get(field), f"Plugin interface.{field}")
        if relative != ICON or relative not in wrapper:
            raise ValueError(f"Plugin interface.{field} must reference the packaged {ICON}")
    for field in ("mcpServers", "apps", "hooks", "commands"):
        if manifest.get(field):
            raise ValueError(f"This skills-only plugin builder does not package {field}")
    if wrapper["LICENSE"] != dict(source)["story-skill/LICENSE"]:
        raise ValueError("Root LICENSE must match the packaged Story Skill license")
    entries = sorted([*(('skills/' + name, raw) for name, raw in adapted), *wrapper.items()])
    original = dict(source)
    adaptation = {
        "source_payload_files": len(source),
        "adapted_ui_files": sum(raw != original[name] for name, raw in adapted),
    }
    return entries, version, adaptation


def plugin_entries():
    """Return the plugin payload snapshot and runtime version."""
    entries, version, _ = _plugin_snapshot()
    return entries, version


def marketplace_entries(payload, version):
    marketplace = {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": "Story Skill Local"},
        "plugins": [{
            "name": PLUGIN_NAME,
            "source": {"source": "local", "path": "./plugins/story-skill"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }],
    }
    readme = f"""# Story Skill {version} — Codex local plugin

This bundle contains one plugin with all eight sibling skills and their shared
Python runtime. It contains no novels, account configuration, or installed apps.
Python 3.10+ and access to your chosen book directory are required. Research and
image generation use tools already available in the current Codex session.

If using the ZIP, extract it into a new directory first:

```sh
python3 -m zipfile -e story-skill-codex-{version}.zip story-skill-codex-{version}
cd story-skill-codex-{version}
codex plugin marketplace add "$PWD"
codex plugin add story-skill@story-skill-local
```

When using the generated directory directly, run the two Codex commands from
this directory. If your CLI has no `plugin add` command, use the Codex plugin
directory interface to install Story Skill from the registered local marketplace.
Keep this marketplace directory available while Codex uses it.

Codex loads these plugin skills with the `story-skill:` namespace. For explicit
invocation, use `$story-skill:story-skill` for the main entry or, for example,
`$story-skill:story-skill-plan` for planning. The eight UI default prompts use
these same namespaced references. You can also describe your task naturally.

Keep book files outside the plugin installation. Existing manually installed
Story Skill skills should be reviewed before enabling another copy with the same
skill names. Installing this bundle does not publish chapters or enable new tools.

The complete license is at `plugins/story-skill/LICENSE`. Building this local
bundle does not publish it or install it in your account.
"""
    return sorted([
        (".agents/plugins/marketplace.json", (json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n").encode("utf-8")),
        ("README.md", readme.encode("utf-8")),
        *((PLUGIN_PREFIX + name, raw) for name, raw in payload),
    ])


def _inventory(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if suite.linked(path):
            raise ValueError(f"Refusing linked staged bundle content: {path}")
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = path.read_bytes()
        elif not path.is_dir():
            raise ValueError(f"Refusing special staged bundle content: {path}")
    return result


def _output_parent(value):
    parent = Path(value).expanduser().absolute()
    for path in (parent, *parent.parents):
        if os.path.lexists(path) and suite.linked(path):
            raise ValueError(f"Refusing linked output directory: {path}")
    if parent.exists() and not parent.is_dir():
        raise ValueError("Output directory must be a directory")
    resolved = parent.resolve()
    for source in (Path(suite.SOURCE), Path(ROOT) / ".codex-plugin", Path(ROOT) / "assets"):
        if resolved == source.resolve() or resolved.is_relative_to(source.resolve()):
            raise ValueError("Output directory must be outside plugin source directories")
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def _refuse_existing(paths):
    for path in paths:
        if os.path.lexists(path):
            raise FileExistsError(f"Refusing to replace an existing build output: {path}; use a new output directory")


def _unlink_owned(path, staged):
    """Rollback only a hard link to this build's own temporary file."""
    try:
        current, expected = path.lstat(), staged.stat()
        if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino):
            path.unlink()
    except FileNotFoundError:
        pass


def package(output_dir=None):
    payload, version, adaptation = _plugin_snapshot()
    entries = marketplace_entries(payload, version)
    parent = _output_parent(output_dir if output_dir is not None else Path(ROOT) / "dist")
    name = f"story-skill-codex-{version}"
    archive, checksum, marketplace_root = parent / (name + ".zip"), parent / (name + ".zip.sha256"), parent / name
    _refuse_existing((archive, checksum, marketplace_root))
    with tempfile.TemporaryDirectory(prefix=".story-skill-codex-build-", dir=parent) as temporary:
        stage = Path(temporary)
        staged_market = stage / name
        staged_market.mkdir()
        for relative, raw in entries:
            target = staged_market / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        if _inventory(staged_market) != dict(entries):
            raise RuntimeError("Staged marketplace differs from the source snapshot")
        staged_archive = stage / archive.name
        with zipfile.ZipFile(staged_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
            for relative, raw in entries:
                info = zipfile.ZipInfo(relative, date_time=(2026, 9, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                handle.writestr(info, raw)
        suite.validate_archive(staged_archive, entries)
        digest = hashlib.sha256(staged_archive.read_bytes()).hexdigest()
        staged_checksum = stage / checksum.name
        staged_checksum.write_bytes(f"{digest}  {archive.name}\n".encode("ascii"))
        _refuse_existing((archive, checksum, marketplace_root))
        try:
            # Hard links publish each file without replacing a raced destination.
            os.link(staged_archive, archive)
            os.link(staged_checksum, checksum)
            # As in the directory installer, Windows rename is no-replace; the
            # POSIX preflight cannot provide a strong CAS against external writers.
            _refuse_existing((marketplace_root,))
            os.rename(staged_market, marketplace_root)
        except BaseException:
            _unlink_owned(checksum, staged_checksum)
            _unlink_owned(archive, staged_archive)
            raise
    return {
        "archive": str(archive), "checksum": str(checksum),
        "marketplace_root": str(marketplace_root),
        "plugin_root": str(marketplace_root / "plugins/story-skill"),
        "version": version, "skill_count": len(suite.skill_names(version)),
        "payload_files": len(payload), "bundle_files": len(entries), "sha256": digest,
        **adaptation,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", help="Parent for new ZIP, checksum and marketplace directory; defaults to dist/")
    args = parser.parse_args()
    try:
        print(json.dumps(package(args.output_dir), ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
