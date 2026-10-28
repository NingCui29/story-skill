# Story Skill 0.6.22 — Codex local plugin

This bundle contains one plugin with all eight sibling skills and their shared
Python runtime. It contains no novels, account configuration, or installed apps.
Python 3.10+ and access to your chosen book directory are required. Research and
image generation use tools already available in the current Codex session.

If using the ZIP, extract it into a new directory first:

```sh
python3 -m zipfile -e story-skill-codex-0.6.22.zip story-skill-codex-0.6.22
cd story-skill-codex-0.6.22
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
