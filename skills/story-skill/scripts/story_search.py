"""Bounded, literal Chinese text search. Mutations join the caller's transaction.

Index one posting per distinct character/bigram per document, never per offset.
The shared core_objects table owns source bytes; (kind, key), not the content
hash, identifies a searchable source. Alias resolution belongs to the caller.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json


SCHEMA = """
CREATE TABLE IF NOT EXISTS core_objects(sha TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS search_documents(
    document_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL, key TEXT NOT NULL, sha TEXT NOT NULL,
    metadata TEXT NOT NULL, char_length INTEGER NOT NULL,
    UNIQUE(kind, key)
);
CREATE TABLE IF NOT EXISTS search_postings(
    gram TEXT NOT NULL, kind TEXT NOT NULL, document_id INTEGER NOT NULL,
    PRIMARY KEY(gram, kind, document_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS search_postings_document
    ON search_postings(document_id, gram);
CREATE TABLE IF NOT EXISTS search_grams(
    gram TEXT NOT NULL, kind TEXT NOT NULL, document_count INTEGER NOT NULL,
    PRIMARY KEY(gram, kind)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS search_kinds(
    kind TEXT PRIMARY KEY, document_count INTEGER NOT NULL
) WITHOUT ROWID;
"""

MAX_CANDIDATES = 4096
MAX_VERIFICATION_CHARS = 8_000_000
MAX_INTERSECTION_GRAMS = 8
MAX_RESULTS = 200


def fail(code, message, **details):
    raise ValueError(f"{code}: {message}")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inject(core):
    """Use the runtime's error/serialization/hash helpers without importing it."""
    global fail, dumps, digest
    fail, dumps, digest = core.fail, core.dumps, core.digest


def _identity(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        fail("invalid_input", f"{name} must be nonempty text of at most 1000 characters")
    return value


def _grams(text):
    return set(text) | {text[i:i + 2] for i in range(len(text) - 1)}


@contextmanager
def _write(db):
    # A top-level SAVEPOINT would commit when released. Begin explicitly so
    # callers may still roll back both their source write and this index write.
    if not db.in_transaction:
        db.execute("BEGIN")
    db.execute("SAVEPOINT search_write")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK TO search_write")
        db.execute("RELEASE search_write")
        raise
    else:
        db.execute("RELEASE search_write")


def _drop_postings(db, document_id, kind):
    grams = [row[0] for row in db.execute(
        "SELECT gram FROM search_postings WHERE document_id=?", (document_id,))]
    db.execute("DELETE FROM search_postings WHERE document_id=?", (document_id,))
    db.executemany("UPDATE search_grams SET document_count=document_count-1 "
                   "WHERE gram=? AND kind=?", ((gram, kind) for gram in grams))
    db.executemany("DELETE FROM search_grams WHERE gram=? AND kind=? "
                   "AND document_count=0", ((gram, kind) for gram in grams))


def upsert(db, kind, key, text, metadata=None):
    """Index the exact text; do not commit, normalize, or deduplicate identities."""
    kind, key = _identity(kind, "kind"), _identity(key, "key")
    if not isinstance(text, str):
        fail("invalid_input", "search text must be a string")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        fail("invalid_input", "search metadata must be an object")
    serialized, sha = dumps(metadata), digest(text)
    with _write(db):
        old = db.execute("SELECT document_id,sha,metadata FROM search_documents "
                         "WHERE kind=? AND key=?", (kind, key)).fetchone()
        db.execute("INSERT OR IGNORE INTO core_objects(sha,text) VALUES(?,?)", (sha, text))
        if old is not None and old[1] == sha:
            if old[2] != serialized:
                db.execute("UPDATE search_documents SET metadata=? WHERE document_id=?",
                           (serialized, old[0]))
            return {"id": key, "kind": kind, "sha": sha,
                    "status": "unchanged" if old[2] == serialized else "updated"}
        if old is None:
            cursor = db.execute("INSERT INTO search_documents(kind,key,sha,metadata,char_length) "
                                "VALUES(?,?,?,?,?)", (kind, key, sha, serialized, len(text)))
            document_id = cursor.lastrowid
            db.execute("INSERT INTO search_kinds VALUES(?,1) ON CONFLICT(kind) DO UPDATE "
                       "SET document_count=document_count+1", (kind,))
        else:
            document_id = old[0]
            _drop_postings(db, document_id, kind)
            db.execute("UPDATE search_documents SET sha=?,metadata=?,char_length=? "
                       "WHERE document_id=?", (sha, serialized, len(text), document_id))
        grams = _grams(text)
        db.executemany("INSERT INTO search_postings VALUES(?,?,?)",
                       ((gram, kind, document_id) for gram in grams))
        db.executemany("INSERT INTO search_grams VALUES(?,?,1) ON CONFLICT(gram,kind) "
                       "DO UPDATE SET document_count=document_count+1",
                       ((gram, kind) for gram in grams))
    return {"id": key, "kind": kind, "sha": sha,
            "status": "inserted" if old is None else "updated"}


def remove(db, kind, key):
    """Remove an index identity; shared source objects remain owned by core."""
    kind, key = _identity(kind, "kind"), _identity(key, "key")
    with _write(db):
        row = db.execute("SELECT document_id FROM search_documents WHERE kind=? AND key=?",
                         (kind, key)).fetchone()
        if row is None:
            return False
        _drop_postings(db, row[0], kind)
        db.execute("DELETE FROM search_documents WHERE document_id=?", (row[0],))
        db.execute("UPDATE search_kinds SET document_count=document_count-1 WHERE kind=?", (kind,))
        db.execute("DELETE FROM search_kinds WHERE kind=? AND document_count=0", (kind,))
    return True


def _query(db, query, limit, kinds):
    if not isinstance(query, str) or not query or len(query) > 1000:
        fail("invalid_input", "query must contain 1 to 1000 literal characters")
    if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
        fail("invalid_input", f"limit must be an integer from 1 to {MAX_RESULTS}")
    if kinds is not None:
        if not isinstance(kinds, (list, tuple)) or len(kinds) > 32:
            fail("invalid_input", "kinds must be an array of at most 32 names")
        kinds = sorted({_identity(kind, "kind") for kind in kinds})
    scope_rows = db.execute("SELECT kind,document_count FROM search_kinds ORDER BY kind").fetchall()
    scope_counts = {row[0]: row[1] for row in scope_rows if kinds is None or row[0] in kinds}
    result = {
        "query": query, "matches": [], "candidates": [], "truncated": False,
        "complete": True, "search_complete": True, "no_match_confirmed": True,
        "total": 0, "matched_count": 0, "omitted": 0, "truncated_reasons": [],
        "candidate_details_truncated": False,
        "scope": {"indexed_documents": sum(scope_counts.values()), "kinds": scope_counts,
                  "requested_kinds": kinds, "unindexed_sources": "not_searched"},
        "capabilities": {"method": "unicode_codepoint_unigram_bigram",
                         "matching": "literal_substring", "case_sensitive": True,
                         "normalization": "none", "alias_resolution": False,
                         "occurrences": "first_per_source", "order": "kind_then_document_id",
                         "candidate_limit": MAX_CANDIDATES,
                         "verification_char_limit": MAX_VERIFICATION_CHARS,
                         "oversized_source": "first_source_allowed_to_exceed_char_limit"},
        "metrics": {"query_grams": 0, "intersection_grams": 0, "seed_document_frequency": 0,
                    "seed_postings_read": 0, "posting_candidates_examined": 0,
                    "intersection_candidates": 0, "documents_verified": 0,
                    "unique_sources_read": 0, "source_chars_read": 0},
    }
    if not scope_counts:
        return result
    suffix = "" if kinds is None else " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
    kind_args = () if kinds is None else tuple(kinds)
    grams = sorted(set(query) if len(query) == 1 else
                   {query[i:i + 2] for i in range(len(query) - 1)})
    result["metrics"]["query_grams"] = len(grams)
    frequencies = []
    for gram in grams:
        frequency = db.execute("SELECT COALESCE(SUM(document_count),0) FROM search_grams "
                               "WHERE gram=?" + suffix, (gram,) + kind_args).fetchone()[0]
        if not frequency:
            return result
        frequencies.append((frequency, gram))
    selected = sorted(frequencies)[:MAX_INTERSECTION_GRAMS]
    seed_frequency, seed = selected[0]
    metrics = result["metrics"]
    metrics.update(intersection_grams=len(selected), seed_document_frequency=seed_frequency)
    # This LIMIT applies to the posting index before intersection or text reads.
    # Its ordering is the index key; no corpus-wide sort or LIKE is involved.
    seed_rows = db.execute("SELECT document_id FROM search_postings WHERE gram=?" + suffix +
                           " ORDER BY kind,document_id LIMIT ?",
                           (seed,) + kind_args + (MAX_CANDIDATES + 1,)).fetchall()
    metrics["seed_postings_read"] = len(seed_rows)
    seed_truncated = len(seed_rows) > MAX_CANDIDATES
    seed_ids = [row[0] for row in seed_rows[:MAX_CANDIDATES]]
    metrics["posting_candidates_examined"] = len(seed_ids)
    candidates = []
    for start in range(0, len(seed_ids), 128):
        batch = seed_ids[start:start + 128]
        sql = ("SELECT d.document_id,d.kind,d.key,d.sha,d.char_length FROM search_documents d "
               "WHERE d.document_id IN (" + ",".join("?" for _ in batch) + ")")
        args = list(batch)
        for _, gram in selected[1:]:
            sql += (" AND EXISTS(SELECT 1 FROM search_postings p WHERE p.gram=? "
                    "AND p.kind=d.kind AND p.document_id=d.document_id)")
            args.append(gram)
        by_id = {row[0]: row for row in db.execute(sql, args)}
        candidates.extend(by_id[document_id] for document_id in batch if document_id in by_id)
    metrics["intersection_candidates"] = len(candidates)
    sources = {}
    matches = []
    char_truncated = False
    for row in candidates:
        document_id, kind, key, sha, char_length = row
        if sha not in sources:
            if sources and metrics["source_chars_read"] + char_length > MAX_VERIFICATION_CHARS:
                char_truncated = True
                break
            source = db.execute("SELECT text FROM core_objects WHERE sha=?", (sha,)).fetchone()
            if source is None:
                fail("search_index_corrupt", "Indexed source object is missing", sha=sha)
            text = source[0]
            position = text.find(query)
            hit = None
            if position >= 0:
                end = position + len(query)
                begin = max(0, position - 64)
                hit = {"quote": text[position:end], "start": position, "end": end,
                       "line": text.count("\n", 0, position) + 1,
                       "snippet_start": begin, "snippet": text[begin:min(len(text), end + 64)]}
            sources[sha] = hit
            metrics["unique_sources_read"] += 1
            metrics["source_chars_read"] += len(text)
        metrics["documents_verified"] += 1
        descriptor = {"id": key, "key": key, "kind": kind, "document_id": document_id,
                      "sha": sha, "source_sha256": sha}
        hit = sources[sha]
        if len(result["candidates"]) < limit:
            result["candidates"].append({**descriptor, "exact_match": hit is not None})
        if hit is not None:
            metadata = db.execute("SELECT metadata FROM search_documents WHERE document_id=?",
                                  (document_id,)).fetchone()[0]
            matches.append({**descriptor, **hit, "metadata": json.loads(metadata)})
            if len(matches) > limit:
                break
    search_complete = not seed_truncated and metrics["documents_verified"] == len(candidates)
    reasons = []
    if seed_truncated:
        reasons.append("candidate_limit")
    if len(matches) > limit:
        reasons.append("result_limit")
    if char_truncated:
        reasons.append("verification_char_limit")
    result.update(matches=matches[:limit], matched_count=len(matches),
                  total=len(matches) if search_complete else None,
                  omitted=max(0, len(matches) - limit) if search_complete else None,
                  search_complete=search_complete, complete=search_complete and len(matches) <= limit,
                  truncated=bool(reasons), truncated_reasons=reasons,
                  no_match_confirmed=search_complete and not matches,
                  candidate_details_truncated=metrics["documents_verified"] > len(result["candidates"]))
    return result


def query(db, query, limit=30, kinds=None):
    """Search one consistent snapshot; an incomplete empty result proves nothing.

    total/omitted are null unless every indexed candidate in scope was checked.
    matches are exact source substrings; candidates are bounded diagnostic rows.
    Source offsets count Unicode codepoints in the original text, including LF.
    """
    owns_read = not db.in_transaction
    if owns_read:
        db.execute("BEGIN")
    try:
        return _query(db, query, limit, kinds)
    finally:
        if owns_read:
            db.rollback()
