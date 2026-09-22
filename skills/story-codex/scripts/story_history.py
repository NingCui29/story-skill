"""Immutable chapter evidence and reviewed, atomic historical revision branches.

The dependency graph finds mechanical impact; it never claims to infer literary
correctness. Incomplete declarations expand the mandatory semantic review scope.
Snapshots contain content-addressed pointers, not recursively summarized prose.
"""
from collections import deque
import hashlib
import json
import re
import uuid


SCHEMA = """
CREATE TABLE history_versions(id TEXT PRIMARY KEY, chapter INTEGER NOT NULL,
 sha TEXT NOT NULL REFERENCES core_objects(sha), summary TEXT NOT NULL, receipt TEXT NOT NULL,
 plan TEXT, complete INTEGER NOT NULL, revision INTEGER NOT NULL, publication_revision INTEGER NOT NULL);
CREATE INDEX history_versions_chapter ON history_versions(chapter,revision);
CREATE TABLE history_heads(chapter INTEGER PRIMARY KEY, version TEXT NOT NULL REFERENCES history_versions(id));
CREATE TABLE history_edges(version TEXT NOT NULL REFERENCES history_versions(id),
 kind TEXT NOT NULL, ref TEXT NOT NULL, sha TEXT NOT NULL, PRIMARY KEY(version,kind,ref));
CREATE INDEX history_edges_reverse ON history_edges(kind,ref,version);
CREATE TABLE history_snapshots(id TEXT PRIMARY KEY, chapter INTEGER NOT NULL, revision INTEGER NOT NULL,
 label TEXT NOT NULL, manifest_sha TEXT NOT NULL REFERENCES core_objects(sha));
CREATE INDEX history_snapshots_chapter ON history_snapshots(chapter,revision);
CREATE TABLE history_branches(id TEXT PRIMARY KEY, target INTEGER NOT NULL, status TEXT NOT NULL,
 revision INTEGER NOT NULL, data TEXT NOT NULL, receipt TEXT);
CREATE TABLE history_cache(key TEXT PRIMARY KEY, chapter INTEGER NOT NULL, kind TEXT NOT NULL,
 body_sha TEXT NOT NULL, dependency_sha TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX history_cache_chapter ON history_cache(chapter);
CREATE TABLE history_invalidations(branch TEXT NOT NULL, chapter INTEGER NOT NULL,
 old_version TEXT NOT NULL, reason TEXT NOT NULL, PRIMARY KEY(branch,chapter));
CREATE TRIGGER history_versions_no_update BEFORE UPDATE ON history_versions BEGIN
 SELECT RAISE(ABORT,'history versions are immutable');
END;
CREATE TRIGGER history_versions_no_delete BEFORE DELETE ON history_versions BEGIN
 SELECT RAISE(ABORT,'history versions are immutable');
END;
CREATE TRIGGER history_edges_no_update BEFORE UPDATE ON history_edges BEGIN
 SELECT RAISE(ABORT,'history dependencies are immutable');
END;
CREATE TRIGGER history_edges_no_delete BEFORE DELETE ON history_edges BEGIN
 SELECT RAISE(ABORT,'history dependencies are immutable');
END;
"""

COMMANDS = {"history-deps", "history-dependencies", "history-snapshot", "history-start", "history-inspect", "history-saved",
            "history-update", "history-refresh", "history-publish", "history-state", "cache-get", "cache-put"}
DEFAULT_BUDGET = 64000
INSPECTION_COMMANDS = {"history-start", "history-inspect", "history-update", "history-refresh"}


def inject(core):
    global api
    api = core


def _hash(value):
    return api.digest(api.dumps(value))


def _sha(value, name="sha256"):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        api.fail("invalid_input", name + " must be a SHA-256 hex digest")
    return value


def _head(book, chapter):
    row = book.db.execute("SELECT v.* FROM history_heads h JOIN history_versions v ON v.id=h.version WHERE h.chapter=?",
                          (chapter,)).fetchone()
    if not row:
        api.fail("history_missing", "No recorded chapter version", chapter=chapter)
    return row


def _body(book, sha):
    row = book.db.execute("SELECT text FROM core_objects WHERE sha=?", (sha,)).fetchone()
    if not row:
        api.fail("state_corrupt", "Missing immutable body", sha256=sha)
    return row[0]


def _edges(book, version):
    return [dict(r) for r in book.db.execute(
        "SELECT kind,ref,sha FROM history_edges WHERE version=? ORDER BY kind,ref", (version,))]


def _resolve(book, kind, ref):
    if kind == "plan":
        row = book.db.execute("SELECT data FROM plans WHERE chapter=?", (int(ref),)).fetchone()
        return _hash(json.loads(row[0])) if row else None
    if kind == "chapter":
        row = book.db.execute("SELECT sha FROM chapters WHERE chapter=?", (int(ref),)).fetchone()
        return row[0] if row else None
    if kind == "card":
        row = book.db.execute("SELECT data FROM cards WHERE id=?", (ref,)).fetchone()
        return _hash(json.loads(row[0])) if row else None
    resolver = getattr(api, "history_resolve_dependency", None)
    if resolver:
        return resolver(book, kind, ref)
    world = getattr(api, "world", None)
    resolver = getattr(world, "resolve_dependency", None) if world else None
    if resolver:
        return resolver(book, {"entity": "entities", "rule": "rules"}.get(kind, kind), ref)
    api.fail("dependency_unavailable", "Entity/rule dependency resolver is not installed", kind=kind, ref=ref)


def _dependencies(book, values, candidate_shas=None, overrides=None):
    if not isinstance(values, list) or len(values) > 2000:
        api.fail("invalid_input", "dependencies must be an array of at most 2000 evidence references")
    result, seen = [], set()
    for value in values:
        api.object_value(value, "dependency")
        kind = value.get("kind")
        world_kinds = getattr(getattr(api, "world", None), "FIELDS", {})
        if kind not in ("chapter", "card", "entity", "rule") and not (isinstance(kind, str) and kind.startswith("world.") and kind[6:] in world_kinds and kind != "world.aliases"):
            api.fail("invalid_input", "dependency.kind must be chapter/card/entity/rule or a typed world record")
        ref = str(api.integer(value.get("ref"), "dependency.ref", 1)) if kind == "chapter" and type(value.get("ref")) is int else api.text_field(value.get("ref"), "dependency.ref", 160)
        if kind == "chapter" and (not ref.isdigit() or int(ref) < 1):
            api.fail("invalid_input", "chapter reference must be a positive number")
        if kind == "chapter":
            ref = str(int(ref))
        key = (kind, ref)
        if key in seen:
            api.fail("duplicate_id", "Duplicate dependency", kind=kind, ref=ref)
        seen.add(key)
        sha = _sha(value.get("sha"), "dependency.sha")
        actual = (candidate_shas or {}).get(ref) if kind == "chapter" else None
        if (kind, ref) in (overrides or {}):
            actual = overrides[(kind, ref)]
        elif actual is None:
            actual = _resolve(book, kind, ref)
        if actual != sha:
            api.fail("stale_dependency", "Evidence changed or is missing", kind=kind, ref=ref, expected=sha, actual=actual)
        result.append({"kind": kind, "ref": ref, "sha": sha})
    return sorted(result, key=lambda d: (d["kind"], d["ref"]))


def _version(book, chapter, text, summary, receipt, plan, dependencies, complete, publication_revision=None):
    sha = book.intern_body(text)
    vid = uuid.uuid4().hex
    book.db.execute("INSERT INTO history_versions VALUES (?,?,?,?,?,?,?,?,?)",
                    (vid, chapter, sha, summary, api.dumps(receipt), api.dumps(plan) if plan else None,
                     int(complete), book.meta("revision"), book.meta("revision") if publication_revision is None else publication_revision))
    book.db.executemany("INSERT INTO history_edges VALUES (?,?,?,?)",
                        [(vid, d["kind"], d["ref"], d["sha"]) for d in dependencies])
    book.db.execute("INSERT INTO history_heads VALUES (?,?) ON CONFLICT(chapter) DO UPDATE SET version=excluded.version",
                    (chapter, vid))
    return vid


def _required_dependencies(plan, dependencies, complete):
    if complete:
        required = set((plan or {}).get("requires", []))
        missing = sorted(required - {d["ref"] for d in dependencies if d["kind"] == "card"})
        if missing:
            api.fail("missing_required_dependencies", "A complete dependency review must include explicitly required cards", ids=missing)


def validate_commit_dependencies(book, raw, chapter):
    """Validate against pre-commit evidence; returns fields to merge into receipt."""
    if "dependencies" not in raw and "dependency_review" not in raw:
        return {}
    if "dependencies" not in raw or "dependency_review" not in raw:
        api.fail("invalid_input", "dependencies and dependency_review must be provided together")
    review = api.object_value(raw["dependency_review"], "dependency_review")
    complete = review.get("complete")
    if type(complete) is not bool:
        api.fail("invalid_input", "dependency_review.complete must be boolean")
    note = api.text_field(review.get("note"), "dependency_review.note", 2000)
    overrides = {}
    existing = book.db.execute("SELECT receipt FROM chapters WHERE chapter=?", (chapter,)).fetchone()
    if existing and chapter == book.meta("last_chapter"):
        previous = json.loads(existing[0])
        overrides = {("card", cid): _hash(value) if value is not None else None for cid, value in previous.get("before", {}).items()}
    dependencies = _dependencies(book, raw["dependencies"], overrides=overrides)
    if any(d["kind"] == "chapter" and d["ref"] == str(chapter) for d in dependencies):
        api.fail("invalid_input", "A new chapter cannot cite itself as prior evidence")
    _required_dependencies(book.get_plan(chapter), dependencies, complete)
    return {"dependencies": dependencies, "dependency_review": {"complete": complete, "note": note}}


def on_commit(book, chapter, plan, receipt, text, sha, publication_revision=None):
    """Called after the chapter event, inside its transaction. No export or commit."""
    if api.digest(text) != sha:
        api.fail("state_corrupt", "Chapter SHA does not match its body")
    explicit = receipt.get("dependency_review")
    if explicit is not None:
        _version(book, chapter, text, book.db.execute("SELECT summary FROM chapters WHERE chapter=?", (chapter,)).fetchone()[0],
                 receipt, plan, receipt["dependencies"], explicit["complete"], publication_revision)
        book.db.execute("DELETE FROM history_cache WHERE chapter=?", (chapter,))
        if chapter % 100 == 0 and chapter == book.meta("last_chapter"):
            _snapshot(book, "automatic:" + str(chapter))
        return
    dependencies = {}
    for cid in (plan or {}).get("requires", []):
        # A chapter consumes the pre-change card, not the state it just produced.
        before = receipt.get("before", {})
        # During migration the current card may contain events from much later
        # chapters. Only recorded preimages are known; omitted evidence remains
        # explicitly incomplete instead of inventing a historical dependency.
        card = before.get(cid) if cid in before else (book.cards([cid]).get(cid) if publication_revision is None else None)
        if card is not None:
            dependencies[("card", cid)] = {"kind": "card", "ref": cid, "sha": _hash(card)}
            source = re.match(r"chapter:(\d+)\b", card.get("source", ""))
            if source and int(source[1]) != chapter:
                ref = str(int(source[1]))
                prior = _resolve(book, "chapter", ref)
                if prior:
                    dependencies[("chapter", ref)] = {"kind": "chapter", "ref": ref, "sha": prior}
    previous = book.db.execute("SELECT chapter,sha FROM chapters WHERE chapter<? ORDER BY chapter DESC LIMIT 1", (chapter,)).fetchone()
    if previous and (publication_revision is None or _head(book, previous[0])["publication_revision"] < publication_revision):
        dependencies[("chapter", str(previous[0]))] = {"kind": "chapter", "ref": str(previous[0]), "sha": previous[1]}
    _version(book, chapter, text, book.db.execute("SELECT summary FROM chapters WHERE chapter=?", (chapter,)).fetchone()[0],
             receipt, plan, list(dependencies.values()), False, publication_revision)
    book.db.execute("DELETE FROM history_cache WHERE chapter=?", (chapter,))
    if chapter % 100 == 0 and chapter == book.meta("last_chapter"):
        _snapshot(book, "automatic:" + str(chapter))


def _ensure_history(book):
    """Lazily seed existing books, keeping imported status and unverified semantics."""
    missing = book.db.execute("SELECT c.chapter FROM chapters c LEFT JOIN history_heads h ON h.chapter=c.chapter WHERE h.chapter IS NULL ORDER BY c.chapter")
    for item in missing:
        row = book.db.execute("SELECT * FROM chapters WHERE chapter=?", (item[0],)).fetchone()
        plan = book.db.execute("SELECT data FROM plans WHERE chapter=?", (row["chapter"],)).fetchone()
        receipt = json.loads(row["receipt"])
        if "input" in receipt and type(receipt["input"].get("base_revision")) is int:
            published = receipt["input"]["base_revision"] + 1
        elif row["imported"]:
            event = book.db.execute("SELECT revision FROM events WHERE kind='adopt' ORDER BY revision DESC LIMIT 1").fetchone()
            published = event[0] if event else 0
        else:
            api.fail("history_unavailable", "Existing chapter has no verifiable publication revision", chapter=row["chapter"])
        on_commit(book, row["chapter"], json.loads(plan[0]) if plan else None,
                  receipt, row["text"], row["sha"], published)


def read_dependencies(book, chapter, budget=DEFAULT_BUDGET):
    """Read the published declaration without resolving or changing its evidence."""
    chapter = api.integer(chapter, "chapter", 1)
    with book.read_snapshot():
        current = book.db.execute("SELECT sha FROM chapter_state WHERE chapter=?", (chapter,)).fetchone()
        if not current:
            api.fail("chapter_missing", "No published chapter to inspect", chapter=chapter)
        head = _head(book, chapter)
        if head["sha"] != current["sha"]:
            api.fail("history_unavailable", "Published chapter and recorded history head differ", chapter=chapter)
        dependencies = _edges(book, head["id"])
        receipt = json.loads(head["receipt"])
        note = (receipt.get("dependency_review") or {}).get("note")
        event = book.db.execute(
            "SELECT data FROM events WHERE revision=? AND kind='history_dependencies' ORDER BY seq DESC LIMIT 1",
            (head["revision"],)).fetchone()
        if event:
            declaration = json.loads(event["data"])
            if declaration.get("chapter") == chapter:
                note = declaration.get("note")
        payload = {"chapter": chapter, "chapter_sha": head["sha"], "dependencies": dependencies,
                   "complete": bool(head["complete"]), "note": note if note is not None else ""}
        return api.bounded_packet({"book_id": book.meta("id"), "chapter": chapter,
            "revision": book.meta("revision"), "version": head["id"], "chapter_sha": head["sha"],
            "dependencies": dependencies, "complete": bool(head["complete"]), "note": note,
            "payload": payload,
            "scope": "Published declaration with its original evidence hashes, not current dependency candidates or branch drafts. "
                     "A missing note is null; fill the payload note after review before saving."}, budget)


def save_dependencies(book, payload, expected):
    api.object_value(payload, "dependencies payload")
    chapter = api.integer(payload.get("chapter"), "chapter", 1)
    complete = payload.get("complete")
    if type(complete) is not bool:
        api.fail("invalid_input", "complete must explicitly declare whether semantic dependencies were fully reviewed")
    note = api.text_field(payload.get("note"), "dependency review note", 2000)
    with book.transaction(expected):
        _ensure_history(book)
        head = _head(book, chapter)
        if _sha(payload.get("chapter_sha"), "chapter_sha") != head["sha"]:
            api.fail("stale_dependency", "Chapter changed; recheck dependencies", chapter=chapter)
        deps = _dependencies(book, payload.get("dependencies"))
        if any(d["kind"] == "chapter" and d["ref"] == str(chapter) for d in deps):
            api.fail("invalid_input", "A chapter cannot depend on itself")
        _required_dependencies(json.loads(head["plan"]) if head["plan"] else None, deps, complete)
        book.event("history_dependencies", {"chapter": chapter, "note": note, "dependencies": deps, "complete": complete})
        vid = _version(book, chapter, _body(book, head["sha"]), head["summary"], json.loads(head["receipt"]),
                       json.loads(head["plan"]) if head["plan"] else None, deps, complete, head["publication_revision"])
        revision = book.meta("revision")
    return {"chapter": chapter, "version": vid, "revision": revision, "complete": complete}


def _snapshot(book, label):
    # Full pointer manifests are infrequent; bodies/card values are deduplicated.
    heads = {str(r[0]): r[1] for r in book.db.execute("SELECT chapter,version FROM history_heads ORDER BY chapter")}
    cards = {}
    for row in book.db.execute("SELECT id,data FROM cards ORDER BY id"):
        cards[row[0]] = book.intern_body(api.dumps(json.loads(row[1])))
    manifest_sha = book.intern_body(api.dumps({"heads": heads, "cards": cards}))
    sid, chapter, revision = uuid.uuid4().hex, book.meta("last_chapter"), book.meta("revision")
    book.db.execute("INSERT INTO history_snapshots VALUES (?,?,?,?,?)", (sid, chapter, revision, label, manifest_sha))
    return {"snapshot": sid, "chapter": chapter, "revision": revision, "manifest_sha256": manifest_sha}


def snapshot(book, label, expected):
    label = api.text_field(label, "snapshot label", 200)
    with book.transaction(expected):
        _ensure_history(book)
        book.event("history_snapshot", {"label": label})
        result = _snapshot(book, label)
    return result


def _apply_recorded_cards(state, before, after, revision):
    for cid in set(before) | set(after):
        if state.get(cid) != before.get(cid):
            api.fail("history_state_conflict", "Recorded card preimage does not match checkpoint replay", card=cid, revision=revision)
    for cid, value in after.items():
        if value is None:
            state.pop(cid, None)
        else:
            state[cid] = value


def _state_at_revision(book, revision):
    """Replay factual database receipts, never semantic deltas against new prose."""
    api.integer(revision, "publication revision")
    snap = book.db.execute("SELECT * FROM history_snapshots WHERE revision<=? ORDER BY revision DESC LIMIT 1", (revision,)).fetchone()
    state, cursor = {}, 0
    if snap:
        manifest = json.loads(_body(book, snap["manifest_sha"]))
        state = {cid: json.loads(_body(book, sha)) for cid, sha in manifest["cards"].items()}
        cursor = snap["revision"]
    non_card_events = {"plan", "adopt", "adopt_backfill", "analysis", "ingest", "report", "world_save", "history_dependencies",
                       "history_snapshot", "history_branch_start", "history_branch_update", "history_branch_refresh"}
    replayed = 0
    for row in book.db.execute("SELECT revision,kind,data FROM events WHERE revision>? AND revision<=? ORDER BY revision", (cursor, revision)):
        if row["revision"] != cursor + 1:
            api.fail("history_unavailable", "Publication event history has a gap", after_revision=cursor)
        cursor = row["revision"]
        kind, data = row["kind"], json.loads(row["data"])
        if kind == "notes":
            _apply_recorded_cards(state, data["before"], {c["id"]: c for c in data["after"]}, cursor)
        elif kind in ("commit_chapter", "replace_chapter"):
            if kind == "replace_chapter":
                previous = data.get("previous", {}).get("receipt")
                previous = json.loads(previous) if isinstance(previous, str) else previous
                if not previous or "before" not in previous or "after" not in previous:
                    api.fail("history_unavailable", "Replacement lacks a reversible original receipt", revision=cursor)
                _apply_recorded_cards(state, previous["after"], previous["before"], cursor)
            receipt = data["receipt"]
            _apply_recorded_cards(state, receipt["before"], receipt["after"], cursor)
        elif kind == "history_publish":
            after = {c["id"]: c["after"] for c in data["after"]}
            before = {cid: data["before"].get(cid) for cid in after}
            _apply_recorded_cards(state, before, after, cursor)
        elif kind not in non_card_events:
            api.fail("history_event_unsupported", "Unknown write event cannot be guessed during historical state reconstruction", kind=kind, revision=cursor)
        replayed += 1
    if cursor != revision:
        api.fail("history_unavailable", "Publication revision is not covered by recorded events", revision=revision, reached=cursor)
    return state, dict(snap) if snap else None, replayed


def history_state(book, chapter, before=False, offset=0, limit=50, budget=DEFAULT_BUDGET):
    api.integer(chapter, "chapter", 1)
    api.integer(offset, "offset")
    api.integer(limit, "limit", 1)
    if limit > 200:
        api.fail("invalid_input", "history-state limit cannot exceed 200 cards")
    with book.transaction():
        _ensure_history(book)
        head = _head(book, chapter)
        revision = head["publication_revision"] - (1 if before else 0)
        cards, snap, replayed = _state_at_revision(book, revision)
        ids = sorted(cards)
        result = {"chapter": chapter, "version": head["id"], "publication_revision": head["publication_revision"],
                  "state_revision": revision, "before_publication": bool(before), "snapshot": snap,
                  "replayed_events": replayed, "total_cards": len(ids), "offset": offset,
                  "cards": [cards[cid] for cid in ids[offset:offset + limit]],
                  "scope": "Database card state at this publication revision, not the in-story chronology; no revised semantic delta is replayed."}
    return api.bounded_packet(result, budget)


def _dependency_kind(kind):
    return {"entity": "world.entities", "rule": "world.rules"}.get(kind, kind)


def _world_refs(values):
    return sorted({(_dependency_kind(value["kind"]), value["ref"]) for value in values})


def _impact(book, target, impact_cards=(), impact_world=()):
    heads = {r["chapter"]: dict(r) for r in book.db.execute("SELECT v.* FROM history_heads h JOIN history_versions v ON v.id=h.version")}
    if target not in heads:
        api.fail("chapter_missing", "Historical chapter is not recorded", chapter=target)
    reverse, produced, produced_world, hints = {}, {}, {}, set()
    record_links, evidenced_world = {}, {}
    for chapter, row in heads.items():
        receipt = json.loads(row["receipt"])
        produced[chapter] = set(receipt.get("after", {})) | set(receipt.get("before", {})) | set(receipt.get("history_state_ids", []))
        produced_world[chapter] = _world_refs(receipt.get("history_world_ids", []))
    for row in book.db.execute("SELECT h.chapter,e.kind,e.ref FROM history_edges e JOIN history_heads h ON h.version=e.version"):
        reverse.setdefault((_dependency_kind(row["kind"]), row["ref"]), set()).add(row["chapter"])
    # A changed source body reaches its observed records; a changed record also
    # requires review of its source body. Plans have no evidence chapter to add.
    for ev in book.db.execute("SELECT kind,record_id,chapter FROM world_evidence WHERE mode='chapter' AND retired=0"):
        if ev["chapter"] in heads:
            key = ("world." + ev["kind"], ev["record_id"])
            evidenced_world.setdefault(ev["chapter"], set()).add(key)
            reverse.setdefault(key, set()).add(ev["chapter"])
    # These are recorded foreign-key dependencies, not inferred story semantics.
    for kind, query in (
        ("knowledge", "SELECT k.fact,k.id FROM world_knowledge k JOIN world_evidence s ON s.kind='facts' AND s.record_id=k.fact JOIN world_evidence t ON t.kind='knowledge' AND t.record_id=k.id WHERE s.retired=0 AND t.retired=0"),
        ("rules", "SELECT r.fact,r.rule_id FROM world_rule_requires r JOIN world_evidence s ON s.kind='facts' AND s.record_id=r.fact JOIN world_evidence t ON t.kind='rules' AND t.record_id=r.rule_id WHERE s.retired=0 AND t.retired=0")):
        for source, consumer in book.db.execute(query):
            record_links.setdefault(("world.facts", source), set()).add(("world." + kind, consumer))
    reasons, pending = {target: "changed_body"}, deque([("chapter", str(target))])
    # Missing declarations cannot prove a later chapter independent.
    for chapter, row in heads.items():
        if chapter > target and not row["complete"]:
            reasons[chapter] = "semantic_dependencies_incomplete"
            pending.append(("chapter", str(chapter)))
    pending.extend(("card", cid) for cid in impact_cards)
    pending.extend(_world_refs(impact_world))
    visited, world_keys = set(), set()
    while pending:
        key = pending.popleft()
        if key in visited:
            continue
        visited.add(key)
        if key[0] == "chapter":
            chapter = int(key[1])
            pending.extend(("card", cid) for cid in produced[chapter])
            pending.extend(produced_world[chapter])
            pending.extend(evidenced_world.get(chapter, ()))
        elif key[0].startswith("world."):
            world_keys.add(key)
            pending.extend(record_links.get(key, ()))
        for affected in reverse.get(key, ()):
            if affected not in reasons:
                reasons[affected] = "dependency:" + key[0] + ":" + key[1]
                pending.append(("chapter", str(affected)))
    state_ids = set(impact_cards)
    fences = {}
    for chapter in reasons:
        state_ids.update(produced[chapter])
        fences["plan:" + str(chapter)] = _resolve(book, "plan", str(chapter))
        for dep in _edges(book, heads[chapter]["id"]):
            key = dep["kind"] + ":" + dep["ref"]
            fences[key] = _resolve(book, dep["kind"], dep["ref"])
            if dep["kind"] in ("entity", "rule", "card") or dep["kind"].startswith("world."):
                hints.add(key)
    for cid in state_ids:
        fences["card:" + cid] = _resolve(book, "card", cid)
    required_world = {key for chapter in reasons for key in evidenced_world.get(chapter, ())}
    for kind, ref in world_keys:
        fences[kind + ":" + ref] = _resolve(book, kind, ref)
        # Source observations already have their own paged world-review list.
        # Keep existing declared hints, but do not duplicate every source record
        # solely because typed traversal now visits it on the way to consumers.
        if (kind, ref) not in required_world:
            hints.add(kind + ":" + ref)
    return heads, reasons, sorted(state_ids), fences, sorted(hints)


def _review_scope(book, target, impact_cards=(), impact_world=()):
    heads, reasons, state_ids, fences, hints = _impact(book, target, impact_cards, impact_world)
    required = []
    for chapter in reasons:
        for ev in book.db.execute("SELECT kind,record_id,sha FROM world_evidence WHERE chapter=? AND mode='chapter' AND retired=0", (chapter,)):
            required.append({"kind": ev["kind"], "id": ev["record_id"], "chapter": chapter, "sha": ev["sha"]})
            fences["world." + ev["kind"] + ":" + ev["record_id"]] = _resolve(book, "world." + ev["kind"], ev["record_id"])
    return heads, reasons, state_ids, fences, hints, sorted(required, key=lambda e: (e["kind"], e["id"]))


def _state_impact_cards(data):
    # Keep an expanded branch's scope stable even if a later edit restores a card.
    cards = set(data.get("impact_cards", []))
    cards.update(change["id"] for change in data["state_changes"]
                 if (_hash(change["after"]) if change["after"] is not None else None) != change["before_sha"])
    return sorted(cards)


def _world_change_value(book, kind, raw):
    """Compare digest fields without treating candidate evidence as published.

    Normalization uses the world's schema and scalar/list validators. References,
    exact body evidence and batch constraints remain checked at publication, so
    a patch can still cite its pending body or an entity created in that batch.
    """
    world = api.world
    fields = world.FIELDS[kind]
    row = {key: world._value(raw.get(key, world.DEFAULTS.get(key)), typ, kind + "." + key)
           for key, typ in fields.items()}
    evidence, entities, requires = None, [], []
    if kind != "entities":
        ev = api.object_value(raw.get("evidence"), "world evidence")
        if ev.get("kind") == "chapter":
            evidence = {"mode": "chapter", "chapter": world._value(ev.get("chapter"), "positive", "evidence.chapter"),
                        "sha": _sha(ev.get("sha256")), "quote": api.text_field(ev.get("quote"), "evidence.quote", 1400), "note": None}
        else:
            evidence = world._evidence(book, ev)
        entities = world._list_ids(raw.get("entities", []), kind + ".entities")
    for key in ("actor", "subject", "place", "sender", "receiver", "resource", "entity"):
        if row.get(key) is not None:
            entities.append(row[key])
    if kind == "rules":
        requires = world._list_ids(raw.get("requires", []), "rules.requires")
    return row, evidence, sorted(set(entities)), requires


def _changed_world_refs(book, data):
    changed = set()
    for kind, values in data.get("world_changes", {}).items():
        if kind not in api.world.FIELDS and kind != "retirements":
            api.fail("invalid_input", "Unknown world change kind", kind=kind)
        if not isinstance(values, list):
            api.fail("invalid_input", "World changes require named record arrays")
        for raw in values:
            api.object_value(raw, "world change")
            if kind == "aliases":
                continue  # Alias names do not change an ID-bound record digest.
            actual_kind = raw.get("kind") if kind == "retirements" else kind
            if actual_kind not in api.world.FIELDS or actual_kind == "aliases":
                api.fail("invalid_input", "Unknown world record kind", kind=actual_kind)
            rid = api.world._value(raw.get("id"), "id", "world record id")
            old = api.world._stored(book, actual_kind, rid)
            if kind == "retirements" or old != _world_change_value(book, actual_kind, raw):
                changed.add(("world." + actual_kind, rid))
    return [{"kind": kind, "ref": ref} for kind, ref in sorted(changed)]


def _state_impact_world(book, data):
    keys = _world_refs(data.get("impact_world", []) + _changed_world_refs(book, data))
    return [{"kind": kind, "ref": ref} for kind, ref in keys]


def _expand_state_scope(book, target, data):
    cards = _state_impact_cards(data)
    world = _state_impact_world(book, data)
    heads, reasons, state_ids, fences, hints, required = _review_scope(book, target, cards, world)
    added = sorted(set(map(str, reasons)) - set(data["base_heads"]), key=int)
    missing = sorted(chapter for chapter in reasons if fences["plan:" + str(chapter)] is None)
    if missing:
        api.fail("plan_missing", "Save the missing reviewed plans, refresh this branch, then resubmit the same history-update payload; its state/world changes still need scope expansion",
                 chapter=missing[0], chapters=missing[:25], missing_plan_count=len(missing), recovery_command="plan")
    updated = {"impact_cards": cards, "impact_world": world,
               "base_heads": {str(c): heads[c]["id"] for c in reasons},
               "base_shas": {str(c): heads[c]["sha"] for c in reasons},
               "reasons": {str(c): why for c, why in reasons.items()},
               "required_state_ids": state_ids, "fences": fences,
               "entity_search_hints": hints, "world_review_required": required}
    changed = any(data.get(key, [] if key in ("impact_cards", "impact_world") else None) != value for key, value in updated.items())
    data.update(updated)
    if added:
        for candidate in data["candidates"].values():
            candidate["review"] = None
    return len(added), changed


def branch_start(book, chapter, expected, label="", budget=DEFAULT_BUDGET, limit=25, state_limit=50):
    chapter = api.integer(chapter, "chapter", 1)
    if label:
        api.text_field(label, "branch label", 200)
    with book.transaction(expected):
        _ensure_history(book)
        heads, reasons, state_ids, fences, hints, world_required = _review_scope(book, chapter)
        missing_plans = sorted(c for c in reasons if fences["plan:" + str(c)] is None)
        if missing_plans:
            api.fail("plan_missing", "Save reviewed plans for the affected chapters before starting this history branch; then reload status and retry history-start",
                     chapter=missing_plans[0], chapters=missing_plans[:25], missing_plan_count=len(missing_plans),
                     recovery_command="plan")
        baseline_revision = heads[chapter]["publication_revision"] - 1
        baseline, snap, replayed = _state_at_revision(book, baseline_revision)
        baseline_sha = book.intern_body(api.dumps({cid: book.intern_body(api.dumps(card)) for cid, card in baseline.items()}))
        data = {"label": label, "snapshot": snap, "baseline_state_sha": baseline_sha,
                "baseline_revision": baseline_revision, "baseline_replayed_events": replayed,
                "base_heads": {str(c): heads[c]["id"] for c in reasons},
                "base_shas": {str(c): heads[c]["sha"] for c in reasons},
                "reasons": {str(c): why for c, why in reasons.items()},
                "required_state_ids": state_ids, "fences": fences, "entity_search_hints": hints,
                "world_review_required": world_required, "impact_cards": [], "impact_world": [],
                "candidates": {}, "state_changes": [], "world_changes": {}, "semantic_review": None}
        bid = uuid.uuid4().hex
        revision = book.event("history_branch_start", {"branch": bid, "chapter": chapter, "affected": sorted(reasons)})
        book.db.execute("INSERT INTO history_branches VALUES (?,?,?,?,?,NULL)", (bid, chapter, "candidate", revision, api.dumps(data)))
        result = api.bounded_packet(_inspection(book, bid, chapter, revision, data, state_limit=state_limit, limit=limit), budget)
    return result


def _branch(book, bid):
    row = book.db.execute("SELECT * FROM history_branches WHERE id=?", (bid,)).fetchone()
    if not row:
        api.fail("branch_missing", "Unknown historical revision branch", branch=bid)
    return row, json.loads(row["data"])


def _check_fences(book, data):
    for chapter, version in data["base_heads"].items():
        head = _head(book, int(chapter))
        current_sha = _resolve(book, "chapter", chapter)
        if head["id"] != version or current_sha != data["base_shas"][chapter]:
            api.fail("stale_branch", "A reviewed chapter version changed; start a new branch", chapter=int(chapter))
    for key, sha in data["fences"].items():
        kind, ref = key.split(":", 1)
        if _resolve(book, kind, ref) != sha:
            api.fail("stale_dependency", "Branch evidence/state changed; start a new branch", dependency=key)


def _editable(book, row, data):
    if row["status"] != "candidate":
        api.fail("branch_published", "Published branches are immutable; start another branch")
    if row["revision"] != book.meta("revision"):
        api.fail("stale_branch", "State changed; refresh this branch to verify unaffected evidence first",
                 expected=row["revision"], actual=book.meta("revision"))
    _check_fences(book, data)


def branch_dependencies(book, branch_id, chapter, budget=DEFAULT_BUDGET):
    """Resolve review candidates without entering the latest-chapter write context."""
    chapter = api.integer(chapter, "chapter", 1)
    with book.read_snapshot():
        row, data = _branch(book, branch_id)
        _editable(book, row, data)
        c = str(chapter)
        if c not in data["base_heads"]:
            api.fail("chapter_missing", "Chapter is outside this branch's affected scope", chapter=chapter)
        plan = book.get_plan(chapter)
        keys = {(dep["kind"], dep["ref"]) for dep in _edges(book, data["base_heads"][c])}
        keys.update((dep["kind"], dep["ref"]) for dep in data["candidates"].get(c, {}).get("dependencies", []))
        keys.update(("card", cid) for cid in plan["requires"])
        scopes = {"global"} | {f"{key}:{plan[key]}" for key in ("volume", "arc", "line") if key in plan}
        scopes.update("entity:" + eid for eid in api.world.resolve(book, plan.get("entities", []), plan.get("line")))
        keys.update(("card", r[0]) for r in book.db.execute(
            f"SELECT id FROM card_index WHERE scope IN ({','.join('?' for _ in scopes)}) AND status='active' "
            "AND (critical=1 OR (kind='hook' AND due<=?))", (*sorted(scopes), chapter)))
        world = api.world.context(book, plan, chapter)
        for field, kind in (("entities", "entities"), ("facts", "facts"), ("propositions", "facts"),
                            ("knowledge", "knowledge"), ("hooks", "hooks"), ("rules", "rules"),
                            ("uses", "uses"), ("arc_steps", "arc_steps"),
                            ("volume", "volumes"), ("arc", "arcs"), ("line", "lines"), ("line_candidates", "lines")):
            records = world.get(field) or []
            if isinstance(records, dict):
                records = [records]
            keys.update(("world." + kind, record["id"]) for record in records)
        previous = book.db.execute("SELECT chapter FROM chapters WHERE chapter<? ORDER BY chapter DESC LIMIT 1", (chapter,)).fetchone()
        if previous:
            keys.add(("chapter", str(previous[0])))
        candidates, unavailable = [], []
        for kind, ref in sorted(keys - {("chapter", c)}):
            sha = data["candidates"].get(ref, {}).get("sha") if kind == "chapter" else None
            sha = sha or _resolve(book, kind, ref)
            target = {"kind": kind, "ref": ref}
            if sha is None:
                unavailable.append(target)
            else:
                candidates.append({**target, "sha": sha})
        return api.bounded_packet({"book_id": book.meta("id"), "branch": branch_id, "revision": row["revision"],
            "chapter": chapter, "candidates": candidates, "unavailable": unavailable, "world_warnings": world.get("warnings", []),
            "scope": "Current record hashes and this branch's saved chapter candidates; not a reconstruction of chapter-before card state.",
            "review_required": "Read actual evidence, select dependencies, and resolve unavailable or omitted sources before declaring completeness. This list is not a complete semantic review."}, budget)


def candidate_fingerprint(value):
    """Public helper: review binds summary and dependencies as well as the body."""
    return _hash({**{key: value[key] for key in ("sha", "summary", "dependencies", "complete")},
                  "external_sha256": value.get("external_sha256")})


def _external(book, chapter):
    relative = book.chapter_external_path(int(chapter))
    path = api.safe_path(book.root, relative)
    row = book.db.execute("SELECT sha,written_sha FROM artifacts WHERE path=?", (relative,)).fetchone()
    if not row and relative != book.chapter_path(int(chapter)):
        row = book.meta("chapter_retired:" + relative)
    if not row or not path.is_file():
        return None
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"path": relative, "sha256": sha} if sha not in {row["sha"], row["written_sha"]} else None


def _manifest(data):
    return _hash({"candidates": {c: candidate_fingerprint(v) for c, v in data["candidates"].items()},
                  "state_changes": data["state_changes"], "world_changes": data.get("world_changes", {}),
                  "base_heads": data["base_heads"], "fences": data["fences"]})


def _review(text, candidate, review):
    api.object_value(review, "chapter review")
    if review.get("draft_sha256") != candidate["sha"] or review.get("candidate_sha256") != candidate_fingerprint(candidate):
        api.fail("stale_review", "Review must bind the candidate body, summary, and dependencies")
    checks = api.object_value(review.get("checks"), "review.checks")
    for name in api.CHECKS:
        check = api.object_value(checks.get(name), "review." + name)
        api.text_field(check.get("note"), "review note", 2000)
        if api.text_field(check.get("quote"), "review quote", 1200) not in text:
            api.fail("invalid_evidence", "Review quote is absent from candidate body", check=name)
    issues = review.get("issues")
    if not isinstance(issues, list):
        api.fail("invalid_input", "review.issues must be an array")
    for issue in issues:
        api.object_value(issue, "review issue")
        if issue.get("severity") not in ("advice", "minor", "major", "blocker"):
            api.fail("invalid_input", "issue severity must be advice/blocker (legacy minor/major are also accepted)")
        if issue["severity"] in ("major", "blocker"):
            api.fail("review_blocked", "Resolve major/blocker issues before publication")
    return review


def _state_changes(book, values, candidates, required):
    if not isinstance(values, list) or len(values) > 10000:
        api.fail("invalid_input", "state_changes must be an array of at most 10000 explicit final card decisions")
    result, seen = [], set()
    for change in values:
        api.object_value(change, "state change")
        cid = api.text_field(change.get("id"), "state change id", 80)
        if cid in seen:
            api.fail("duplicate_id", "Each final card can be decided once", card=cid)
        seen.add(cid)
        before = change.get("before_sha")
        if before is not None:
            _sha(before, "before_sha")
        if _resolve(book, "card", cid) != before:
            api.fail("stale_dependency", "Final card decision does not match current state", card=cid)
        if "after" not in change:
            api.fail("invalid_input", "A state decision requires after (card object or null)")
        after = api.valid_card(change["after"]) if change["after"] is not None else None
        if after is not None and after["id"] != cid:
            api.fail("invalid_input", "State card id must match decision id")
        chapter = str(api.integer(change.get("chapter"), "evidence chapter", 1))
        if chapter not in candidates:
            api.fail("invalid_evidence", "Final state evidence must reference a reviewed branch candidate")
        quote = api.text_field(change.get("quote"), "state quote", 1200)
        if quote not in _body(book, candidates[chapter]["sha"]):
            api.fail("invalid_evidence", "State quote is absent from candidate body", card=cid)
        result.append({"id": cid, "before_sha": before, "after": after, "chapter": int(chapter),
                       "quote": quote, "note": api.text_field(change.get("note"), "state decision note", 2000)})
    return sorted(result, key=lambda c: c["id"])


def branch_update(book, branch_id, payload, expected, budget=DEFAULT_BUDGET, limit=25, state_limit=50):
    api.object_value(payload, "branch update")
    with book.transaction(expected):
        row, data = _branch(book, branch_id)
        _editable(book, row, data)
        entries = payload.get("chapters", [])
        if not isinstance(entries, list):
            api.fail("invalid_input", "chapters must be an array")
        seen, prepared = set(), {}
        # Intern all submitted bodies before validating cross-chapter references.
        for entry in entries:
            api.object_value(entry, "chapter candidate")
            c = str(api.integer(entry.get("chapter"), "chapter", 1))
            if c not in data["base_heads"] or c in seen:
                api.fail("invalid_input", "Candidate chapter must appear once in the affected scope", chapter=c)
            seen.add(c)
            old = data["candidates"].get(c)
            text = entry.get("text", _body(book, (old or {}).get("sha", data["base_shas"][c])))
            if not isinstance(text, str):
                api.fail("invalid_input", "candidate text must be text")
            plan_row = book.db.execute("SELECT data FROM plans WHERE chapter=?", (int(c),)).fetchone()
            if not plan_row:
                api.fail("plan_missing", "Historical/imported revisions require a current reviewed plan", chapter=int(c))
            check = api.lint_text(text, json.loads(plan_row[0]))
            if not check["ok"]:
                api.fail("lint_failed", "Historical candidate fails deterministic checks", chapter=int(c), lint=check)
            prepared[c] = (entry, text, book.intern_body(text))
        shas = {c: v["sha"] for c, v in data["candidates"].items()}
        shas.update({c: value[2] for c, value in prepared.items()})
        changed = False
        for c, (entry, text, sha) in prepared.items():
            old = data["candidates"].get(c, {})
            complete = entry.get("complete", old.get("complete", False))
            if type(complete) is not bool:
                api.fail("invalid_input", "Candidate complete must be boolean")
            dependencies = _dependencies(book, entry.get("dependencies", old.get("dependencies", [])), shas)
            if any(d["kind"] == "chapter" and d["ref"] == c for d in dependencies):
                api.fail("invalid_input", "Candidate cannot depend on itself")
            _required_dependencies(book.get_plan(int(c)), dependencies, complete)
            candidate = {"sha": sha, "summary": api.text_field(entry.get("summary", old.get("summary")), "candidate summary", 800),
                         "dependencies": dependencies, "complete": complete, "review": None}
            external_sha = entry.get("external_sha256", old.get("external_sha256"))
            if external_sha is not None:
                _sha(external_sha, "external_sha256")
                external = _external(book, c)
                if not external or external["sha256"] != external_sha:
                    api.fail("stale_external", "Outside edit changed or disappeared; inspect and review its current bytes", chapter=int(c))
                candidate["external_sha256"] = external_sha
            review = entry.get("review")
            if review is None and old and candidate_fingerprint(old) == candidate_fingerprint(candidate):
                review = old.get("review")
            if review is not None:
                candidate["review"] = _review(text, candidate, review)
            changed |= not old or candidate_fingerprint(old) != candidate_fingerprint(candidate)
            data["candidates"][c] = candidate
        if "state_changes" in payload:
            values = _state_changes(book, payload["state_changes"], data["candidates"], data["required_state_ids"])
            changed |= values != data["state_changes"]
            data["state_changes"] = values
        if "world_changes" in payload:
            changes = api.object_value(payload["world_changes"], "world_changes")
            changed |= changes != data.get("world_changes", {})
            data["world_changes"] = changes
        added, scope_changed = _expand_state_scope(book, row["target"], data)
        changed |= scope_changed
        if changed:
            data["semantic_review"] = None
        if "semantic_review" in payload:
            review = api.object_value(payload["semantic_review"], "semantic_review")
            if review.get("manifest_sha256") != _manifest(data):
                api.fail("stale_review", "Semantic review must bind the complete candidate/state manifest")
            for field in ("note", "state_review", "coverage_review"):
                api.text_field(review.get(field), "semantic_review." + field, 6000)
            reviewed = review.get("reviewed_chapters")
            if not isinstance(reviewed, list) or any(type(c) is not int or c < 1 for c in reviewed):
                api.fail("invalid_input", "semantic_review.reviewed_chapters must be a list of positive chapter numbers")
            if sorted(reviewed) != sorted(int(c) for c in data["base_heads"]):
                api.fail("review_incomplete", "Semantic review must cover every directly, indirectly, or uncertain affected chapter")
            if review.get("issues") != []:
                api.fail("review_blocked", "Resolve semantic review issues before publication")
            data["semantic_review"] = review
        revision = book.event("history_branch_update", {"branch": branch_id, "chapters": sorted(int(c) for c in prepared)})
        book.db.execute("UPDATE history_branches SET data=?,revision=? WHERE id=?", (api.dumps(data), revision, branch_id))
        result = api.bounded_packet({**_inspection(book, branch_id, row["target"], revision, data, state_limit=state_limit, limit=limit),
                                     "scope_expanded": bool(added), "added_chapter_count": added}, budget)
    return result


def _page(values, offset, limit, name):
    api.integer(offset, name + " offset")
    api.integer(limit, name + " limit", 1)
    if limit > 200:
        api.fail("invalid_input", "Inspection pages cannot exceed 200 entries", section=name)
    selected = values[offset:offset + limit]
    return selected, {"offset": offset, "limit": limit, "total": len(values),
                      "next_offset": offset + len(selected) if offset + len(selected) < len(values) else None,
                      "complete": offset == 0 and len(selected) == len(values)}


def _inspection(book, bid, target, revision, data, state_offset=0, state_limit=50,
                affected_offset=0, world_offset=0, hints_offset=0, limit=25):
    candidates = data["candidates"]
    all_chapters = sorted(data["base_heads"], key=int)
    chapters, affected_page = _page(all_chapters, affected_offset, limit, "affected")
    ids, state_page = _page(data["required_state_ids"], state_offset, state_limit, "state")
    world, world_page = _page(data.get("world_review_required", []), world_offset, limit, "world")
    hints, hints_page = _page(data["entity_search_hints"], hints_offset, limit, "hints")
    pages = {"affected": affected_page, "state": state_page, "world": world_page, "hints": hints_page}
    cards = book.cards(ids)
    manifest_sha = _manifest(data)
    return {"branch": bid, "chapter": target, "revision": revision, "snapshot": data["snapshot"],
            "baseline_state_sha256": data["baseline_state_sha"], "baseline_publication_revision": data["baseline_revision"],
            "baseline_replayed_events": data["baseline_replayed_events"],
            "affected": [{"chapter": int(c), "path": book.chapter_path(int(c)),
                          "reason": data["reasons"][c], "base_sha256": data["base_shas"][c],
                          "candidate_sha256": candidate_fingerprint(candidates[c]) if c in candidates else None,
                          "draft_sha256": candidates[c]["sha"] if c in candidates else None,
                          "external_edit": _external(book, c),
                          "reviewed": bool(candidates.get(c, {}).get("review"))} for c in chapters],
            "pages": pages, "complete": all(p["complete"] for p in pages.values()),
            "pagination_note": "Each complete flag means this response includes that entire section. A final page with a nonzero offset is not the whole review scope. Read all pages before declaring full coverage.",
            "required_state_ids": ids, "entity_search_hints": hints,
            "state_review_template": [{"id": cid, "before_sha": _hash(cards[cid]) if cid in cards else None,
                                       "before": cards.get(cid), "chapter": None, "quote": "", "note": ""} for cid in ids],
            "state_review_offset": state_offset, "state_review_total": state_page["total"],
            "state_review_next_offset": state_page["next_offset"],
            "state_review_instruction": "For each decision explicitly add after: a complete card object to keep/update, or null for a reviewed deletion. Cite a candidate chapter and exact quote; fill the decision note.",
            "world_review_required": world,
            "semantic_warning": "Dependency declarations are author evidence. Recheck unstated entity/rule effects and final state; no old delta is replayed.",
            "manifest_sha256": manifest_sha, "semantic_reviewed": bool(data["semantic_review"]),
            "review_scope": {"total_chapters": len(all_chapters), "sha256": _hash([int(c) for c in all_chapters]),
                             "chapter_ids_page": [int(c) for c in chapters], "complete": affected_page["complete"]},
            "review_template": {"manifest_sha256": manifest_sha,
                                "reviewed_chapters": [int(c) for c in chapters] if affected_page["complete"] else None,
                                "note": "", "state_review": "", "coverage_review": "", "issues": []}}


def branch_saved(book, branch_id, budget=DEFAULT_BUDGET):
    """Read persisted decisions verbatim, even when a branch cannot be edited."""
    with book.read_snapshot():
        row, data = _branch(book, branch_id)
        return api.bounded_packet({
            "branch": branch_id, "chapter": row["target"], "status": row["status"],
            "revision": row["revision"], "current_revision": book.meta("revision"),
            "manifest_sha256": _manifest(data),
            "candidate_chapters": sorted(map(int, data["candidates"])),
            "saved": {"state_changes": data["state_changes"], "world_changes": data["world_changes"],
                      "semantic_review": data["semantic_review"]},
            "note": "Saved branch decisions, not current canonical state or renewed approval. "
                    "Read candidate prose and chapter reviews with history-inspect --chapter. "
                    "Null semantic_review means no current saved overall review. "
                    "State/world updates replace the supplied section; preserve its other valid entries."
        }, budget)


def branch_inspect(book, branch_id, chapter=None, state_offset=0, state_limit=50,
                   affected_offset=0, world_offset=0, hints_offset=0, limit=25, budget=DEFAULT_BUDGET):
    with book.read_snapshot():
        return _branch_inspect(book, branch_id, chapter, state_offset, state_limit,
                               affected_offset, world_offset, hints_offset, limit, budget)


def _branch_inspect(book, branch_id, chapter=None, state_offset=0, state_limit=50,
                    affected_offset=0, world_offset=0, hints_offset=0, limit=25, budget=DEFAULT_BUDGET):
    row, data = _branch(book, branch_id)
    if chapter is not None:
        c = str(api.integer(chapter, "chapter", 1))
        if c not in data["base_heads"]:
            api.fail("chapter_missing", "Chapter is outside this branch's affected scope", chapter=chapter)
        chapter_offset = sorted(data["base_heads"], key=int).index(c)
        api.integer(limit, "inspection limit", 1)
        if not affected_offset <= chapter_offset < affected_offset + limit:
            affected_offset = (chapter_offset // limit) * limit
    result = _inspection(book, branch_id, row["target"], row["revision"], data, state_offset, state_limit,
                         affected_offset, world_offset, hints_offset, limit)
    result["status"] = row["status"]
    result["current_revision"] = book.meta("revision")
    result["receipt"] = json.loads(row["receipt"]) if row["receipt"] else None
    if result["receipt"] and "chapters" in result["receipt"]:
        result["receipt"]["chapters"], result["pages"]["receipt_chapters"] = _page(
            result["receipt"]["chapters"], affected_offset, limit, "receipt chapters")
    if chapter is not None:
        head = book.db.execute("SELECT * FROM history_versions WHERE id=?", (data["base_heads"][c],)).fetchone()
        result["base"] = {"text": _body(book, head["sha"]), "summary": head["summary"],
                          "dependencies": _edges(book, head["id"]), "complete": bool(head["complete"])}
        candidate = data["candidates"].get(c)
        result["candidate"] = {**candidate, "text": _body(book, candidate["sha"])} if candidate else None
        result["chapter_review_template"] = {"draft_sha256": candidate["sha"] if candidate else None,
                                              "candidate_sha256": candidate_fingerprint(candidate) if candidate else None,
                                              "checks": {check: {"note": "", "quote": ""} for check in api.CHECKS}, "issues": []}
    return api.bounded_packet(result, budget)


def branch_refresh(book, branch_id, expected, budget=DEFAULT_BUDGET, limit=25, state_limit=50):
    with book.transaction(expected):
        row, data = _branch(book, branch_id)
        if row["status"] != "candidate":
            api.fail("branch_published", "Published branches cannot be refreshed")
        _check_fences(book, data)
        # Validate the recorded scope first. Legacy saved decisions may still
        # need update to expand it after missing plans are supplied; publish
        # separately fences those decisions until that migration is reviewed.
        _, reasons, state_ids, fences, _, required = _review_scope(
            book, row["target"], data.get("impact_cards", []), data.get("impact_world", []))
        if (set(map(str, reasons)) != set(data["base_heads"]) or state_ids != data["required_state_ids"] or fences != data["fences"] or
                required != data.get("world_review_required", [])):
            api.fail("stale_branch", "New dependent material appeared; start a fresh branch with its expanded review scope")
        revision = book.event("history_branch_refresh", {"branch": branch_id})
        book.db.execute("UPDATE history_branches SET revision=? WHERE id=?", (revision, branch_id))
        result = api.bounded_packet(_inspection(book, branch_id, row["target"], revision, data, state_limit=state_limit, limit=limit), budget)
    return result


def branch_publish(book, branch_id, expected):
    with book.transaction():
        row, data = _branch(book, branch_id)
        if row["status"] == "published":
            result = {**json.loads(row["receipt"]), "idempotent": True}
        else:
            if api.integer(expected, "expected revision") != book.meta("revision"):
                api.fail("stale_revision", "State changed before historical publication")
            _editable(book, row, data)
            # Older candidates could save an extra state/world correction without
            # recording the consumers that now require fresh chapter reviews.
            changed_world = _changed_world_refs(book, data)
            _, reasons, _, _, _, _ = _review_scope(book, row["target"], _state_impact_cards(data), _state_impact_world(book, data))
            uncovered = sorted(set(reasons) - set(map(int, data["base_heads"])))
            if uncovered:
                api.fail("history_scope_changed", "Saved state/world changes affect unreviewed chapters; run history-update with an empty object or the preserved changes to expand this branch, then review its full scope",
                         chapters=uncovered[:25], missing_chapter_count=len(uncovered), recovery_command="history-update")
            integrity = book.integrity
            try:
                # Historical publication is infrequent and rewrites the shared
                # baseline. Its fence always checks the whole managed archive.
                book.integrity = "strict"
                pending, drift = book._export_health()
            finally:
                book.integrity = integrity
            accepted_external, external_paths = {}, {}
            for c, candidate in data["candidates"].items():
                if candidate.get("external_sha256"):
                    current = _external(book, c)
                    if not current or current["sha256"] != candidate["external_sha256"]:
                        api.fail("stale_external", "Reviewed outside edit changed before publication", chapter=int(c))
                    accepted_external[current["path"]] = current["sha256"]
                    external_paths[c] = current["path"]
            drift = [path for path in drift if path not in accepted_external]
            pending = [path for path in pending if path not in accepted_external]
            if pending or drift:
                api.fail("exports_unresolved", "Resolve exports/outside edits before historical publication", pending=pending[:10], changed=drift[:10])
            if set(data["candidates"]) != set(data["base_heads"]) or any(not c.get("review") for c in data["candidates"].values()):
                api.fail("review_incomplete", "Every affected chapter needs a body, refreshed summary, and bound review")
            semantic = data["semantic_review"]
            if not semantic or semantic.get("manifest_sha256") != _manifest(data):
                api.fail("review_incomplete", "Complete and bind the final state/semantic coverage review")
            if not set(data["required_state_ids"]) <= {c["id"] for c in data["state_changes"]}:
                api.fail("state_review_incomplete", "Explicitly preserve, change, or delete every affected produced card", required=data["required_state_ids"])
            candidates = data["candidates"]
            shas = {c: v["sha"] for c, v in candidates.items()}
            # Revalidate all receipts at the final fence; staging alone cannot pass.
            for c, candidate in candidates.items():
                _review(_body(book, candidate["sha"]), candidate, candidate["review"])
                _dependencies(book, candidate["dependencies"], shas)
                plan = book.get_plan(int(c))
                _required_dependencies(plan, candidate["dependencies"], candidate["complete"])
                check = api.lint_text(_body(book, candidate["sha"]), plan)
                if not check["ok"]:
                    api.fail("lint_failed", "Plan changed or historical draft fails checks", chapter=int(c), lint=check)
            decisions = _state_changes(book, data["state_changes"], candidates, data["required_state_ids"])
            before = book.cards(c["id"] for c in decisions)
            for change in decisions:
                if change["after"] is None:
                    book.delete_card(change["id"])
                else:
                    book.put_card(change["after"])
            receipts = {}
            for c, candidate in candidates.items():
                chapter = int(c)
                text = _body(book, candidate["sha"])
                receipt = {"history_branch": branch_id, "before": {}, "after": {},
                           "review": candidate["review"], "semantic_review": semantic,
                           "supersedes": data["base_heads"][c]}
                previous = json.loads(_head(book, chapter)["receipt"])
                receipt["history_state_ids"] = sorted(set(previous.get("before", {})) | set(previous.get("after", {})) |
                                                     set(previous.get("history_state_ids", [])) |
                                                     {d["id"] for d in decisions if d["chapter"] == chapter})
                world_ids = _world_refs(previous.get("history_world_ids", []) + (changed_world if chapter == row["target"] else []))
                if world_ids:
                    receipt["history_world_ids"] = [{"kind": kind, "ref": ref} for kind, ref in world_ids]
                accepted_sha = candidate.get("external_sha256")
                if accepted_sha:
                    accepted_sha = book.accept_chapter_external(chapter, external_paths[c], accepted_sha)
                imported = book.db.execute("SELECT imported FROM chapter_state WHERE chapter=?", (chapter,)).fetchone()[0]
                book.queue_chapter(chapter, text, plan=book.get_plan(chapter),
                                   accepted_sha=accepted_sha, imported=bool(imported))
                book.db.execute("UPDATE chapters SET text=?,sha=?,summary=?,receipt=?,input_hash=? WHERE chapter=?",
                                (text, candidate["sha"], candidate["summary"], api.dumps(receipt),
                                 _hash({"branch": branch_id, "candidate": candidate_fingerprint(candidate)}), chapter))
                book.index_chapter(chapter, text, candidate["summary"])
                receipts[c] = receipt
                book.db.execute("INSERT INTO history_invalidations VALUES (?,?,?,?)",
                                (branch_id, chapter, data["base_heads"][c], "superseded summary/review; branch semantic review replaces it"))
                book.db.execute("DELETE FROM history_cache WHERE chapter=?", (chapter,))
            invalidator = getattr(getattr(api, "world", None), "invalidate_chapters", None)
            world_changes = []
            if invalidator:
                invalidator(book, [int(c) for c in candidates])
                if data.get("world_changes"):
                    for values in data["world_changes"].values():
                        if not isinstance(values, list):
                            api.fail("invalid_input", "World changes require named record arrays")
                        for value in values:
                            if not isinstance(value, dict):
                                api.fail("invalid_input", "World change records must be objects")
                            evidence = value.get("evidence", {})
                            if isinstance(evidence, dict) and evidence.get("kind") == "chapter" and str(evidence.get("chapter")) not in candidates:
                                api.fail("invalid_evidence", "Historical world changes must cite a reviewed candidate chapter")
                    world_changes = api.world.apply_in_transaction(book, data["world_changes"], repair=True)
                remaining = []
                for c in candidates:
                    remaining.extend(dict(r) for r in book.db.execute("SELECT kind,record_id,chapter FROM world_evidence WHERE chapter=? AND valid=0 AND retired=0", (int(c),)))
                if remaining:
                    api.fail("world_review_incomplete", "Rebind or explicitly retire every affected world record before publication; canonical state is unchanged",
                             pending_world_review=remaining)
                for c in candidates:
                    plan = book.get_plan(int(c))
                    payload = {}
                    for kind in ("uses", "transfers"):
                        values = [v for v in data.get("world_changes", {}).get(kind, [])
                                  if v.get("evidence", {}).get("kind") == "chapter" and v["evidence"].get("chapter") == int(c)]
                        if values:
                            payload[kind] = values
                    if payload or any(key in plan for key in ("volume", "arc", "line", "entities", "time")):
                        checked = (api.world.check_transition(book, plan, int(c), payload) if payload else
                                   api.world.check(book, {**plan, "chapter": int(c)}))
                        if not checked["ok"]:
                            api.fail("world_constraint", "Resolve recorded rule/resource conflicts in the candidate world state", chapter=int(c), checks=checked)
            revision = book.event("history_publish", {"branch": branch_id, "chapters": sorted(map(int, candidates)),
                                                       "before": before, "after": decisions, "world_changes": world_changes,
                                                       "semantic_review": semantic})
            for c, candidate in candidates.items():
                _version(book, int(c), _body(book, candidate["sha"]), candidate["summary"], receipts[c], book.get_plan(int(c)),
                         candidate["dependencies"], candidate["complete"])
            result = {"committed": True, "idempotent": False, "branch": branch_id, "revision": revision,
                      "chapters": sorted(map(int, candidates))}
            book.db.execute("UPDATE history_branches SET status='published',revision=?,receipt=? WHERE id=?",
                            (revision, api.dumps(result), branch_id))
    return book.delivery(result)


def _cache_key(book, chapter, kind):
    head = _head(book, chapter)
    if not head["complete"] or _resolve(book, "chapter", str(chapter)) != head["sha"]:
        return None
    dependencies = []
    for dep in _edges(book, head["id"]):
        actual = _resolve(book, dep["kind"], dep["ref"])
        if actual != dep["sha"]:
            return None
        dependencies.append(dep)
    dependency_sha = _hash(dependencies)
    return _hash({"chapter": chapter, "kind": kind, "body": head["sha"], "dependencies": dependency_sha}), head["sha"], dependency_sha


def cache_put(book, chapter, kind, value, expected):
    api.integer(chapter, "chapter", 1)
    api.text_field(kind, "cache kind", 80)
    encoded = api.dumps(value)
    if len(encoded.encode("utf-8")) > 200000:
        api.fail("invalid_input", "A derived cache entry cannot exceed 200000 UTF-8 bytes")
    with book.transaction(expected):
        key = _cache_key(book, chapter, kind)
        if key is None:
            api.fail("cache_unverified", "Cache requires complete, unchanged body/dependency evidence")
        book.db.execute("INSERT INTO history_cache VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key[0], chapter, kind, key[1], key[2], encoded))
        revision = book.meta("revision")
    return {"cached": True, "key": key[0], "revision": revision}


def cache_get(book, chapter, kind, budget=DEFAULT_BUDGET):
    api.integer(chapter, "chapter", 1)
    api.text_field(kind, "cache kind", 80)
    with book.read_snapshot():
        key = _cache_key(book, chapter, kind)
        row = book.db.execute("SELECT value FROM history_cache WHERE key=?", (key[0],)).fetchone() if key else None
        return api.bounded_packet({"hit": row is not None, "value": json.loads(row[0]) if row else None,
                                   "key": key[0] if key else None, "evidence_current": bool(key),
                                   "dependency_review_declared_complete": bool(_head(book, chapter)["complete"]),
                                   "note": "Hashes confirm recorded evidence versions; dependency completeness is an author declaration, not an automatic semantic or quality verdict."}, budget)


def register_parser(sub, command):
    for name in sorted(COMMANDS):
        parser = command(name, "Versioned dependencies, reviewed historical branches, and evidence-bound caches",
                         DEFAULT_BUDGET if name in INSPECTION_COMMANDS or name in ("history-deps", "history-dependencies", "history-state", "history-saved", "cache-get") else None)
        if name == "history-deps":
            mode = parser.add_mutually_exclusive_group(required=True)
            mode.add_argument("--chapter", type=int, help="Read the published dependency declaration without changing state")
            mode.add_argument("--input", help="Replace the declaration with a reviewed payload; requires --expect")
            parser.add_argument("--expect", type=int, help="Required for --input; not accepted with --chapter")
            continue
        if name in INSPECTION_COMMANDS:
            parser.add_argument("--limit", type=int, default=25, help="Entries per affected/world/hints page, at most 200")
            parser.add_argument("--state-limit", type=int, default=50)
        if name in ("history-update", "cache-put"):
            parser.add_argument("--input", required=True)
        if name in ("history-start", "history-dependencies", "history-state", "cache-get", "cache-put"):
            parser.add_argument("--chapter", type=int, required=True)
        if name in ("history-start", "history-snapshot"):
            parser.add_argument("--label", default="manual")
        if name in ("history-inspect", "history-saved", "history-dependencies", "history-update", "history-refresh", "history-publish"):
            parser.add_argument("--branch", required=True)
        if name == "history-inspect":
            parser.add_argument("--chapter", type=int)
            parser.add_argument("--state-offset", type=int, default=0)
            parser.add_argument("--affected-offset", type=int, default=0)
            parser.add_argument("--world-offset", type=int, default=0)
            parser.add_argument("--hints-offset", type=int, default=0)
        if name not in ("history-inspect", "history-saved", "history-dependencies", "history-state", "cache-get"):
            parser.add_argument("--expect", type=int, required=True)
        if name.startswith("cache-"):
            parser.add_argument("--kind", required=True)
        if name == "history-state":
            parser.add_argument("--before", action="store_true")
            parser.add_argument("--offset", type=int, default=0)
            parser.add_argument("--limit", type=int, default=50)


def run(book, args):
    cmd = args.command
    if cmd == "history-deps":
        if args.chapter is not None:
            if args.expect is not None:
                api.fail("invalid_input", "history-deps --chapter is read-only; omit --input and --expect")
            return read_dependencies(book, args.chapter, args.budget_bytes)
        if args.expect is None:
            api.fail("invalid_input", "history-deps --input requires --expect for a reviewed replacement")
        return save_dependencies(book, api.read_json(args.input), args.expect)
    if cmd == "history-dependencies":
        return branch_dependencies(book, args.branch, args.chapter, args.budget_bytes)
    if cmd == "history-snapshot":
        return snapshot(book, args.label, args.expect)
    if cmd == "history-start":
        return branch_start(book, args.chapter, args.expect, args.label, args.budget_bytes, args.limit, args.state_limit)
    if cmd == "history-state":
        return history_state(book, args.chapter, args.before, args.offset, args.limit, args.budget_bytes)
    if cmd == "history-inspect":
        return branch_inspect(book, args.branch, args.chapter, args.state_offset, args.state_limit,
                              args.affected_offset, args.world_offset, args.hints_offset, args.limit, args.budget_bytes)
    if cmd == "history-saved":
        return branch_saved(book, args.branch, args.budget_bytes)
    if cmd == "history-update":
        return branch_update(book, args.branch, api.read_json(args.input), args.expect, args.budget_bytes, args.limit, args.state_limit)
    if cmd == "history-refresh":
        return branch_refresh(book, args.branch, args.expect, args.budget_bytes, args.limit, args.state_limit)
    if cmd == "history-publish":
        return branch_publish(book, args.branch, args.expect)
    if cmd == "cache-get":
        return cache_get(book, args.chapter, args.kind, args.budget_bytes)
    if cmd == "cache-put":
        return cache_put(book, args.chapter, args.kind, api.read_json(args.input), args.expect)
    raise AssertionError(cmd)
