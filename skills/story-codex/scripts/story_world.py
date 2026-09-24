"""Indexed, evidence-backed narrative state. No model calls or prose generation.

Story time uses integer ticks in an explicitly named clock; different clocks are
not comparable. Published observations are immutable. Author plans may be
revised or promoted to observations, but never count as observed knowledge,
balances, or previous ability use. Caller installs SCHEMA before using this API.
"""
from decimal import Decimal, InvalidOperation, localcontext
from itertools import groupby
import json
import re
import sqlite3

_core = None


def inject(core):
    global _core
    _core = core


def fail(code, message, **details):
    if _core is None:
        raise RuntimeError("story_world.inject(core) is required")
    _core.fail(code, message, **details)


SCHEMA = """
CREATE TABLE IF NOT EXISTS world_entities(id TEXT PRIMARY KEY, name TEXT NOT NULL,
 kind TEXT NOT NULL, description TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS world_entity_name ON world_entities(name);
CREATE TABLE IF NOT EXISTS world_aliases(alias TEXT NOT NULL, entity TEXT NOT NULL,
 scope TEXT NOT NULL, PRIMARY KEY(alias,entity,scope));
CREATE TABLE IF NOT EXISTS world_evidence(kind TEXT NOT NULL, record_id TEXT NOT NULL,
 mode TEXT NOT NULL, chapter INTEGER, sha TEXT, quote TEXT, note TEXT, valid INTEGER NOT NULL DEFAULT 1, retired INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(kind,record_id));
CREATE INDEX IF NOT EXISTS world_evidence_source ON world_evidence(chapter,sha);
CREATE INDEX IF NOT EXISTS world_evidence_mode ON world_evidence(kind,mode,chapter,record_id);
CREATE TABLE IF NOT EXISTS world_retirements(kind TEXT NOT NULL, record_id TEXT NOT NULL,
 reason TEXT NOT NULL, chapter INTEGER NOT NULL, sha TEXT NOT NULL, quote TEXT NOT NULL,
 PRIMARY KEY(kind,record_id));
CREATE TABLE IF NOT EXISTS world_links(kind TEXT NOT NULL, record_id TEXT NOT NULL,
 entity TEXT NOT NULL, PRIMARY KEY(kind,record_id,entity));
CREATE INDEX IF NOT EXISTS world_links_entity ON world_links(entity,kind,record_id);
CREATE TABLE IF NOT EXISTS world_volumes(id TEXT PRIMARY KEY, title TEXT NOT NULL,
 goal TEXT NOT NULL, entry_condition TEXT NOT NULL, exit_condition TEXT NOT NULL, cost TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS world_arcs(id TEXT PRIMARY KEY, volume TEXT NOT NULL,
 title TEXT NOT NULL, goal TEXT NOT NULL, entry_condition TEXT NOT NULL,
 exit_condition TEXT NOT NULL, cost TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS world_arc_volume ON world_arcs(volume,id);
CREATE TABLE IF NOT EXISTS world_lines(id TEXT PRIMARY KEY, line TEXT NOT NULL,
 clock TEXT NOT NULL, at INTEGER, place TEXT NOT NULL, summary TEXT NOT NULL, unfinished TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS world_line_time ON world_lines(line,clock,at DESC);
CREATE TABLE IF NOT EXISTS world_facts(id TEXT PRIMARY KEY, subject TEXT NOT NULL,
 predicate TEXT NOT NULL, value TEXT NOT NULL, clock TEXT NOT NULL, start INTEGER,
 end INTEGER, hard INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS world_fact_subject ON world_facts(subject,clock,predicate,start DESC);
CREATE TABLE IF NOT EXISTS world_knowledge(id TEXT PRIMARY KEY, actor TEXT NOT NULL,
 fact TEXT NOT NULL, state TEXT NOT NULL, clock TEXT NOT NULL, at INTEGER, channel TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS world_knowledge_actor ON world_knowledge(actor,clock,fact,at DESC);
CREATE INDEX IF NOT EXISTS world_knowledge_fact ON world_knowledge(fact,id);
CREATE TABLE IF NOT EXISTS world_hooks(id TEXT PRIMARY KEY, hook TEXT NOT NULL,
 state TEXT NOT NULL, description TEXT NOT NULL, clock TEXT NOT NULL, at INTEGER,
 hard_deadline INTEGER, window_start INTEGER, window_end INTEGER, trigger_line TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS world_hook_history ON world_hooks(hook,clock,at DESC);
CREATE INDEX IF NOT EXISTS world_hook_deadline ON world_hooks(clock,hard_deadline,hook);
CREATE INDEX IF NOT EXISTS world_hook_line ON world_hooks(trigger_line,hook);
CREATE INDEX IF NOT EXISTS world_hook_window ON world_hooks(window_end,hook);
CREATE TABLE IF NOT EXISTS world_rules(id TEXT PRIMARY KEY, rule TEXT NOT NULL,
 version INTEGER NOT NULL, clock TEXT NOT NULL, start INTEGER, end INTEGER,
 cooldown INTEGER, description TEXT NOT NULL, hard INTEGER NOT NULL, line TEXT,
 UNIQUE(rule,version));
CREATE INDEX IF NOT EXISTS world_rule_time ON world_rules(rule,clock,start DESC,version DESC);
CREATE INDEX IF NOT EXISTS world_rule_line ON world_rules(line,clock,start);
CREATE TABLE IF NOT EXISTS world_rule_requires(rule_id TEXT NOT NULL, fact TEXT NOT NULL,
 PRIMARY KEY(rule_id,fact));
CREATE INDEX IF NOT EXISTS world_rule_fact ON world_rule_requires(fact,rule_id);
CREATE TABLE IF NOT EXISTS world_uses(id TEXT PRIMARY KEY, actor TEXT NOT NULL,
 rule TEXT NOT NULL, clock TEXT NOT NULL, at INTEGER, exception TEXT);
CREATE INDEX IF NOT EXISTS world_use_actor ON world_uses(actor,rule,clock,at DESC);
CREATE TABLE IF NOT EXISTS world_transfers(id TEXT PRIMARY KEY, resource TEXT NOT NULL,
 sender TEXT, receiver TEXT, amount TEXT, quantity_text TEXT NOT NULL,
 clock TEXT NOT NULL, at INTEGER, opening INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS world_transfer_sender ON world_transfers(sender,clock,at,resource);
CREATE INDEX IF NOT EXISTS world_transfer_receiver ON world_transfers(receiver,clock,at,resource);
CREATE TABLE IF NOT EXISTS world_arc_steps(id TEXT PRIMARY KEY, arc TEXT NOT NULL,
 actor TEXT NOT NULL, pattern TEXT NOT NULL, desire TEXT NOT NULL, strategy TEXT NOT NULL,
 choice TEXT NOT NULL, cost TEXT NOT NULL, relationship TEXT NOT NULL, result TEXT NOT NULL,
 irreversible INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS world_step_actor ON world_arc_steps(actor,pattern,arc);
"""

# Typed columns, not free-form data blobs. '?' denotes nullable; '=' a default.
FIELDS = {
    "entities": {"id": "id", "name": "text", "kind": "entity_kind", "description": "text"},
    "aliases": {"alias": "text", "entity": "id", "scope": "scope"},
    "volumes": {"id": "id", "title": "text", "goal": "text", "entry_condition": "text", "exit_condition": "text", "cost": "text"},
    "arcs": {"id": "id", "volume": "id", "title": "text", "goal": "text", "entry_condition": "text", "exit_condition": "text", "cost": "text"},
    "lines": {"id": "id", "line": "id", "clock": "clock", "at": "int?", "place": "id", "summary": "text", "unfinished": "text"},
    "facts": {"id": "id", "subject": "id", "predicate": "text", "value": "text", "clock": "clock", "start": "int?", "end": "int?", "hard": "bool"},
    "knowledge": {"id": "id", "actor": "id", "fact": "id", "state": "knowledge_state", "clock": "clock", "at": "int?", "channel": "text"},
    "hooks": {"id": "id", "hook": "id", "state": "hook_state", "description": "text", "clock": "clock", "at": "int?", "hard_deadline": "int?", "window_start": "positive?", "window_end": "positive?", "trigger_line": "id?", "reason": "text?"},
    "rules": {"id": "id", "rule": "id", "version": "positive", "clock": "clock", "start": "int?", "end": "int?", "cooldown": "nonnegative?", "description": "text", "hard": "bool", "line": "id?"},
    "uses": {"id": "id", "actor": "id", "rule": "id", "clock": "clock", "at": "int?", "exception": "text?"},
    "transfers": {"id": "id", "resource": "id", "sender": "id?", "receiver": "id?", "amount": "decimal?", "quantity_text": "text", "clock": "clock", "at": "int?", "opening": "bool"},
    "arc_steps": {"id": "id", "arc": "id", "actor": "id", "pattern": "text", "desire": "text", "strategy": "text", "choice": "text", "cost": "text", "relationship": "text", "result": "text", "irreversible": "bool"},
}
DEFAULTS = {"clock": "main", "scope": "*", "hard": False, "opening": False, "irreversible": False}
ENUMS = {"entity_kind": {"character", "place", "item", "resource", "organization"},
         "knowledge_state": {"knows", "believes", "suspects", "unknown"},
         "hook_state": {"seeded", "reinforced", "dormant", "fulfilled", "breached", "abandoned", "reopened"}}


def _value(value, typ, field):
    if typ.endswith("?"):
        if value is None:
            return None
        typ = typ[:-1]
    if typ in ("int", "positive", "nonnegative"):
        if type(value) is not int or abs(value) > 2**53 or (typ == "positive" and value < 1) or (typ == "nonnegative" and value < 0):
            fail("invalid_input", "Invalid integer story coordinate", field=field)
        return value
    if typ == "bool":
        if type(value) is not bool:
            fail("invalid_input", "Expected boolean", field=field)
        return int(value)
    if typ == "decimal":
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,24}(?:\.[0-9]{1,12})?", value):
            fail("invalid_input", "Exact amounts must be decimal strings; use null for unknown quantities", field=field)
        try:
            number = Decimal(value)
        except InvalidOperation:
            fail("invalid_input", "Invalid exact amount", field=field)
        if number < 0:
            fail("invalid_input", "Amount must be nonnegative", field=field)
        return format(number, "f")
    value = _core.text_field(value, field, 1600 if typ == "text" else 120)
    if typ in ("id", "clock") and not re.fullmatch(r"[\w.-]+", value):
        fail("invalid_input", "Use a stable identifier", field=field)
    if typ == "scope" and value != "*" and not re.fullmatch(r"[\w.-]+", value):
        fail("invalid_input", "Alias scope must be a line ID or *", field=field)
    if typ in ENUMS and value not in ENUMS[typ]:
        fail("invalid_input", "Invalid narrative state", field=field, allowed=sorted(ENUMS[typ]))
    return value


def _list_ids(value, field):
    if not isinstance(value, list) or len(value) > 100:
        fail("invalid_input", "Expected at most 100 identifiers", field=field)
    return sorted(set(_value(v, "id", field) for v in value))


def _evidence(book, raw):
    if not isinstance(raw, dict):
        fail("invalid_input", "Each narrative record needs evidence")
    mode = raw.get("kind")
    if mode == "author_plan":
        if set(raw) != {"kind", "note"}:
            fail("invalid_input", "Author plans require only kind and note")
        return {"mode": mode, "chapter": None, "sha": None, "quote": None,
                "note": _core.text_field(raw["note"], "evidence.note", 1600)}
    if mode != "chapter" or set(raw) != {"kind", "chapter", "sha256", "quote"}:
        fail("invalid_input", "Published evidence needs chapter, sha256 and exact quote")
    chapter = _value(raw["chapter"], "positive", "evidence.chapter")
    sha = raw["sha256"]
    if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        fail("invalid_input", "Evidence needs a SHA256 digest")
    quote = _core.text_field(raw["quote"], "evidence.quote", 1400)
    row = book.db.execute("SELECT sha,text FROM chapters WHERE chapter=?", (chapter,)).fetchone()
    if row is None or row["sha"] != sha or quote not in row["text"]:
        fail("world_evidence", "Evidence must match a committed chapter version and exact quote", chapter=chapter)
    return {"mode": mode, "chapter": chapter, "sha": sha, "quote": quote, "note": None}


def _exists(book, table, field, value):
    if value is not None and book.db.execute(f"SELECT 1 FROM world_{table} WHERE {field}=? LIMIT 1", (value,)).fetchone() is None:
        fail("world_reference", "Unknown narrative reference", kind=table, field=field, id=value)


def _normal(book, kind, raw):
    if not isinstance(raw, dict):
        fail("invalid_input", "Narrative records must be objects", kind=kind)
    extra = {"evidence", "entities"} if kind not in ("entities", "aliases") else set()
    if kind == "rules":
        extra.add("requires")
    unknown = sorted(set(raw) - set(FIELDS[kind]) - extra)
    if unknown:
        fail("invalid_input", "Unknown narrative fields", kind=kind, fields=unknown)
    row = {key: _value(raw.get(key, DEFAULTS.get(key)), typ, kind + "." + key) for key, typ in FIELDS[kind].items()}
    evidence = _evidence(book, raw.get("evidence")) if "evidence" in extra else None
    entities = _list_ids(raw.get("entities", []), kind + ".entities") if "entities" in extra else []
    requires = _list_ids(raw.get("requires", []), "rules.requires") if kind == "rules" else []
    for key in ("actor", "subject", "place", "sender", "receiver", "resource", "entity"):
        if key in row and row[key] is not None:
            _exists(book, "entities", "id", row[key])
            entities.append(row[key])
    for entity in entities:
        _exists(book, "entities", "id", entity)
    for key, table in (("volume", "volumes"), ("arc", "arcs"), ("fact", "facts")):
        if key in row:
            _exists(book, table, "id", row[key])
    if kind == "uses":
        _exists(book, "rules", "rule", row["rule"])
    for fact in requires:
        _exists(book, "facts", "id", fact)
    for start, end in (("start", "end"), ("window_start", "window_end")):
        if row.get(start) is not None and row.get(end) is not None and row[start] > row[end]:
            fail("invalid_input", "Narrative range is reversed", kind=kind, start=start, end=end)
    if kind == "transfers":
        if not row["opening"] and row["amount"] is not None and Decimal(row["amount"]) == 0:
            fail("invalid_input", "Ordinary transfers must be positive; only an opening balance may be zero", field="transfers.amount")
        if row["sender"] == row["receiver"]:
            fail("invalid_input", "Transfers need different endpoints and at least one named holder")
        if row["opening"] and (row["sender"] is not None or row["receiver"] is None):
            fail("invalid_input", "Opening balance must enter a named holder from outside")
    if kind == "hooks":
        if row["state"] in ("abandoned", "reopened", "breached") and not row["reason"]:
            fail("invalid_input", "Abandoning or reopening a promise needs a reason")
        if row["state"] in ("fulfilled", "breached") and evidence["mode"] != "chapter":
            fail("world_evidence", "A payoff or breach must be evidenced in published prose")
        if row["hard_deadline"] is not None and row["at"] is not None and row["hard_deadline"] < row["at"] and row["state"] == "seeded":
            fail("invalid_input", "A new promise cannot already have expired at its creation")
    return row, evidence, sorted(set(entities)), requires


def _stored(book, kind, rid):
    row = book.db.execute(f"SELECT * FROM world_{kind} WHERE id=?", (rid,)).fetchone()
    if row is None:
        return None
    ev = book.db.execute("SELECT mode,chapter,sha,quote,note FROM world_evidence WHERE kind=? AND record_id=?", (kind, rid)).fetchone()
    links = [r[0] for r in book.db.execute("SELECT entity FROM world_links WHERE kind=? AND record_id=? ORDER BY entity", (kind, rid))]
    req = [r[0] for r in book.db.execute("SELECT fact FROM world_rule_requires WHERE rule_id=? ORDER BY fact", (rid,))] if kind == "rules" else []
    return dict(row), dict(ev) if ev else None, links, req


def _hook_predecessor(book, row, evidence):
    """Find the predecessor of this event, not the hook's eventual end state.

    Comparable story time wins; publication order breaks equal-time ties or
    supplies a fallback when time is unknown. Existing row order is preserved
    during history repair, so rebinding a seed cannot see its later payoff.
    """
    existing = book.db.execute("SELECT rowid FROM world_hooks WHERE id=?", (row["id"],)).fetchone()
    boundary = existing[0] if existing else 2**63 - 1
    publication = "(e.chapter<? OR (e.chapter=? AND h.rowid<?))"
    publication_args = [evidence["chapter"], evidence["chapter"], boundary]
    if row["at"] is None:
        predicate = publication
        args = publication_args
        ordering = "e.chapter DESC,h.rowid DESC"
    else:
        predicate = "(h.at<? OR ((h.at=? OR h.at IS NULL) AND " + publication + "))"
        args = [row["at"], row["at"], *publication_args]
        ordering = "h.at DESC,e.chapter DESC,h.rowid DESC"
    previous = book.db.execute("SELECT h.id,h.state,h.hard_deadline,e.mode,e.chapter,e.sha FROM world_hooks h JOIN world_evidence e "
                           "ON e.kind='hooks' AND e.record_id=h.id WHERE h.hook=? AND h.clock=? AND h.id<>? "
                           "AND e.mode='chapter' AND e.retired=0 AND " + predicate + " ORDER BY " + ordering + " LIMIT 1",
                           (row["hook"], row["clock"], row["id"], *args)).fetchone()
    if previous:
        _verify_current(book, "hooks", {"id": previous["id"], "evidence": {key: previous[key] for key in ("mode", "chapter", "sha")}})
    return previous


def apply_in_transaction(book, payload, repair=False):
    """Apply validated state alongside a chapter; caller owns transaction/event."""
    if not book.db.in_transaction:
        fail("transaction_required", "Narrative changes need the caller's open transaction")
    if not isinstance(payload, dict) or not payload or set(payload) - (set(FIELDS) | {"retirements"}):
        fail("invalid_input", "world-save expects named arrays", allowed=list(FIELDS))
    if any(not isinstance(v, list) or len(v) > 500 for v in payload.values()):
        fail("invalid_input", "Each narrative batch is limited to 500 records per kind")
    changed = []
    for kind in FIELDS:
        seen = set()
        for raw in payload.get(kind, []):
            row, evidence, entities, requires = _normal(book, kind, raw)
            if kind != "aliases":
                if row["id"] in seen:
                    fail("duplicate_id", "Each world record may appear once per batch", kind=kind, id=row["id"])
                seen.add(row["id"])
            if kind == "aliases":
                cursor = book.db.execute("INSERT OR IGNORE INTO world_aliases(alias,entity,scope) VALUES (?,?,?)", tuple(row.values()))
                if cursor.rowcount:
                    changed.append({"kind": kind, "after": row})
                continue
            old = _stored(book, kind, row["id"])
            value = (row, evidence, entities, requires)
            if old == value:
                valid = book.db.execute("SELECT valid FROM world_evidence WHERE kind=? AND record_id=?", (kind, row["id"])).fetchone()
                if valid and not valid[0]:
                    book.db.execute("UPDATE world_evidence SET valid=1 WHERE kind=? AND record_id=?", (kind, row["id"]))
                    changed.append({"kind": kind, "id": row["id"], "revalidated": True, "evidence": evidence})
                continue
            if old and old[1] and old[1]["mode"] == "chapter":
                invalid = book.db.execute("SELECT valid FROM world_evidence WHERE kind=? AND record_id=?", (kind, row["id"])).fetchone()
                if not repair or invalid is None or invalid[0]:
                    fail("world_record_conflict", "Published observations are immutable outside an invalidated history repair", kind=kind, id=row["id"])
                if evidence["mode"] != "chapter":
                    fail("world_evidence", "A repaired observation needs reviewed chapter evidence")
            if kind == "hooks" and evidence["mode"] == "chapter":
                peers = [] if row["at"] is None else [r[0] for r in book.db.execute(
                    "SELECT h.id FROM world_hooks h JOIN world_evidence e ON e.kind='hooks' AND e.record_id=h.id "
                    "WHERE h.hook=? AND h.clock=? AND h.at=? AND e.chapter=? AND e.mode='chapter' "
                    "AND e.retired=0 AND h.id<>? ORDER BY h.id",
                    (row["hook"], row["clock"], row["at"], evidence["chapter"], row["id"]))]
                legacy_tie = (peers and repair and old and old[1]["mode"] == "chapter"
                              and old[1]["chapter"] == evidence["chapter"]
                              and all(old[0][field] == row[field] for field in ("hook", "clock", "at")))
                if peers and not legacy_tie:
                    fail("world_time_ambiguous", "Same-chapter hook transitions need distinct, evidenced story times; IDs do not establish event order",
                         hook=row["hook"], chapter=evidence["chapter"], at=row["at"], records=peers + [row["id"]])
                previous = None if legacy_tie else _hook_predecessor(book, row, evidence)
                if row["state"] != "seeded" and previous is None and not legacy_tie:
                    fail("world_lifecycle", "A hook transition needs its earlier published seed", hook=row["hook"])
                if previous and previous["state"] in ("fulfilled", "breached", "abandoned") and row["state"] not in ("reopened", "fulfilled", "breached", "abandoned"):
                    fail("world_lifecycle", "Reopening a closed promise must be explicit", hook=row["hook"])
                if previous and previous["hard_deadline"] is not None and row["hard_deadline"] != previous["hard_deadline"] and not row["reason"]:
                    fail("world_lifecycle", "Changing a hard promise deadline needs an evidenced explanation", hook=row["hook"])
            columns = list(row)
            try:
                book.db.execute(f"INSERT INTO world_{kind}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in columns if c != "id"), tuple(row.values()))
            except sqlite3.IntegrityError:
                fail("world_record_conflict", "Conflicting narrative identifier or rule version", kind=kind, id=row["id"])
            if evidence:
                book.db.execute("INSERT OR REPLACE INTO world_evidence(kind,record_id,mode,chapter,sha,quote,note) VALUES (?,?,?,?,?,?,?)", (kind, row["id"], *evidence.values()))
            book.db.execute("DELETE FROM world_links WHERE kind=? AND record_id=?", (kind, row["id"]))
            book.db.executemany("INSERT INTO world_links VALUES (?,?,?)", [(kind, row["id"], e) for e in entities])
            if kind == "rules":
                book.db.execute("DELETE FROM world_rule_requires WHERE rule_id=?", (row["id"],))
                book.db.executemany("INSERT INTO world_rule_requires VALUES (?,?)", [(row["id"], f) for f in requires])
            changed.append({"kind": kind, "id": row["id"], "before": old,
                            "after": {**row, "evidence": evidence, "entities": entities, "requires": requires}})
    for raw in payload.get("retirements", []):
        if not repair or not isinstance(raw, dict) or set(raw) != {"kind", "id", "reason", "evidence"}:
            fail("invalid_input", "Retirements are explicit reviewed-history repairs")
        kind = raw["kind"]
        if kind not in FIELDS or kind in ("entities", "aliases"):
            fail("invalid_input", "Only evidenced narrative records can be retired")
        rid = _value(raw["id"], "id", "retirement.id")
        current = book.db.execute("SELECT valid,retired FROM world_evidence WHERE kind=? AND record_id=?", (kind, rid)).fetchone()
        if current is None or current["valid"] or current["retired"]:
            fail("world_record_conflict", "Retirement requires an invalidated published record", kind=kind, id=rid)
        evidence = _evidence(book, raw["evidence"])
        if evidence["mode"] != "chapter":
            fail("world_evidence", "Retirement needs the reviewed replacement chapter")
        reason = _core.text_field(raw["reason"], "retirement.reason", 1600)
        book.db.execute("INSERT OR REPLACE INTO world_retirements VALUES (?,?,?,?,?,?)", (kind, rid, reason, evidence["chapter"], evidence["sha"], evidence["quote"]))
        book.db.execute("UPDATE world_evidence SET valid=1,retired=1 WHERE kind=? AND record_id=?", (kind, rid))
        changed.append({"kind": kind, "id": rid, "retired": True, "reason": reason, "evidence": evidence})
    for raw in payload.get("retirements", []):
        if raw["kind"] == "facts":
            users = [r[0] for r in book.db.execute("SELECT k.id FROM world_knowledge k JOIN world_evidence e ON e.kind='knowledge' AND e.record_id=k.id WHERE k.fact=? AND e.retired=0 UNION SELECT r.rule_id FROM world_rule_requires r JOIN world_evidence e ON e.kind='rules' AND e.record_id=r.rule_id WHERE r.fact=? AND e.retired=0", (raw["id"], raw["id"]))]
            if users:
                fail("world_reference", "Retired facts still have live cognition or rule dependents; repair or retire them in the same batch", fact=raw["id"], dependents=users)
    return changed


def save(book, payload, expected):
    """Atomic standalone author-plan/baseline update."""
    with book.transaction(expected):
        changed = apply_in_transaction(book, payload)
        revision = book.event("world_save", {"changes": changed}) if changed else book.meta("revision")
        return {"revision": revision, "changed": len(changed), "idempotent": not changed}


def resolve(book, references, line=None):
    """Resolve exact IDs first. Never guess among Chinese names or scoped aliases."""
    if not isinstance(references, list) or len(references) > 100:
        fail("invalid_input", "plan.entities must contain at most 100 references")
    result = []
    for reference in references:
        reference = _core.text_field(reference, "plan.entities", 120)
        if book.db.execute("SELECT 1 FROM world_entities WHERE id=?", (reference,)).fetchone():
            result.append(reference)
            continue
        candidates = sorted({r[0] for r in book.db.execute("SELECT id FROM world_entities WHERE name=? UNION SELECT entity FROM world_aliases WHERE alias=? AND scope IN ('*',?)", (reference, reference, line or "*"))})
        if len(candidates) != 1:
            fail("ambiguous_entity" if candidates else "world_reference", "Use the stable entity ID to resolve this name", reference=reference, candidates=candidates)
        result.extend(candidates)
    return sorted(set(result))


def _clock(plan):
    raw = plan.get("time")
    if raw is None:
        return "main", None, None
    if not isinstance(raw, dict) or set(raw) - {"clock", "start", "end"}:
        fail("invalid_input", "plan.time needs clock, start and end")
    clock = _value(raw.get("clock", "main"), "clock", "plan.time.clock")
    start = _value(raw.get("start"), "int?", "plan.time.start")
    end = _value(raw.get("end", start), "int?", "plan.time.end")
    if start is not None and end is not None and end < start:
        fail("invalid_input", "plan.time is reversed")
    return clock, start, end


def _rows(book, kind, ids, chapter):
    result = []
    for rid in sorted(set(ids)):
        value = _stored(book, kind, rid)
        if value is None:
            continue
        row, ev, entities, requires = value
        flags = book.db.execute("SELECT retired FROM world_evidence WHERE kind=? AND record_id=?", (kind, rid)).fetchone()
        if flags and flags[0]:
            continue
        if ev and ev["mode"] == "chapter" and ev["chapter"] >= chapter:
            continue
        result.append({**row, "evidence": ev, "entities": entities, **({"requires": requires} if kind == "rules" else {})})
    return result


def _verify_current(book, kind, record):
    ev = record.get("evidence")
    if ev and ev["mode"] == "chapter":
        row = book.db.execute("SELECT e.valid,e.retired,c.sha FROM world_evidence e LEFT JOIN chapters c ON c.chapter=e.chapter WHERE e.kind=? AND e.record_id=?", (kind, record["id"])).fetchone()
        if row is None or not row["valid"] or row["retired"] or row["sha"] != ev["sha"]:
            fail("stale_world_evidence", "This narrative record needs review against the current chapter version", kind=kind, id=record["id"], chapter=ev["chapter"])


def invalidate_chapters(book, chapters):
    """Called inside the history publisher's transaction; never starts one."""
    if not isinstance(chapters, (list, tuple, set)):
        fail("invalid_input", "Expected chapter identifiers")
    changed = 0
    for chapter in sorted(set(chapters)):
        _value(chapter, "positive", "chapter")
        changed += book.db.execute("UPDATE world_evidence SET valid=0 WHERE chapter=? AND valid=1 AND retired=0", (chapter,)).rowcount
    return {"invalidated": changed}


def resolve_dependency(book, kind, key):
    """Stable content digest, or None for missing/stale evidence."""
    kind = kind.removeprefix("world.").removeprefix("world_")
    if kind not in FIELDS or kind == "aliases":
        return None
    value = _stored(book, kind, key)
    if value is None:
        return None
    row, evidence, entities, requires = value
    if evidence and evidence["mode"] == "chapter":
        current = book.db.execute("SELECT e.valid,e.retired,c.sha FROM world_evidence e LEFT JOIN chapters c ON c.chapter=e.chapter WHERE e.kind=? AND e.record_id=?", (kind, key)).fetchone()
        if current is None or not current["valid"] or current["retired"] or current["sha"] != evidence["sha"]:
            return None
    import hashlib
    return hashlib.sha256(_core.dumps([row, evidence, entities, requires]).encode("utf-8")).hexdigest()


def _related(book, kind, entities):
    ids = set()
    for entity in entities:
        ids.update(r[0] for r in book.db.execute("SELECT record_id FROM world_links WHERE entity=? AND kind=?", (entity, kind)))
    return ids


def _eligible(row, clock, at):
    return row.get("clock", clock) == clock and (at is None or row.get("at", row.get("start")) is None or row.get("at", row.get("start")) <= at) and (at is None or row.get("end") is None or at < row["end"])


def _latest(rows, keys, preserve_ties=False, publication_order=True):
    """Keep untimed evidence unordered instead of treating NULL as ancient."""
    chosen, undated = {}, {}
    for row in rows:
        key = tuple(row[k] for k in keys) + (row["evidence"]["mode"],)
        coordinate = row.get("at", row.get("start"))
        if coordinate is None:
            undated.setdefault(key, []).append(row)
            continue
        rank = (coordinate, row.get("version", 0))
        if publication_order:
            rank += (row["evidence"]["chapter"] or 0,)
        if not preserve_ties:
            rank += (row["id"],)
        if key not in chosen or rank > chosen[key][0]:
            chosen[key] = (rank, [row])
        elif rank == chosen[key][0]:
            chosen[key][1].append(row)
    result = []
    for key in sorted(chosen.keys() | undated.keys()):
        dated = chosen.get(key, (None, []))[1]
        for row in dated + undated.get(key, []):
            result.append({**row, **({"time_uncertain": True} if key in undated else {}),
                           **({"order_uncertain": True} if preserve_ties and len(dated) > 1 else {})})
    return result


def _current_rows(book, kind, ids, keys, chapter, clock, at):
    """Fetch dated winners and ambiguous states without inventing event order."""
    ids = sorted(set(ids))
    selected = []
    time_field = "at" if "at" in FIELDS[kind] else "start"
    partition = ",".join("r." + key for key in keys) + ",e.mode"
    version = ",r.version DESC" if kind == "rules" else ""
    unordered_states = kind in ("facts", "knowledge", "lines")
    publication = "" if unordered_states else ",e.chapter DESC"
    end_sql = " AND (r.end IS NULL OR ? IS NULL OR r.end>?)" if "end" in FIELDS[kind] else ""
    for offset in range(0, len(ids), 300):
        chunk = ids[offset:offset + 300]
        sql = f"""SELECT id FROM (SELECT r.id,r.{time_field} AS coordinate,DENSE_RANK() OVER
          (PARTITION BY {partition} ORDER BY r.{time_field} DESC{version}{publication}) AS rank
          FROM world_{kind} r JOIN world_evidence e ON e.kind=? AND e.record_id=r.id
          WHERE r.id IN ({','.join('?' for _ in chunk)}) AND r.clock=?
          AND e.retired=0 AND (e.mode='author_plan' OR e.chapter<?)
          AND (? IS NULL OR r.{time_field} IS NULL OR r.{time_field}<=?){end_sql}) WHERE rank=1 OR coordinate IS NULL"""
        args = [kind, *chunk, clock, chapter, at, at] + ([at, at] if end_sql else [])
        selected.extend(r[0] for r in book.db.execute(sql, args))
    return _latest(_rows(book, kind, selected, chapter), keys,
                   preserve_ties=unordered_states or kind == "hooks",
                   publication_order=not unordered_states)


def _applicable_rule_ids(book, actor, line, rule=None):
    """Filter actor/line scope before choosing versions, including global rules."""
    sql = """SELECT r.id FROM world_rules r WHERE (r.line IS NULL OR r.line=?)
      AND (NOT EXISTS (SELECT 1 FROM world_links l WHERE l.kind='rules' AND l.record_id=r.id)
           OR EXISTS (SELECT 1 FROM world_links l WHERE l.kind='rules' AND l.record_id=r.id AND l.entity=?))"""
    args = [line, actor]
    if rule is not None:
        sql += " AND r.rule=?"
        args.append(rule)
    return [r[0] for r in book.db.execute(sql, args)]


def _observed_uses(book, actor, rule, clock, at, chapter):
    ids = [r[0] for r in book.db.execute(
        "SELECT u.id FROM world_uses u JOIN world_evidence e ON e.kind='uses' AND e.record_id=u.id "
        "WHERE u.actor=? AND u.rule=? AND u.clock=? AND e.mode='chapter' AND e.retired=0 AND e.chapter<?",
        (actor, rule, clock, chapter))]
    rows = _current_rows(book, "uses", ids, ("actor", "rule"), chapter, clock, at)
    for row in rows:
        _verify_current(book, "uses", row)
    return rows


def _observed_transfers(book, holder, clock, at, chapter):
    # Indexed endpoint lookup; only numeric transaction columns are read.
    sql = """SELECT t.*,e.chapter FROM world_transfers t JOIN world_evidence e
      ON e.kind='transfers' AND e.record_id=t.id
      WHERE t.id IN (SELECT id FROM world_transfers WHERE sender=? AND clock=?
       UNION SELECT id FROM world_transfers WHERE receiver=? AND clock=?)
      AND e.mode='chapter' AND e.retired=0 AND e.chapter<? AND (? IS NULL OR t.at<=? OR t.at IS NULL)
      ORDER BY t.at,t.opening DESC,e.chapter,t.id"""
    for row in book.db.execute(sql, (holder, clock, holder, clock, chapter, at, at)):
        _verify_current(book, "transfers", {"id": row["id"], "evidence": dict(book.db.execute("SELECT mode,chapter,sha FROM world_evidence WHERE kind='transfers' AND record_id=?", (row["id"],)).fetchone())})
        yield dict(row)


def _balance_effect(value, row, holder):
    amount = Decimal(row["amount"]) if row["amount"] is not None else None
    if row["at"] is None:
        # An undated transfer may follow the opening even when SQL lists it first.
        value["time_uncertain"] = True
    if row["opening"] and row["receiver"] == holder:
        if value["opening_seen"]:
            value["amount"] = None
        else:
            value["amount"] = amount if not value.get("time_uncertain") else None
        value["opening_seen"] = True
    elif amount is None or row["at"] is None or value["amount"] is None:
        value["amount"] = None
    else:
        value["amount"] = _add_amount(value["amount"], amount if row["receiver"] == holder else amount.copy_negate())


def _balance(book, holder, clock, at, chapter):
    state = {}
    for row in _observed_transfers(book, holder, clock, at, chapter):
        value = state.setdefault(row["resource"], {"amount": None, "basis": [], "opening_seen": False})
        value["basis"].append(row["id"])
        _balance_effect(value, row, holder)
    return [{"holder": holder, "resource": resource, "amount": format(value["amount"], "f") if value["amount"] is not None and at is not None else None,
             "basis": value["basis"][-8:], "event_count": len(value["basis"]), "known": value["amount"] is not None and at is not None}
            for resource, value in sorted(state.items())]


def _add_amount(left, right):
    # Input permits 24 integer and 12 fractional digits. Python's default
    # Decimal precision (28) would silently round otherwise valid quantities.
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        return left + right


def context(book, plan, chapter, budget=None):
    """Build the complete selected world packet; never trim selected hard facts."""
    _value(chapter, "positive", "chapter")
    clock, at, end = _clock(plan)
    entities = resolve(book, plan.get("entities", []), plan.get("line"))
    packet = {"entities": [dict(book.db.execute("SELECT * FROM world_entities WHERE id=?", (e,)).fetchone()) for e in entities],
              "clock": clock, "at": at, "planned": {}, "warnings": []}
    for kind, field in (("volumes", "volume"), ("arcs", "arc")):
        if plan.get(field):
            _exists(book, kind, "id", plan[field])
            rows = _rows(book, kind, [plan[field]], chapter)
            if not rows:
                fail("world_reference", "The selected structure is not available before this chapter", kind=kind, id=plan[field])
            packet[field] = rows[0]
    if packet.get("arc") and plan.get("volume") and packet["arc"]["volume"] != plan["volume"]:
        fail("world_hierarchy", "The selected arc does not belong to the selected volume")
    if packet.get("arc") and "volume" not in packet:
        rows = _rows(book, "volumes", [packet["arc"]["volume"]], chapter)
        if not rows:
            fail("world_reference", "The selected volume is not available before this chapter")
        packet["volume"] = rows[0]
    if plan.get("line"):
        ids = [r[0] for r in book.db.execute("SELECT id FROM world_lines WHERE line=? AND clock=? AND (? IS NULL OR at<=? OR at IS NULL)", (plan["line"], clock, at, at))]
        rows = _current_rows(book, "lines", ids, ("line",), chapter, clock, at)
        actual = [r for r in rows if r["evidence"]["mode"] == "chapter"]
        uncertain = any(r.get("time_uncertain") or r.get("order_uncertain") for r in actual)
        packet["line"] = actual[-1] if actual and not uncertain else None
        if uncertain:
            packet["line_candidates"] = actual
            for flag, code in (("time_uncertain", "world_time_unknown"), ("order_uncertain", "world_order_ambiguous")):
                affected = [r["id"] for r in actual if r.get(flag)]
                if affected:
                    packet["warnings"].append({"code": code, "kind": "lines", "records": affected,
                                              "message": "These checkpoints do not establish a unique current scene."})
            for record in actual:
                _verify_current(book, "lines", record)
        packet["planned"]["lines"] = [r for r in rows if r["evidence"]["mode"] == "author_plan"]
        if not actual:
            packet["warnings"].append({"code": "line_checkpoint_unknown", "line": plan["line"]})
    for kind, keys in (("facts", ("subject", "predicate")), ("knowledge", ("actor", "fact")), ("hooks", ("hook",)), ("rules", ("rule",))):
        ids = _related(book, kind, entities)
        if kind in ("facts", "knowledge"):
            # Entity links discover state slots. Later ownership or cognition
            # changes need not repeat every related item or place link.
            seeds, slots = sorted(ids), set()
            columns = ",".join("r." + key for key in keys)
            for offset in range(0, len(seeds), 300):
                chunk = seeds[offset:offset + 300]
                slots.update(tuple(r) for r in book.db.execute(
                    f"SELECT DISTINCT {columns} FROM world_{kind} r JOIN world_evidence e "
                    "ON e.kind=? AND e.record_id=r.id "
                    f"WHERE r.id IN ({','.join('?' for _ in chunk)}) AND r.clock=? "
                    "AND e.retired=0 AND (e.mode='author_plan' OR e.chapter<?)", (kind, *chunk, clock, chapter)))
            for first, second in sorted(slots):
                ids.update(r[0] for r in book.db.execute(
                    f"SELECT id FROM world_{kind} WHERE {keys[0]}=? AND clock=? AND {keys[1]}=?",
                    (first, clock, second)))
        if kind == "hooks":
            if plan.get("line"):
                ids.update(r[0] for r in book.db.execute("SELECT id FROM world_hooks WHERE trigger_line=?", (plan["line"],)))
            if at is not None:
                hook_ids = {r[0] for r in book.db.execute("SELECT hook FROM world_hooks WHERE clock=? AND hard_deadline<=?", (clock, at))}
                for hook in hook_ids:
                    ids.update(r[0] for r in book.db.execute("SELECT id FROM world_hooks WHERE hook=? AND clock=?", (hook, clock)))
            # The planting supplies the relationship; later lifecycle records
            # need not repeat every entity tag to retire or fulfill that seed.
            hook_ids = {book.db.execute("SELECT hook FROM world_hooks WHERE id=?", (rid,)).fetchone()[0] for rid in ids}
            for hook in hook_ids:
                ids.update(r[0] for r in book.db.execute("SELECT id FROM world_hooks WHERE hook=? AND clock=?", (hook, clock)))
        if kind == "rules":
            selected = {}
            for actor in entities or [None]:
                for record in _current_rows(book, kind, _applicable_rule_ids(book, actor, plan.get("line")), keys, chapter, clock, at):
                    old = selected.get(record["id"], {})
                    selected[record["id"]] = {**record, **({"time_uncertain": True} if old.get("time_uncertain") else {})}
            rows = [selected[rid] for rid in sorted(selected)]
        else:
            rows = _current_rows(book, kind, ids, keys, chapter, clock, at)
        packet[kind] = [r for r in rows if r["evidence"]["mode"] == "chapter"]
        packet["planned"][kind] = [r for r in rows if r["evidence"]["mode"] == "author_plan"]
        for flag, code in (("time_uncertain", "world_time_unknown"), ("order_uncertain", "world_order_ambiguous")):
            affected = [r["id"] for r in rows if r.get(flag)]
            if affected:
                packet["warnings"].append({"code": code, "kind": kind, "records": affected,
                                           "message": "These records do not establish a unique state; review their evidenced story times."})
    # Knowledge references the original proposition, even if the world changed.
    required_facts = {r["fact"] for r in packet["knowledge"]}
    required_facts.update(f for r in packet["rules"] + packet["planned"]["rules"] for f in r["requires"])
    packet["propositions"] = _rows(book, "facts", required_facts, chapter)
    packet["resources"] = [b for entity in entities for b in _balance(book, entity, clock, at, chapter)]
    packet["uses"] = []
    packet["planned"]["uses"] = []
    for entity in entities:
        for rule in {r["rule"] for r in packet["rules"] + packet["planned"]["rules"]}:
            packet["uses"].extend(_observed_uses(book, entity, rule, clock, at, chapter))
        ids = [r[0] for r in book.db.execute("SELECT u.id FROM world_uses u JOIN world_evidence e ON e.kind='uses' AND e.record_id=u.id WHERE actor=? AND clock=? AND e.mode='author_plan' AND e.retired=0 AND (? IS NULL OR at>=? OR at IS NULL) AND (? IS NULL OR at<=? OR at IS NULL)", (entity, clock, at, at, end, end))]
        packet["planned"]["uses"].extend(_rows(book, "uses", ids, chapter))
    untimed_uses = [r["id"] for r in packet["uses"] if r["at"] is None]
    if untimed_uses:
        packet["warnings"].append({"code": "world_time_unknown", "kind": "uses", "records": untimed_uses,
                                   "message": "Recorded uses with unknown times cannot be discarded when reviewing cooldown."})
    packet["planned"]["transfers"] = []
    transfer_ids = set()
    for entity in entities:
        transfer_ids.update(r[0] for r in book.db.execute("SELECT t.id FROM world_transfers t JOIN world_evidence e ON e.kind='transfers' AND e.record_id=t.id WHERE t.id IN (SELECT id FROM world_transfers WHERE sender=? AND clock=? UNION SELECT id FROM world_transfers WHERE receiver=? AND clock=?) AND e.mode='author_plan' AND e.retired=0 AND (? IS NULL OR t.at>=? OR t.at IS NULL) AND (? IS NULL OR t.at<=? OR t.at IS NULL)", (entity, clock, entity, clock, at, at, end, end)))
    packet["planned"]["transfers"] = _rows(book, "transfers", transfer_ids, chapter)
    packet["arc_steps"] = []
    for entity in entities:
        ids = [r[0] for r in book.db.execute("SELECT s.id FROM world_arc_steps s JOIN world_evidence e ON e.kind='arc_steps' AND e.record_id=s.id WHERE actor=? AND e.retired=0 AND (e.chapter<? OR e.mode='author_plan') ORDER BY COALESCE(e.chapter,?) DESC,s.id DESC LIMIT 6", (entity, chapter, chapter))]
        packet["arc_steps"].extend(_rows(book, "arc_steps", ids, chapter))
    if at is None and any(packet.get(k) for k in ("knowledge", "facts", "rules", "hooks", "resources", "line")):
        packet["warnings"].append({"code": "story_time_unknown", "message": "These records cannot establish the state at an unspecified story time."})
    for kind, field in (("volumes", "volume"), ("arcs", "arc"), ("lines", "line")):
        if packet.get(field):
            _verify_current(book, kind, packet[field])
    for kind in ("facts", "knowledge", "hooks", "rules", "propositions", "uses", "arc_steps"):
        for record in packet.get(kind, []):
            _verify_current(book, "facts" if kind == "propositions" else kind, record)
    if budget is not None:
        _value(budget, "positive", "world budget")
        used = len(_core.dumps(packet).encode("utf-8"))
        if used > budget:
            fail("budget_exceeded", "Selected narrative state cannot be silently truncated", minimum_bytes=used, budget_bytes=budget)
    return packet


def check(book, plan, chapter=None):
    chapter = chapter if chapter is not None else plan.get("chapter", book.meta("last_chapter") + 1)
    packet = context(book, plan, chapter)
    return _evaluate(book, plan, chapter, packet, chapter)


def check_transition(book, plan, chapter, payload):
    """Check this chapter's actions once, after insertion but before commit.

    The baseline excludes the current chapter's ledger. Provisional uses and
    transfers saved on an earlier planning turn are not mixed into this check.
    Current-chapter rules/propositions are considered at each action's own time.
    """
    actors = set(resolve(book, plan.get("entities", []), plan.get("line")))
    actions = {"uses": [], "transfers": []}
    for kind in FIELDS:
        for raw in payload.get(kind, []):
            if kind in ("entities", "aliases"):
                continue
            stored = _stored(book, kind, raw["id"])
            if stored is None:
                fail("world_reference", "Apply world changes before checking their transition", kind=kind, id=raw["id"])
            row, evidence, entities, requires = stored
            if evidence["mode"] != "chapter":
                continue
            if evidence["chapter"] != chapter:
                fail("world_evidence_scope", "Chapter world_changes must cite this chapter; save historical baselines separately", kind=kind, id=row["id"])
            record = {**row, "evidence": evidence, "entities": entities, **({"requires": requires} if kind == "rules" else {})}
            _verify_current(book, kind, record)
            if kind in actions:
                actions[kind].append(record)
                actors.update(row.get(k) for k in ("actor", "sender", "receiver") if row.get(k))
    scoped_plan = {**plan, "entities": sorted(actors)}
    packet = context(book, scoped_plan, chapter)
    packet["planned"]["uses"] = actions["uses"]
    packet["planned"]["transfers"] = actions["transfers"]
    return _evaluate(book, scoped_plan, chapter, packet, chapter + 1)


def _evaluate(book, plan, chapter, packet, rule_horizon):
    blockers, warnings = [], list(packet["warnings"])
    clock, at, end = _clock(plan)
    for warning in list(warnings):
        if warning["code"] == "stale_world_evidence":
            blockers.append(warning)
            warnings.remove(warning)
    for hook in packet["hooks"]:
        if hook.get("time_uncertain") or hook.get("order_uncertain"):
            continue
        if hook["state"] not in ("fulfilled", "breached", "abandoned"):
            if at is not None and hook["hard_deadline"] is not None and at > hook["hard_deadline"]:
                warnings.append({"code": "hard_promise_overdue", "hook": hook["hook"], "deadline": hook["hard_deadline"], "evidence": hook["evidence"], "message": "A character may break a promise; review the consequences and record breached rather than pretending fulfillment."})
            if hook["window_end"] is not None and chapter > hook["window_end"]:
                warnings.append({"code": "soft_schedule_due", "hook": hook["hook"], "window_end": hook["window_end"]})
    previous = {}
    for use in sorted(packet["planned"]["uses"], key=lambda r: (r["at"] if r["at"] is not None else -2**54, r["id"])):
        if use["clock"] != clock:
            warnings.append({"code": "story_clock_unmatched", "id": use["id"]})
            continue
        if use["at"] is None:
            warnings.append({"code": "ability_state_unknown", "id": use["id"]})
            continue
        rule_ids = _applicable_rule_ids(book, use["actor"], plan.get("line"), use["rule"])
        choices = _current_rows(book, "rules", rule_ids, ("rule",), rule_horizon, clock, use["at"])
        # A proposed weaker version cannot silently replace a published rule.
        choices.sort(key=lambda r: r["evidence"]["mode"] == "chapter", reverse=True)
        rule = choices[0] if choices else None
        if rule is None or rule["start"] is None or rule.get("time_uncertain"):
            warnings.append({"code": "ability_state_unknown", "id": use["id"]})
            continue
        _verify_current(book, "rules", rule)
        issues = blockers if rule["hard"] and not use["exception"] else warnings
        prior = previous.get((use["actor"], use["rule"]))
        observed_uses = _observed_uses(book, use["actor"], use["rule"], clock, use["at"], chapter)
        unknown_uses = [r["id"] for r in observed_uses if r["at"] is None]
        if unknown_uses and rule["cooldown"] is not None:
            warnings.append({"code": "cooldown_time_unknown", "id": use["id"], "records": unknown_uses})
        for observed in (r for r in observed_uses if r["at"] is not None):
            if prior is None or observed["at"] is not None and (prior["at"] is None or observed["at"] > prior["at"]):
                prior = observed
        if prior and rule["cooldown"] is not None:
            if prior["at"] is None:
                warnings.append({"code": "cooldown_time_unknown", "id": use["id"]})
            elif use["at"] - prior["at"] < rule["cooldown"]:
                issues.append({"code": "cooldown_unfinished", "id": use["id"], "previous": prior["id"], "remaining": rule["cooldown"] - (use["at"] - prior["at"])})
        for fact in rule["requires"]:
            fact_rows = _rows(book, "facts", [fact], rule_horizon)
            proposition = fact_rows[0] if fact_rows else None
            active = []
            if proposition and proposition["evidence"]["mode"] == "chapter":
                _verify_current(book, "facts", proposition)
                ids = [r[0] for r in book.db.execute("SELECT id FROM world_facts WHERE subject=? AND predicate=? AND clock=?", (proposition["subject"], proposition["predicate"], clock))]
                active = [r for r in _current_rows(book, "facts", ids, ("subject", "predicate"), rule_horizon, clock, use["at"]) if r["evidence"]["mode"] == "chapter" and not r.get("time_uncertain") and not r.get("order_uncertain")]
            if fact not in {r["id"] for r in active}:
                warnings.append({"code": "rule_prerequisite_unverified", "id": use["id"], "fact": fact})
        if use["exception"]:
            warnings.append({"code": "rule_exception_review", "id": use["id"], "reason": use["exception"]})
        previous[(use["actor"], use["rule"])] = use
    # Merge recorded and proposed actions by story tick. A shared tick does not
    # establish income-before-spending order, even across publication chapters.
    actions = packet["planned"]["transfers"]
    matching = [r for r in actions if r["clock"] == clock]
    holders = {r[key] for r in matching for key in ("sender", "receiver") if r[key] is not None}
    horizon = max((r["at"] for r in matching if r["at"] is not None), default=None)
    observed = {r["id"]: r for holder in holders for r in _observed_transfers(book, holder, clock, horizon, chapter)}
    ordered = [(r, True) for r in observed.values()] + [(r, False) for r in actions]
    ordered.sort(key=lambda item: (item[0]["at"] if item[0]["at"] is not None else -2**54,
                                   not item[0]["opening"], not item[1], item[0].get("chapter", chapter), item[0]["id"]))
    balances = {}
    for tick, batch in groupby(ordered, key=lambda item: item[0]["at"]):
        flows = {}
        for transfer, is_observed in batch:
            if transfer["clock"] != clock:
                warnings.append({"code": "story_clock_unmatched", "id": transfer["id"]})
                continue
            amount = Decimal(transfer["amount"]) if transfer["amount"] is not None else None
            if not is_observed and (tick is None or amount is None):
                warnings.append({"code": "quantity_or_time_unknown", "id": transfer["id"]})
            for holder, sign in ((transfer["sender"], -1), (transfer["receiver"], 1)):
                if holder is None:
                    continue
                key = (holder, transfer["resource"])
                if transfer["opening"]:
                    existed = key in balances
                    value = balances.setdefault(key, {"amount": None, "opening_seen": False})
                    _balance_effect(value, transfer, holder)
                    if not is_observed and tick is not None and amount is not None and existed:
                        warnings.append({"code": "opening_balance_already_exists", "id": transfer["id"], "holder": holder})
                else:
                    flows.setdefault(key, []).append((transfer, is_observed, sign, amount))
        for (holder, resource), entries in sorted(flows.items()):
            value = balances.setdefault((holder, resource), {"amount": None, "opening_seen": False})
            current = value["amount"]
            incoming, outgoing = Decimal(0), Decimal(0)
            checked = []
            for transfer, is_observed, sign, amount in entries:
                _balance_effect(value, transfer, holder)
                if amount is not None:
                    if sign == 1:
                        incoming = _add_amount(incoming, amount)
                    else:
                        outgoing = _add_amount(outgoing, amount)
                if not is_observed and tick is not None and amount is not None:
                    checked.append((transfer, sign))
            if current is None or value["amount"] is None:
                warnings.extend({"code": "resource_baseline_unknown", "id": transfer["id"], "holder": holder}
                                for transfer, _ in checked)
            # Unknown spending cannot cure a shortfall already proved by the
            # known minimum. Unknown income or baseline can, so keep those open.
            if current is None or any(sign == 1 and amount is None for _, _, sign, amount in entries):
                continue
            if not any(sign == -1 for _, sign in checked):
                continue
            spending = [transfer for transfer, _, sign, _ in entries if sign == -1]
            reference = {"id": spending[0]["id"]} if len(spending) == 1 else {"records": sorted(r["id"] for r in spending)}
            available = _add_amount(current, incoming)
            detail = {**reference, "holder": holder, "resource": resource, "at": tick,
                      "available": format(available, "f"), "required": format(outgoing, "f")}
            if any(sign == -1 and amount is None for _, _, sign, amount in entries):
                detail["required_is_minimum"] = True
            if _add_amount(available, outgoing.copy_negate()) < 0:
                blockers.append({"code": "resource_overdraft", **detail})
            elif value["amount"] is not None and _add_amount(current, outgoing.copy_negate()) < 0:
                warnings.append({"code": "resource_order_ambiguous", **detail,
                                 "records": sorted(transfer["id"] for transfer, _, _, _ in entries),
                                 "message": "Spending needs income recorded at the same tick; establish their order before treating funds as available."})
    patterns = {}
    for step in packet["arc_steps"]:
        patterns.setdefault((step["actor"], step["pattern"]), []).append(step)
    for (actor, pattern), steps in patterns.items():
        if len(steps) >= 3:
            warnings.append({"code": "repeated_arc_pattern", "actor": actor, "pattern": pattern,
                             "examples": [{"id": s["id"], "choice": s["choice"], "cost": s["cost"], "result": s["result"], "irreversible": bool(s["irreversible"]), "evidence": s["evidence"]} for s in steps[:3]],
                             "message": "Compare choices and consequences; repetition can be intentional, and is not a quality verdict."})
    return {"blockers": blockers, "warnings": warnings, "ok": not blockers,
            "scope": {"entities": [e["id"] for e in packet["entities"]], "clock": clock, "start": at, "end": end},
            "limits": "Checks apply only to recorded evidence and explicit numeric rules; missing evidence and literary quality need review."}


def template(kind=None):
    evidence = {"kind": "author_plan", "note": "可修改的字段示例；须结合本书重新设计，提交正文证据后才能作为已发生事实。"}
    structure = {"title": "雨夜寻人", "goal": "在天黑前与失散同伴会合", "entry_condition": "同伴在岔路走散", "exit_condition": "两人在旧桥重逢", "cost": "返程被迫推迟", "evidence": evidence}
    values = {"entities": [{"id": "lin", "name": "林岚", "kind": "character", "description": "与同伴走散的行路人"},
                         {"id": "bridge", "name": "旧桥", "kind": "place", "description": "两条山路交汇的地方"},
                         {"id": "scarf", "name": "红围巾", "kind": "item", "description": "可能帮助辨认同伴行踪的随身物"},
                         {"id": "water", "name": "饮水", "kind": "resource", "description": "按升记录的随身饮水"}],
            "aliases": [{"alias": "阿岚", "entity": "lin", "scope": "bridge-line"}],
            "volumes": [{"id": "v1", **structure, "title": "第一卷 雨夜寻人"}], "arcs": [{"id": "a1", "volume": "v1", **structure}],
            "lines": [{"id": "bridge-entry", "line": "bridge-line", "clock": "main", "at": 10, "place": "bridge", "summary": "林岚沿旧桥寻找同伴", "unfinished": "桥对岸的口哨尚未辨明", "entities": ["lin"], "evidence": evidence}],
            "facts": [{"id": "f-scarf", "subject": "scarf", "predicate": "持有人", "value": "林岚", "entities": ["lin"], "clock": "main", "start": 10, "end": None, "hard": True, "evidence": evidence}],
            "knowledge": [{"id": "k-scarf", "actor": "lin", "fact": "f-scarf", "state": "unknown", "clock": "main", "at": 0, "channel": "开篇尚未找到红围巾", "evidence": evidence}],
            "hooks": [{"id": "h-seed", "hook": "meet-at-bridge", "state": "seeded", "description": "两人约定天黑前在旧桥会合", "clock": "main", "at": 10, "hard_deadline": 40, "window_start": 2, "window_end": 5, "trigger_line": "bridge-line", "entities": ["lin"], "evidence": evidence}],
            "rules": [{"id": "r1", "rule": "whistle-signal", "version": 1, "clock": "main", "start": 0, "end": None, "cooldown": None, "description": "彼此以三短一长的口哨确认方位", "hard": True, "line": "bridge-line", "requires": [], "entities": ["lin"], "evidence": evidence}],
            "uses": [{"id": "u1", "actor": "lin", "rule": "whistle-signal", "clock": "main", "at": 20, "exception": None, "evidence": evidence}],
            "transfers": [{"id": "t-opening", "resource": "water", "sender": None, "receiver": "lin", "amount": "2", "quantity_text": "随身饮水两升", "clock": "main", "at": 0, "opening": True, "evidence": evidence}],
            "arc_steps": [{"id": "s1", "arc": "a1", "actor": "lin", "pattern": "先寻找同伴再考虑返程", "desire": "确认同伴安全", "strategy": "循约定的口哨声靠近旧桥", "choice": "放弃返程车继续寻人", "cost": "入夜仍留在山中", "relationship": "与同伴的信任加深", "result": "听见桥对岸传来回应", "irreversible": False, "evidence": evidence}]}
    if kind == "all":
        return values
    if kind is None:
        return {key: values[key] for key in ("entities", "aliases", "facts")}
    if kind not in values:
        fail("invalid_input", "Unknown world template", allowed=list(values))
    return {kind: values[kind]}
