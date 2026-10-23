#!/usr/bin/env python3
"""Enforce the project's ban on Qt-family and PHP implementations/dependencies.

Historical prose and external URLs are not dependencies. Source scanning checks
imports, dependency declarations, build commands and implementation file names;
distribution scanning checks every shipped file, including vendor payloads.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys


FORBIDDEN_PACKAGE = re.compile(
    r'^(?:qt(?:[56]|base|declarative|webengine|tools|svg|charts|quick|widgets|core|gui|network)?'
    r'|pyside\d*|pyqt\d*(?:[-_]qt\d*)?|shiboken\d*|php(?:\d+(?:\.\d+)*)?)(?:[-_.]|$)', re.I)
FORBIDDEN_FILE = re.compile(
    r'^(?:(?:lib)?qt[56](?:core|gui|widgets|webengine|quick|qml|network|svg|printsupport|'
    r'multimedia|opengl|sql|concurrent|positioning|pdf|test|dbus|wayland|xml)'
    r'|qtwebengine|pyside\d*|pyqt\d*|shiboken\d*|php(?:\d+(?:\.\d+)*)?(?:ts)?(?:[-_.]|$))', re.I)
PHP_EXTENSIONS = {'.php', '.php3', '.php4', '.php5', '.php7', '.php8', '.phtml', '.phar'}
FORBIDDEN_BINARY = re.compile(
    r'^(?:(?:lib)?qt[456]?[a-z0-9_]*|libphp[0-9]*(?:ts)?|'
    r'(?:lib)?q(?:windows|cocoa|xcb|offscreen|minimal|direct2d|wayland[a-z0-9_-]*|'
    r'jpeg|gif|ico|svg|tiff|webp|sqlite|opensslbackend|schannelbackend))'
    r'\.(?:dll|pyd|dylib|framework|so(?:\.\d+)*)$', re.I)
SOURCE_EXTENSIONS = {'.py', '.cs', '.cpp', '.c', '.h', '.hpp', '.js', '.mjs', '.ts', '.tsx',
                     '.sh', '.ps1', '.bat', '.cmd', '.yml', '.yaml', '.toml', '.json', '.xml',
                     '.csproj', '.props', '.targets', '.txt', '.iss', '.ui', '.pro', '.pri'}
IMPLEMENTATION_EXTENSIONS = SOURCE_EXTENSIONS - {'.json', '.txt'}


def forbidden_path(name):
    path = Path(name)
    if path.suffix.lower() in PHP_EXTENSIONS:
        return True
    return any(FORBIDDEN_FILE.match(part) or FORBIDDEN_BINARY.fullmatch(part) or part.lower() in
               {'qt', 'qt5', 'qt6', 'qtbase', 'qtwebengine', 'php', 'php-src'} for part in path.parts)


def source_violations(path, relative):
    if path.suffix.lower() not in SOURCE_EXTENSIONS:
        return []
    try:
        text = path.read_text(encoding='utf-8-sig')
    except (UnicodeError, OSError):
        return [f'{relative}: cannot inspect source bytes']
    errors = []
    if path.suffix == '.py':
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return [f'{relative}: invalid Python source']
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split('.')[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module.split('.')[0]]
            elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                  and isinstance(node.args[0].value, str)
                  and (isinstance(node.func, ast.Name) and node.func.id == '__import__'
                       or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module')):
                modules = [node.args[0].value.split('.')[0]]
            if any(FORBIDDEN_PACKAGE.match(module) for module in modules):
                errors.append(f'{relative}:{node.lineno}: forbidden import')
    # Test fixtures deliberately mention rejected imports and packages as data.
    if relative.startswith('tests/') or path.name == Path(__file__).name:
        return errors
    patterns = [
        r'(?im)^\s*(?:from|import|using)\s+(?:PySide\d*|PyQt\d*|Qt\w*|shiboken\d*)\b',
        r'(?im)^\s*#\s*include\s*[<"](?:Qt\w*[/\.]|Q(?:Application|Widget|Object|MainWindow)[>"])',
        r'(?im)\bfind_package\s*\(\s*Qt[56]?\b',
        r'(?im)(?:PackageReference|package)\s+Include=[\'"](?:Qt\b|Qt[56]|QtSharp|Qml\.Net|PySide|PyQt|PHP)',
        r'(?im)^\s*(?:PySide\d*|PyQt\d*|shiboken\d*|qtbase\d*|php\d*)\s*(?:[=<>~!]|$)',
        r'(?im)[\'"](?:pyside\d*|pyqt\d*|shiboken\d*|qtbase\d*|php)[\'"]\s*:',
        r'(?im)^\s*(?:-\s*)?(?:run:\s*)?(?:php(?:\.exe)?|qmake(?:\.exe)?|qtpaths)\s+',
        r'(?im)\b(?:pip|pip3)\s+install\s+(?:[^\n]*?\s)?(?:PySide\d*|PyQt\d*|shiboken\d*)\b',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            errors.append(f'{relative}:{text.count(chr(10), 0, match.start()) + 1}: forbidden dependency or command')
    if path.suffix.lower() in IMPLEMENTATION_EXTENSIONS and '<?php' in text.lower():
        errors.append(f'{relative}: PHP implementation')
    return errors


def check_tree(root):
    """Check a complete staged/installed distribution, with no path exclusions."""
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        return ['distribution root must be a real directory']
    errors = []
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append(f'{relative}: linked payload cannot be inspected')
        if forbidden_path(relative):
            errors.append(f'{relative}: forbidden technology payload')
        if path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS:
            errors.extend(source_violations(path, relative))
    return sorted(set(errors))


def assert_clean_tree(root):
    errors = check_tree(root)
    if errors:
        raise RuntimeError('Forbidden technology check failed:\n' + '\n'.join(errors))


def check_source(root):
    root = Path(root).resolve()
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=root, check=True, capture_output=True)
    errors = []
    for name in sorted(set(result.stdout.decode('utf-8').split('\0')) - {''}):
        path = root / name
        if not path.exists():  # A tracked legacy file may be deleted in this change.
            continue
        if name.startswith(('benchmarks/', 'docs/releases/')):
            continue  # Historical receipts are evidence, never build inputs.
        if forbidden_path(name):
            errors.append(f'{name}: forbidden implementation or vendor payload')
        if path.is_file():
            errors.extend(source_violations(path, name))
    return sorted(set(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--source', action='store_true')
    args = parser.parse_args()
    errors = check_source(args.root) if args.source else check_tree(args.root)
    print(json.dumps({'ok': not errors, 'policy': 'no-qt-no-php', 'errors': errors}, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == '__main__':
    sys.exit(main())
