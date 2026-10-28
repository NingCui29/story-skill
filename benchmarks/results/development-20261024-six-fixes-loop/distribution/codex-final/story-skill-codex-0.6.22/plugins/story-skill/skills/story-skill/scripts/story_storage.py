"""SQLite storage primitives. Migration is explicit and keeps a verified backup."""
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS core_objects(sha TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TRIGGER core_objects_no_update BEFORE UPDATE ON core_objects BEGIN
 SELECT RAISE(ABORT,'stored evidence objects are immutable');
END;
CREATE TRIGGER core_objects_no_delete BEFORE DELETE ON core_objects BEGIN
 SELECT RAISE(ABORT,'stored evidence objects are immutable');
END;
CREATE TABLE chapter_state(chapter INTEGER PRIMARY KEY, sha TEXT NOT NULL REFERENCES core_objects(sha),
 summary TEXT NOT NULL, receipt TEXT NOT NULL, input_hash TEXT NOT NULL, imported INTEGER NOT NULL DEFAULT 0);
CREATE TABLE artifact_state(path TEXT PRIMARY KEY, sha TEXT NOT NULL REFERENCES core_objects(sha), written_sha TEXT);
CREATE INDEX artifact_pending ON artifact_state(path) WHERE written_sha IS NULL OR written_sha<>sha;
CREATE TABLE card_index(id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, status TEXT NOT NULL, critical INTEGER NOT NULL, due INTEGER, scope TEXT NOT NULL);
CREATE INDEX card_critical ON card_index(scope,critical,status);
CREATE INDEX card_due ON card_index(scope,kind,status,due);
CREATE TABLE card_tags(tag TEXT NOT NULL, id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE, PRIMARY KEY(tag,id));
CREATE INDEX card_tags_id ON card_tags(id);
CREATE TABLE integrity_audits(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, checked INTEGER NOT NULL,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE source_stats(source TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE, characters INTEGER NOT NULL);
CREATE TRIGGER source_stats_insert AFTER INSERT ON sources BEGIN
 INSERT INTO source_stats VALUES(new.id,length(new.text));
END;
CREATE TABLE chunk_classification(source TEXT NOT NULL, ordinal INTEGER NOT NULL, noncontent INTEGER NOT NULL,
 PRIMARY KEY(source,ordinal), FOREIGN KEY(source,ordinal) REFERENCES chunks(source,ordinal) ON DELETE CASCADE);
CREATE INDEX chunk_noncontent ON chunk_classification(source,ordinal) WHERE noncontent=1;
CREATE INDEX chunk_pending ON chunks(source,ordinal) WHERE analysis IS NULL;
CREATE VIEW chapters AS SELECT s.chapter,b.text,s.sha,s.summary,s.receipt,s.input_hash,s.imported
 FROM chapter_state s JOIN core_objects b ON b.sha=s.sha;
CREATE TRIGGER chapters_insert INSTEAD OF INSERT ON chapters BEGIN
 INSERT OR IGNORE INTO core_objects VALUES(new.sha,new.text);
 INSERT INTO chapter_state VALUES(new.chapter,new.sha,new.summary,new.receipt,new.input_hash,coalesce(new.imported,0))
 ON CONFLICT(chapter) DO UPDATE SET sha=excluded.sha,summary=excluded.summary,receipt=excluded.receipt,
 input_hash=excluded.input_hash,imported=excluded.imported;
END;
CREATE TRIGGER chapters_update INSTEAD OF UPDATE ON chapters BEGIN
 INSERT OR IGNORE INTO core_objects VALUES(new.sha,new.text);
 UPDATE chapter_state SET sha=new.sha,summary=new.summary,receipt=new.receipt,input_hash=new.input_hash,
 imported=new.imported WHERE chapter=old.chapter;
END;
CREATE TRIGGER chapters_delete INSTEAD OF DELETE ON chapters BEGIN
 DELETE FROM chapter_state WHERE chapter=old.chapter;
END;
CREATE VIEW artifacts AS SELECT s.path,b.text AS content,s.sha,s.written_sha
 FROM artifact_state s JOIN core_objects b ON b.sha=s.sha;
CREATE TRIGGER artifacts_insert INSTEAD OF INSERT ON artifacts BEGIN
 INSERT OR IGNORE INTO core_objects VALUES(new.sha,new.content);
 INSERT INTO artifact_state VALUES(new.path,new.sha,new.written_sha)
 ON CONFLICT(path) DO UPDATE SET sha=excluded.sha;
END;
CREATE TRIGGER artifacts_update INSTEAD OF UPDATE ON artifacts BEGIN
 INSERT OR IGNORE INTO core_objects VALUES(new.sha,new.content);
 UPDATE artifact_state SET sha=new.sha,written_sha=new.written_sha WHERE path=old.path;
END;
CREATE TRIGGER artifacts_delete INSTEAD OF DELETE ON artifacts BEGIN
 DELETE FROM artifact_state WHERE path=old.path;
END;
"""


def execute_schema(db, script):
    # executescript commits a pending transaction: execute complete statements instead.
    statement = ""
    for line in script.splitlines(True):
        statement += line
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("Incomplete schema statement")


@contextmanager
def operation_lock(book):
    """OS-released per-book writer/export lock; no stale lock deletion heuristics."""
    if getattr(book, "_lock_depth", 0):
        book._lock_depth += 1
        try:
            yield
        finally:
            book._lock_depth -= 1
        return
    path = book.root / ".story/operation.lock"
    # root is already validated by Book; lock path must also reject links.
    path = book.safe_path(book.root, ".story/operation.lock")
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 10
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    book.fail("book_busy", "Another process is writing or exporting this book; retry later")
                time.sleep(.025)
        book._lock_depth = 1
        try:
            yield
        finally:
            book._lock_depth = 0
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def migrate(core, root):
    """Upgrade schema 1 atomically. Backup is consistent even if a WAL exists."""
    root = Path(root).expanduser().resolve()
    path = core.safe_path(root, ".story/state.sqlite3")
    if not path.is_file():
        core.fail("book_missing", "No state database to migrate")
    from types import SimpleNamespace
    owner = SimpleNamespace(root=root, safe_path=core.safe_path, fail=core.fail)
    with operation_lock(owner):
        db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        backup_path = core.safe_path(root, f".story/migration-backups/schema1-{uuid.uuid4().hex}.sqlite3")
        try:
            schema = json.loads(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0])
            if schema == core.SCHEMA_VERSION:
                return {"migrated": False, "schema": schema}
            if schema != 1:
                core.fail("schema_mismatch", "Unsupported schema; no changes made", schema=schema)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            with backup_path.open("xb"):
                pass
            with closing(sqlite3.connect(backup_path)) as backup:
                db.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    core.fail("backup_failed", "Migration backup failed integrity check")
                backup_revision = backup.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0] != backup_revision:
                core.fail("stale_revision", "State changed after backup; retry migration")
            for table in ("chapters", "artifacts"):
                db.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
            execute_schema(db, SCHEMA)
            db.execute("INSERT INTO source_stats SELECT id,length(text) FROM sources")
            for module in (core.search, core.world, core.history):
                execute_schema(db, module.SCHEMA)
            for row in db.execute("SELECT * FROM legacy_chapters"):
                if core.digest(row["text"]) != row["sha"]:
                    core.fail("state_corrupt", "Stored chapter hash mismatch", chapter=row["chapter"])
                db.execute("INSERT INTO chapters VALUES (?,?,?,?,?,?,?)", tuple(row))
                core.search.upsert(db, "chapter", str(row["chapter"]), row["text"], {"chapter": row["chapter"], "summary": row["summary"]})
                core.search.upsert(db, "chapter_summary", str(row["chapter"]), row["summary"], {"chapter": row["chapter"]})
            for row in db.execute("SELECT * FROM legacy_artifacts"):
                if core.digest(row["content"]) != row["sha"]:
                    core.fail("state_corrupt", "Stored artifact hash mismatch", path=row["path"])
                db.execute("INSERT INTO artifacts VALUES (?,?,?,?)", tuple(row))
            for row in db.execute("SELECT id,data FROM cards"):
                core.index_card(db, json.loads(row["data"]))
            # Preserve old event semantics; body references replace only manuscript fields.
            for row in db.execute("SELECT seq,kind,data FROM events WHERE kind IN ('commit_chapter','replace_chapter')"):
                value = core.compact_event(db, row["kind"], json.loads(row["data"]))
                db.execute("UPDATE events SET data=? WHERE seq=?", (core.dumps(value), row["seq"]))
            db.execute("DROP TABLE legacy_chapters")
            db.execute("DROP TABLE legacy_artifacts")
            db.execute("UPDATE meta SET value=? WHERE key='schema'", (core.dumps(core.SCHEMA_VERSION),))
            if db.execute("PRAGMA foreign_key_check").fetchall():
                core.fail("migration_failed", "Foreign key validation failed")
            db.commit()
            return {"migrated": True, "schema": core.SCHEMA_VERSION, "backup": str(backup_path),
                    "rollback": "Keep the schema1 backup. To roll back, stop all book processes and restore it to a separate book copy with v0.2.0; later schema2 changes are not in that backup."}
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
