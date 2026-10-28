"""Compare equivalent inline HTML with a plain-text parsing oracle."""
from pathlib import Path
import hashlib
import html
import json
import random
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import test_rank_sources as fixtures


def inline(value, rng):
    output = []
    for character in value:
        text = html.escape(character)
        for _ in range(rng.randint(0, 3)):
            tag = rng.choice(("span", "b", "em", "strong"))
            text = f"<{tag}>{text}</{tag}>"
        output.append(text)
        if rng.randrange(4) == 0:
            output.append("<!-- hidden comment --><script>ignored</script><style>ignored</style>")
    return "".join(output)


if __name__ == "__main__":
    rng = random.Random(20261023)
    cases = 0
    for index in range(160):
        title = rng.choice(("样本&书名", "The Last Door", "门后·秘密", "甲 乙<丙>", "春𠮷é"))
        author = rng.choice(("陈小明", "笔名&A", "Alice Li", "未明昼"))
        category = rng.choice(("东方玄幻", "都市生活"))
        plain = fixtures.card(author=html.escape(author), category=html.escape(category))
        plain = plain.replace("样本&amp;书名", html.escape(title))
        marked = fixtures.card(author=inline(author, rng), category=inline(category, rng))
        marked = marked.replace("样本&amp;书名", inline(title, rng))
        expected = fixtures.sources.parse_qimao_page(fixtures.page(cards=plain), 1)
        actual = fixtures.sources.parse_qimao_page(fixtures.page(cards=marked, sort=inline("按点击量", rng)), 1)
        assert actual == expected, (index, actual, expected)
        cases += 1
    source = ROOT / "skills/story-skill/scripts/story_rank_sources.py"
    print(json.dumps({"ok": True, "cases": cases,
                      "scope": "Equivalent inline markup versus independently constructed plain HTML; offline fixtures",
                      "source_sha256": {str(source.relative_to(ROOT)): hashlib.sha256(source.read_bytes()).hexdigest()}},
                     ensure_ascii=False, indent=2))
