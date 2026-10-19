"""Exercise a supplied desktop payload with isolated synthetic book data only.

The same probe runs against copied build sources in unit tests and the installed
runtime in Windows CI. Native trash is called only for a nonexistent path.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys


def probe(scripts, area):
    scripts = Path(scripts).resolve()
    area = Path(area).resolve()
    area.mkdir()  # Refuse any existing location; never reuse user book data.
    specification = importlib.util.spec_from_file_location('desktop_payload_probe_story', scripts / 'story.py')
    story = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(story)
    for module in (story, story.workbench, story.workbench_books, story.workbench_archives):
        if Path(module.__file__).resolve().parent != scripts:
            raise AssertionError('Desktop runtime imported a module outside its payload.')
    state_dir = area / '合成书架'
    state_dir.mkdir()
    manager = story.workbench_books.BookManager(story.workbench)
    root = area / '合成作品 中文 空格'
    entries, digest, created = manager.create(state_dir, {}, None, str(root), '桌面隔离验证', 'short')
    entry = story.workbench._library_entry(root)
    if (not created['created'] or not created['registered']
            or entry['book_id'] != next(iter(entries.values()))['book_id']
            or entry['progress']['formal_chapters'] != 0):
        raise AssertionError('Synthetic book creation or shelf identity failed.')
    candidate = root / '.story/drafts/合成候选.md'
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text('这是合成测试候选，尚未正式采用。\n', encoding='utf-8')
    binary = bytes(range(256)) * 4
    (root / '合成素材.bin').write_bytes(binary)
    archive = area / '合成备份.zip'
    backup = story.workbench_archives.create_archive(root, archive, entry['book_id'])
    restored_root = area / '合成恢复副本 中文 空格'
    restored = story.workbench_archives.restore_archive(archive, restored_root)
    restored_entry = story.workbench._library_entry(restored_root, entry)
    if (not backup['ok'] or restored['book_id'] != entry['book_id']
            or restored_entry['book_id'] != entry['book_id']
            or restored_entry['progress']['formal_chapters'] != 0
            or (restored_root / '.story/drafts/合成候选.md').read_bytes() != candidate.read_bytes()
            or (restored_root / '合成素材.bin').read_bytes() != binary):
        raise AssertionError('Synthetic backup/restore changed identity or saved files.')
    for book_root in (root, restored_root):
        with sqlite3.connect(book_root / '.story/state.sqlite3') as database:
            if database.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise AssertionError('Synthetic state database failed integrity check.')
    # Test the actual native bridge's failure path without recycling any data.
    missing = area / '此目录从未创建-回收站拒绝验证'
    if missing.exists():
        raise AssertionError('Missing-path trash fixture unexpectedly exists.')
    trash_refused = False
    try:
        story.workbench_books.move_to_trash(missing)
    except OSError:
        trash_refused = True
    if not trash_refused or not root.is_dir() or not restored_root.is_dir():
        raise AssertionError('Missing-path trash did not fail safely.')
    return {
        'ok': True, 'platform': sys.platform, 'story_version': story.VERSION,
        'isolated_payload': True, 'created_and_registered': True,
        'backup_verified': True, 'restore_identity_preserved': True,
        'candidate_and_binary_preserved': True, 'sqlite_integrity_verified': True,
        'missing_trash_path_refused': trash_refused,
        'native_trash_bridge_checked': sys.platform in ('win32', 'darwin'),
        'existing_book_files_recycled': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scripts', type=Path, required=True)
    parser.add_argument('--area', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    arguments = parser.parse_args()
    result = probe(arguments.scripts, arguments.area)
    output = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if arguments.report:
        arguments.report.write_text(output, encoding='utf-8')
    print(output, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
