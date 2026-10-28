"""Fresh literal-search oracle and distribution manifests; no installation."""
from pathlib import Path
import hashlib
import importlib.util
import json
import random
import sqlite3

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
search = load("fresh_search", "skills/story-skill/scripts/story_search.py")
rng = random.Random(2026102406)
expected = {}
checks = 0
with sqlite3.connect(":memory:") as db:
    db.executescript(search.SCHEMA)
    for step in range(160):
        identity = (rng.choice(["chapter", "card"]), str(rng.randrange(24)))
        old = dict(expected)
        if rng.randrange(4) == 0:
            search.remove(db, *identity)
            expected.pop(identity, None)
        else:
            value = rng.choice(["江棠持灯。", "杜承安还了借条。", "A-19 / 甲&乙", "灯😀亮。\n𠮷", "", "江棠把门推开。"])
            search.upsert(db, *identity, value, metadata={"step": step})
            expected[identity] = value
        if step % 7 == 0:
            db.rollback()
            expected = old
        else:
            db.commit()
        for query in ("江棠", "杜承安", "甲&", "😀", "𠮷", "A-19", "没有的名字"):
            kinds = rng.choice([None, [], ["chapter"], ["card"], ["chapter", "card"]])
            result = search.query(db, query, limit=200, kinds=kinds)
            oracle = {key for key, value in expected.items() if query in value and (kinds is None or key[0] in kinds)}
            assert result["complete"] and result["search_complete"], result
            assert {(hit["kind"], hit["key"]) for hit in result["matches"]} == oracle, (step, query, kinds, result)
            assert result["total"] == len(oracle)
            assert result["no_match_confirmed"] == (not oracle)
            checks += 1
p = load("distribution_package", "scripts/package.py")
i = load("distribution_install", "scripts/install.py")
n = load("distribution_npm", "scripts/package_npm.py")
version = p.current_version()
members = set(p.suite_files(version))
assert members == set(i.suite_files(version)) == set(n.payload_files(version))
assert p.skill_names(version) == i.skill_names(version) == n.skill_names(version)
target = "skills/story-skill/scripts/story_rank_sources.py"
assert any(data == (ROOT / target).read_bytes() for _, data in p.source_entries())
paths = [target, "skills/story-skill/scripts/story_search.py", "scripts/package.py", "scripts/install.py", "scripts/package_npm.py"]
summary = {"ok": True, "seed": 2026102406, "search_oracle_checks": checks,
           "distribution": {"version": version, "members": len(members), "skill_entries": len(p.skill_names(version)),
                            "modified_source_present_byte_exact": True, "installed": False},
           "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}}
(OUT / "probe-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(summary, ensure_ascii=False))
