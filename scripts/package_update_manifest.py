#!/usr/bin/env python3
"""Describe immutable installer bytes for verified, resumable range downloads.

Content-defined boundaries keep unchanged compressed regions reusable even when
an earlier file grows. The manifest never contains paths or alternative URLs:
clients fetch ranges from the already authenticated official release asset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat

SCHEMA = 1
MIN_CHUNK = 256 * 1024
AVG_CHUNK = 1024 * 1024
MAX_CHUNK = 4 * 1024 * 1024
MAX_PACKAGE = 2_000_000_000
MAX_MANIFEST = 1024 * 1024
MAX_CHUNKS = 4096
GEAR = tuple(int.from_bytes(hashlib.sha256(b'story-update-v1:' + bytes([i])).digest()[:8], 'little')
             for i in range(256))


def chunks(stream):
    pending = bytearray()
    rolling = 0
    for data in iter(lambda: stream.read(1024 * 1024), b''):
        for value in data:
            pending.append(value)
            rolling = ((rolling << 1) + GEAR[value]) & 0xffffffffffffffff
            length = len(pending)
            if length >= MIN_CHUNK and (rolling & (AVG_CHUNK - 1) == 0 or length == MAX_CHUNK):
                yield bytes(pending)
                pending.clear()
                rolling = 0
    if pending:
        yield bytes(pending)


def describe(package):
    package = Path(package)
    before = package.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_PACKAGE:
        raise ValueError('Expected a regular, nonempty installer no larger than 2 GB')
    if not package.name.isascii() or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._' for c in package.name):
        raise ValueError('Installer filename must be an ASCII release asset name')
    digest = hashlib.sha256()
    records = []
    offset = 0
    with package.open('rb') as stream:
        for block in chunks(stream):
            digest.update(block)
            records.append({'offset': offset, 'size': len(block), 'sha256': hashlib.sha256(block).hexdigest()})
            offset += len(block)
            if len(records) > MAX_CHUNKS:
                raise ValueError('Installer has too many download chunks')
    after = package.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or offset != before.st_size:
        raise ValueError('Installer changed while preparing its download manifest')
    return {'schema': SCHEMA, 'file': package.name, 'size': offset,
            'sha256': digest.hexdigest(), 'chunks': records}


def write_manifest(package):
    package = Path(package)
    result = describe(package)
    content = (json.dumps(result, ensure_ascii=True, separators=(',', ':')) + '\n').encode('ascii')
    if len(content) > MAX_MANIFEST:
        raise ValueError('Download manifest exceeds the client limit')
    target = package.with_name(package.name + '.download.json')
    # Release assets are immutable. A caller must choose a new output directory
    # rather than silently replacing a manifest belonging to another build.
    with target.open('xb') as stream:
        stream.write(content)
    checksum = target.with_name(target.name + '.sha256')
    with checksum.open('x', encoding='ascii') as stream:
        stream.write(hashlib.sha256(content).hexdigest() + '  ' + target.name + '\n')
    return {'manifest': str(target), 'chunks': len(result['chunks']),
            'size': result['size'], 'sha256': result['sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('packages', nargs='+', type=Path)
    arguments = parser.parse_args()
    print(json.dumps([write_manifest(path) for path in arguments.packages], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
