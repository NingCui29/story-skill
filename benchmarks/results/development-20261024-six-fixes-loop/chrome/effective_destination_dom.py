"""Run the production serialized action snapshot in isolated local Chrome."""
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
SOURCE = ROOT / "skills/story-skill/scripts/story_chrome_bridge.mjs"
NODE = "/Users/cuining/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
code = subprocess.check_output([NODE, "--input-type=module", "-e",
    "import {readActionState} from " + json.dumps(SOURCE.as_uri()) +
    ";process.stdout.write(readActionState.toString());"], text=True)
cases = [
    ("path", "next", "https://example.test/draft/", "https://example.test/publish/", True),
    ("query", "?mode=draft", "https://example.test/draft/", "https://example.test/publish/", True),
    ("empty", "", "https://example.test/draft/", "https://example.test/publish/", True),
    ("fragment", "#save", "https://example.test/draft/", "https://example.test/publish/", True),
    ("root", "/draft/save", "https://example.test/draft/", "https://example.test/publish/", False),
    ("absolute", "https://example.test/draft/save", "https://example.test/draft/", "https://other.test/", False),
    ("protocol-relative", "//example.test/draft/save", "https://example.test/draft/", "https://other.test/", False),
    ("no-link", None, "https://example.test/draft/", "https://other.test/", False),
    ("stable-base", "next", "https://example.test/draft/", "https://example.test/draft/", False),
    ("unicode", "下一步?草稿=是", "https://example.test/draft/", "https://example.test/publish/", True),
    ("encoded", "%E8%8D%89%E7%A8%BF", "https://example.test/draft/", "https://example.test/publish/", True),
    ("base-host", "/save", "https://example.test/", "https://other.test/", True),
    ("nbsp-path", "\u00a0#save", "https://example.test/edit/99", "https://example.test/modify/99", True),
    ("ideographic-space-path", "\u3000#save", "https://example.test/edit/99", "https://example.test/modify/99", True),
    ("ascii-fragment", " \t\r\n#save ", "https://example.test/edit/99", "https://example.test/modify/99", True),
    ("ascii-control-fragment", "\x00#save", "https://example.test/edit/99", "https://example.test/modify/99", True),
]
script = "const snapshot=(" + code + "); const cases=" + json.dumps(cases) + ";" + r"""
const results=[];
for(const [name,href,beforeBase,afterBase,changed] of cases){
  const base=document.createElement('base');base.href=beforeBase;document.head.append(base);
  const el=document.createElement('a');el.textContent='存草稿';if(href!==null)el.setAttribute('href',href);document.body.append(el);
  const before=snapshot([el]);base.href=afterBase;const after=snapshot([el]);
  let clickedHref;el.addEventListener('click',event=>{event.preventDefault();clickedHref=event.currentTarget.href;});el.click();
  const pass=before.href===after.href&&(before.resolvedHref!==after.resolvedHref)===changed&&
    (href===null?after.resolvedHref===null:after.resolvedHref===clickedHref);
  results.push({name,pass,before,after,clickedHref});el.remove();base.remove();
}
// Without BASE the DOM falls back to the document URL. No page/network visit.
const el=document.createElement('a');el.href='?local=1';document.body.append(el);
results.push({name:'document-default-base',pass:snapshot([el]).resolvedHref===el.href});
el.setAttribute('href','http://[');
results.push({name:'invalid-link',pass:snapshot([el]).resolvedHref===null});
const out=document.createElement('pre');out.id='result';out.textContent=JSON.stringify(results);document.body.replaceChildren(out);
"""
with tempfile.TemporaryDirectory(prefix="story-link-dom-") as folder:
    local = Path(folder) / "test.html"
    local.write_text("<!doctype html><html><head><meta charset='utf-8'></head><body><script>" + script + "</script></body></html>")
    command = [CHROME, "--headless=new", "--disable-gpu", "--disable-background-networking", "--disable-component-update",
        "--disable-sync", "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--metrics-recording-only",
        "--host-resolver-rules=MAP * 0.0.0.0", "--user-data-dir=" + folder + "/profile", "--dump-dom", "--virtual-time-budget=1000", local.as_uri()]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    packet = re.search(r'<pre id="result">(.*?)</pre>', result.stdout, re.S)
    if packet is None:
        raise RuntimeError(result.stderr[-2000:] + result.stdout[-2000:])
    rows = json.loads(html.unescape(packet.group(1)))
report = {"source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "checks": len(rows),
    "passed": sum(row["pass"] for row in rows), "results": rows, "scope": "Production serialized callback in local isolated Chrome; clicks prevented from navigating; no platform/account."}
(HERE / "effective-destination-dom.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key:value for key,value in report.items() if key != "results"}, ensure_ascii=False))
assert report["checks"] == report["passed"]
