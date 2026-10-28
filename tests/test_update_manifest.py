"""Installer download plans cover exact bytes and reuse unchanged regions."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import random
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('update_manifest', ROOT / 'scripts/package_update_manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class UpdateManifestTests(unittest.TestCase):
    def test_exact_coverage_determinism_digest_and_immutable_sidecars(self):
        data = random.Random(81).randbytes(7 * 1024 * 1024 + 23)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'story-workbench-0.3.0-windows-x86_64-setup.exe'
            path.write_bytes(data)
            result = manifest.write_manifest(path)
            encoded = Path(result['manifest']).read_bytes()
            plan = json.loads(encoded)
            self.assertEqual(plan, manifest.describe(path))
            self.assertEqual(plan['sha256'], hashlib.sha256(data).hexdigest())
            offset = 0
            for block in plan['chunks']:
                self.assertEqual(block['offset'], offset)
                self.assertGreater(block['size'], 0)
                self.assertLessEqual(block['size'], manifest.MAX_CHUNK)
                self.assertEqual(block['sha256'], hashlib.sha256(data[offset:offset + block['size']]).hexdigest())
                offset += block['size']
            self.assertEqual(offset, len(data))
            self.assertLess(len(encoded), manifest.MAX_MANIFEST)
            self.assertEqual(Path(result['manifest'] + '.sha256').read_text().split()[0], hashlib.sha256(encoded).hexdigest())
            with self.assertRaises(FileExistsError):
                manifest.write_manifest(path)

    def test_inserting_bytes_reuses_content_after_shifted_offsets(self):
        original = random.Random(32).randbytes(12 * 1024 * 1024)
        modified = original[:1_200_000] + b'changed chapter interface' * 37 + original[1_200_000:]
        old = {hashlib.sha256(block).hexdigest() for block in manifest.chunks(io.BytesIO(original))}
        reused = sum(len(block) for block in manifest.chunks(io.BytesIO(modified)) if hashlib.sha256(block).hexdigest() in old)
        self.assertGreater(reused, len(original) // 2)

    def test_empty_linked_and_unbounded_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'empty.zip'
            path.touch()
            with self.assertRaises(ValueError): manifest.describe(path)
            path.write_bytes(b'x')
            link = Path(raw) / 'link.zip'
            try: link.symlink_to(path)
            except OSError: return
            with self.assertRaises(ValueError): manifest.describe(link)
