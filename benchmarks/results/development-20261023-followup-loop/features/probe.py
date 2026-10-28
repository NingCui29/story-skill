"""Offline independent invariants for the non-browser audit; no real book writes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def search_oracle():
    search = load("feature_search", "skills/story-skill/scripts/story_search.py")
    db = sqlite3.connect(":memory:")
    db.executescript(search.SCHEMA)
    rng = random.Random(20261023)
    alphabet = "甲乙丙丁天地门𠮷😀e\u0301 aB.,%_\n\t"
    documents = {}
    for number in range(96):
        kind = ("chapter", "note", "analysis")[number % 3]
        text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(5, 100)))
        if number % 11 == 0:
            text = "同文不同身份 门甲"
        search.upsert(db, kind, str(number), text)
        documents[(kind, str(number))] = text
    db.commit()
    checks = 0
    for phase in range(3):
        queries = ["不存在的短语", "\n", "\t", " ", "𠮷", "e\u0301", "😀", "%", "_", "门甲"]
        for text in list(documents.values())[:60]:
            start = rng.randrange(len(text))
            queries.append(text[start:start + rng.randrange(1, 8)])
        for query in queries:
            for kinds in (None, ["chapter"], ["note", "analysis"], []):
                actual = search.query(db, query, limit=200, kinds=kinds)
                expected = {key for key, text in documents.items()
                            if query in text and (kinds is None or key[0] in kinds)}
                assert {(row["kind"], row["key"]) for row in actual["matches"]} == expected
                assert actual["search_complete"] and actual["complete"]
                assert actual["total"] == len(expected)
                assert actual["no_match_confirmed"] == (not expected)
                for row in actual["matches"]:
                    original = documents[(row["kind"], row["key"])]
                    assert original[row["start"]:row["end"]] == row["quote"] == query
                checks += 1
        if phase == 0:
            for key in list(documents)[::4]:
                search.remove(db, *key)
                del documents[key]
            for key in list(documents)[::3]:
                documents[key] = "更改后 Ω门 " + documents[key]
                search.upsert(db, *key, documents[key], {"changed": True})
            db.commit()
        elif phase == 1:
            before = tuple(db.iterdump())
            for key in list(documents)[::7]:
                search.remove(db, *key)
            search.upsert(db, "chapter", "rolled-back", "不应保留")
            db.rollback()
            assert tuple(db.iterdump()) == before
            checks += 1
    db.close()
    return {"queries_and_rollback_checks": checks, "seed": 20261023, "initial_documents": 96}


def markup_oracle():
    import test_rank_sources as fixtures
    rng = random.Random(23)
    cases = 0
    for title in ("龙令归位", "The Last Door", "甲&乙", "真假𠮷😀", "左 门 右"):
        for _ in range(20):
            parts = []
            for char in title:
                escaped = char.replace("&", "&amp;")
                for _ in range(rng.randrange(4)):
                    tag = rng.choice(("span", "b", "strong", "em", "i"))
                    escaped = f"<{tag}>{escaped}</{tag}>"
                parts.append(escaped)
            card = fixtures.card().replace("样本&amp;书名", "".join(parts))
            result = fixtures.sources.parse_qimao_page(fixtures.page(cards=card), 1)[0]
            assert result["title"] == title
            cases += 1
    return {"inline_wrapper_invariance_cases": cases, "seed": 23}


def rank_oracle():
    rank = load("feature_rank", "skills/story-skill/scripts/story_rank.py")
    research = load("feature_research", "skills/story-skill/scripts/story_research.py")
    snapshot = rank.template()
    base = snapshot["entries"][0]
    snapshot["entries"] = [{**base, "book_id": str(n), "book_url": f"https://example.invalid/book/{n}",
        "title": f"门后的第{n}本书", "rank": n + 1, "category": f"分类{n % 3}",
        "tags": [f"标签{n % 7}", f"标签{(n + 1) % 7}"],
        "word_count": (None, 0, 99999, 100000, 299999, 300000, 999999, 1000000)[n % 8]}
        for n in range(32)]
    baseline = rank.build_sample([snapshot], limit=20)
    rng = random.Random(25)
    for _ in range(20):
        rng.shuffle(snapshot["entries"])
        sample = rank.build_sample([snapshot, snapshot], limit=20)
        assert sample == baseline
        assert rank.validate_sample(sample) == sample
        decision = research.initialize(sample)
        assert research.validate_decision(decision) == decision
    return {"permutation_dedup_handoff_cases": 20, "seed": 25}


def distribution_contract():
    package = load("feature_package", "scripts/package.py")
    install = load("feature_install", "scripts/install.py")
    npm = load("feature_npm", "scripts/package_npm.py")
    version = package.current_version()
    expected = set(package.suite_files(version))
    assert set(install.suite_files(version)) == expected == set(npm.payload_files(version))
    assert package.skill_names(version) == install.skill_names(version) == npm.skill_names(version)
    entries = package.source_entries()
    assert set(name for name, _ in entries) == expected
    return {"version": version, "source_members": len(expected), "skill_entries": len(package.skill_names(version)),
            "contracts": ["standard source snapshot", "installer manifest", "npm payload manifest"]}


def main():
    results = {"search": search_oracle(), "markup": markup_oracle(),
               "rank": rank_oracle(), "distribution": distribution_contract()}
    sources = ["skills/story-skill/scripts/story_search.py", "skills/story-skill/scripts/story_rank.py",
               "skills/story-skill/scripts/story_rank_sources.py", "skills/story-skill/scripts/story_research.py",
               "scripts/package.py", "scripts/install.py", "scripts/package_npm.py"]
    results.update(ok=True, source_sha256={path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sources})
    (OUT / "probe-result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
