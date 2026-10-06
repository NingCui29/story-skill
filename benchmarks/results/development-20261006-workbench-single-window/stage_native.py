"""Refresh private app clones; preserve their unchanged bundled runtime."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess

EVIDENCE = Path(__file__).resolve().parent
REPO = EVIDENCE.parents[2]
BACKUP = Path(json.loads((EVIDENCE / 'baseline.json').read_text())['backup'])
COMPILED = BACKUP / 'StoryWorkbench-new'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


entries = json.loads((EVIDENCE / 'staged-installation.json').read_text())
sources = {name: sha(REPO / 'desktop/macos' / name)
           for name in ('StoryWorkbench.swift', 'ClientInstance.swift')}
for entry in entries:
    stage = Path(entry['stage'])
    resources = stage / 'Contents/Resources'
    manifest = dict(entry['manifest'])
    manifest.update(native_source_sha256=sources['StoryWorkbench.swift'], native_sources=sources,
                    compiled_native_binary_sha256=sha(COMPILED), local_single_window_update='2026-10-06')
    for name in ('native_binary_sha256', 'signed_native_binary_sha256'):
        manifest.pop(name, None)
    shutil.copy2(COMPILED, stage / 'Contents/MacOS/StoryWorkbench')
    (resources / 'build-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    for arguments in (['--force', '--deep', '--sign', '-'], ['--verify', '--deep', '--strict']):
        subprocess.run(['codesign', *arguments, str(stage)], check=True, capture_output=True, timeout=60)
    entry.update(manifest=manifest, signed_native_binary_sha256=sha(stage / 'Contents/MacOS/StoryWorkbench'),
                 signature_verified=True)
(EVIDENCE / 'staged-installation.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'ok': True, 'staged_apps': len(entries), 'native_sources': sources}))
