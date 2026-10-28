"""Local Chromium HTML parsing oracle; isolated profile and no network."""
from pathlib import Path
import hashlib
import html
import json
import random
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
import test_rank_sources as f

cases = []
def add(label, cards, reject=False):
    cases.append({"label": label, "html": f.page(cards=cards), "reject": reject})
for closing in ("</ul>", "</body>", "</html>", "</ul></body></html>"):
    add("template ancestor " + closing, "<template>" + closing + f.card("900") + "</template>" + f.card("100"))
add("nested templates", "<template><template></body>" + f.card("900") + "</template>" + f.card("901") + "</template>" + f.card("100"))
for tag in ("template", "div hidden", 'span hidden="false"', "script", "style"):
    add("inert selfclose " + tag, "<" + tag + "/>" + f.card("900") + "</" + tag.split()[0] + ">" + f.card("100"), True)
for decoration in ("<svg hidden/>", "<math hidden/>", "<svg><path hidden/><circle/></svg>",
                   "<math><mspace hidden/><mi>x</mi></math>",
                   "<svg><foreignObject><svg><path hidden/></svg></foreignObject></svg>", "<img hidden/><br/>"):
    add("legal foreign/void " + decoration, decoration + f.card("100"))
for prefix, suffix in (("<svg><foreignObject>", "</foreignObject></svg>"), ("<svg><desc>", "</desc></svg>"),
                       ("<math><mtext>", "</mtext></math>"),
                       ('<math><annotation-xml encoding="text/html">', "</annotation-xml></math>"), ("<svg>", "</svg>")):
    add("HTML integration " + prefix, prefix + "<div hidden/>" + f.card("900") + "</div>" + suffix + f.card("100"), True)

rng = random.Random(2026102406)
for n in range(160):
    title = ("门与灯", "真假𠮷😀", "甲&乙", "The Last Door")[n % 4]
    pieces = []
    for char in title:
        value = html.escape(char)
        for _ in range(rng.randrange(4)):
            tag = rng.choice(["span", "strong", "em", "i"])
            value = f"<{tag}>{value}</{tag}>"
        inert = rng.choice(["<template></a></span></li></ul>旧版</template>",
                           '<span hidden="false">旧版</span>', "<template><template></body>旧版</template></template>"])
        pieces.append(value + inert)
    add("random inline " + str(n), f.card("100").replace("样本&amp;书名", "".join(pieces)))

payload = json.dumps(cases, ensure_ascii=False).replace("</", "<\\/")
script = """
const cases = PAYLOAD;
const result = cases.map(c => {
  const doc = new DOMParser().parseFromString(c.html, 'text/html');
  const cards = Array.from(doc.querySelectorAll('li.qm-cover-text-item')).filter(n => !n.closest('[hidden]'));
  function text(n) {
    if (n.nodeType === Node.TEXT_NODE) return n.data;
    if (n.nodeType !== Node.ELEMENT_NODE || n.matches('template,script,style,[hidden]')) return '';
    return Array.from(n.childNodes, text).join('');
  }
  return {label: c.label, books: cards.map(n => {
    const a = n.querySelector('.s-tit a');
    return {id: a.getAttribute('href').match(/shuku\\/(\\d+)/)[1], title: text(a).trim().replace(/\\s+/g,' ')};
  })};
});
const out = document.createElement('pre'); out.id = 'result'; out.textContent = JSON.stringify(result); document.body.append(out);
""".replace("PAYLOAD", payload)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    path = root / "oracle.html"
    path.write_text("<!doctype html><meta charset=utf-8><body><script>" + script + "</script>", encoding="utf-8")
    command = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--headless=new", "--disable-gpu",
               "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run",
               "--no-default-browser-check", "--disable-extensions", "--host-resolver-rules=MAP * 0.0.0.0",
               "--user-data-dir=" + str(root / "profile"), "--dump-dom", "--virtual-time-budget=1000", path.as_uri()]
    process = subprocess.run(command, text=True, capture_output=True, timeout=45)
    assert process.returncode == 0, process.stderr[-2000:]
    match = re.search(r'<pre id="result">(.*?)</pre>', process.stdout, re.S)
    assert match, process.stdout[-2000:]
    observed = json.loads(html.unescape(match[1]))
rows = []
for case, actual in zip(cases, observed, strict=True):
    if case["reject"]:
        assert [book["id"] for book in actual["books"]] == ["100"], actual
        try:
            f.sources.parse_qimao_page(case["html"], 1)
        except ValueError as error:
            assert "重新导出" in str(error), error
        else:
            raise AssertionError("ambiguous inert selfclose accepted: " + case["label"])
        outcome = "rejected ambiguous HTML; browser only exposes book 100"
    else:
        parsed = [{"id": entry["book_id"], "title": entry["title"]} for entry in f.sources.parse_qimao_page(case["html"], 1)]
        assert parsed == actual["books"], {"case": case["label"], "parser": parsed, "chromium": actual["books"]}
        outcome = "matches Chromium DOM"
    rows.append({"label": case["label"], "outcome": outcome})
summary = {"ok": True, "checks": len(rows), "random_inline_checks": 160, "seed": 2026102406,
           "source_sha256": hashlib.sha256((ROOT / "skills/story-skill/scripts/story_rank_sources.py").read_bytes()).hexdigest(),
           "boundaries": "Isolated local Chromium DOM parsing; no live site/account, CSS visibility or JavaScript page effects validated",
           "rows": rows}
(OUT / "dom-probe-result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, ensure_ascii=False))
