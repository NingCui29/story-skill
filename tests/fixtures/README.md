# Schema 1 migration fixture

`schema1_runtime.py` preserves the version 0.2.0 schema 1 behavior needed to
create representative books for migration and rollback tests. Two user-facing
brand strings were updated; database and export logic are unchanged. The fixture
is source code under the repository's MIT license and is never included in an
installable release package.

SHA-256: `70c8a0294d72103cb2232834ac951f30abef6ab20e940cb0303aca1858910cb3`
(64,267 bytes). Tests verify the digest before loading the fixture. This keeps
schema migration tests runnable in a fresh checkout without relying on ignored
local build artifacts.
