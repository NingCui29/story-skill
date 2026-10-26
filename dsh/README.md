# Story Skill · DeepSeek Harness bundle

This directory is the DeepSeek Harness (DSH) bundle for Story Skill. It packages the eight `story-skill*` skills and mounts them through DSH's filesystem skill provider, so the suite appears in the Plugins page and in the session skill catalog.

## Build

The source `skills/` directory is canonical. Build a complete bundle (script, patch, locales, icon, and all 69 skill files) into an ignored `dist/` directory:

```bash
python3 -B -X utf8 scripts/package_dsh.py --output dist/dsh-plugin
```

The build script verifies the reviewed 69-file suite, the eight one-level skill roots, frontmatter name/description, and that no links or unreviewed files enter the package.

## Install

Use the Harness Plugins page (or the `plugin_manager` tool with `install_bundle`) and select the absolute path of the built directory, for example:

```text
D:\Developer\WorkSpace\story-skill\dist\dsh-plugin
```

The bundle is private and installed from a local directory; it declares no runtime package dependencies. The patch mounts a second filesystem provider (`providerName: story-skill`) with `bundledSkillDir` pointing at this package's `skills/` directory.

Install or update while Harness is stopped when possible. Windows keeps handles on loaded bundle files, so replacing a running bundle can fail with `EPERM`; if the Plugins page reports that a restart is required, restart Harness before updating again.

After installation, open a new turn and check that eight `story-skill*` entries appear in the skill catalog. Users can invoke `/story-skill` or `/story-skill-plan`; the model can load the full body with the `skill` tool.

## Precedence

DSH resolves duplicate names by discovery rank. The bundle's skills are the bundled root (rank 600), so project `.dsh/skills`, project `.agents/skills`, custom roots, and user roots take precedence when they contain the same names. Use the bundle as the only installation for a plugin-only setup, or keep a project/user copy when you want live, per-workspace overrides. Do not keep a stale copy and assume the bundle is serving it.

## Update

Rebuild after changing `skills/`, then reinstall or update the bundle in the Plugins page. The bundle contains copies, not links, so source edits do not reach Harness until the rebuild and reinstall complete. Restart Harness when the Plugins page reports that a running module must be replaced.

The bundled provider sets `watch: false`: bundled skills change only on rebuild, so avoiding a filesystem watcher prevents Windows handles on an immutable package during replacement.

## Files

| Path | Purpose |
|---|---|
| `package.json` | Bundle manifest (`dsh.bundle.patch`), exports, files and icon |
| `cordis.patch.yml` | Inserts the no-op host plugin and the Story Skill filesystem provider |
| `index.js` | Host plugin entry; owns no runtime behavior |
| `locale/en.json`, `locale/zh.json` | Plugins-page title and description |
| `icon.svg` | Original bundle icon |
| `README.md` | This file |
| `LICENSE` | Story Skill root license |
| `skills/**` | Generated at build time from the repository `skills/` suite |

The root project license applies. See `../LICENSE` and `../docs/licensing.md`.