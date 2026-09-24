#!/usr/bin/env python3
"""Real CLI use on short Chinese scenes; no claim of a complete long novel."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/story-skill/scripts/story.py"
OUTPUT = ROOT / "benchmarks/results/v0.5.0/long-acceptance.json"

SCENES = {
    1: """# 第1章 蓝线钥匙

江棠把缠着蓝线的铜钥匙装进透明袋，递给杜承安。
“十一日，我来北库取钥匙。”她说。
杜承安把袋子塞进上衣内袋。
门口有人喊“老邱”，西装客人邱衡应了一声。江棠只知道他在北岸使用这个称呼。
杜承安说：“我猜账本被邱衡带到南岸了。”
江棠摇头：“我猜它还锁在北库。”她没有开柜，两个人的话都缺少实物证明。
""",
    10: """# 第10章 南岸听雨

关岚从搬运工邱朗手里接过带泥的绳索。伙计喊他“老邱”，他扭头应了。
“北岸也有个老邱。”关岚说。
“他们叫他们的。我没去过北库。”邱朗把手摊开。
关岚没有替两个“老邱”认关系。她只登记了本地工人的姓名，继续等十一日北岸开库后的消息。
""",
    11: """# 第11章 原袋

江棠回到北库。杜承安从上衣内袋里拿出透明袋，蓝线还缠在铜钥匙上。
“我答应十一日还你，没拆过袋。”他说。
江棠接过钥匙，当着他的面打开柜门。账本平放在第二层，封面的纸角向上翘着。
杜承安盯着账本，收回了“邱衡带走”的猜测。
江棠说：“现在只能确认账本在这里，不能凭这件事断定两个老邱是不是一伙。”
窗外传来关岚的电话。江棠只告诉她账本找到了，没有替邱朗作出保证。
""",
}
SOUTH_SCENES = {
    2: "关岚在船棚门口收到江棠托人送来的纸条，上面写着十一日核账。她把纸条夹进值班簿，只告诉工人等通知，没有说账本已经找到。",
    3: "雨从棚顶漏下来。关岚把值班簿移到高架，又拿空桶接水。邱朗问北岸有没有新消息，她翻开纸条看了一遍，回答仍然只有十一日这个日期。",
    4: "邱朗搬来一只旧木箱，提议用它垫高工具。关岚先让他把箱子翻空，亲眼看过里面没有票据，才把值班簿压在箱盖下。她不想把防雨做成另一笔糊涂账。",
    5: "棚里有人问老邱是不是那个穿西装的客人。关岚叫邱朗过来，当面登记了他的全名。至于北岸客人叫什么，她还没有亲自问过，便把那一栏空着。",
    6: "江棠打来电话，说北岸那位姓邱名衡。关岚把邱衡、邱朗分成两行记录，在前一个名字旁注明电话来源，没有因为两人都被叫作老邱就把工时合在一起。",
    7: "送绳索的人要走，关岚追到门口，请他在收条上补上数量。对方报了一个大概，她没替他凑整数，而是和邱朗一起数完，才把收条放回值班簿。",
    8: "邱朗来领工钱，关岚让他先看当天记下的工时。两人核对的是南岸搬运，北岸有没有另一笔账，她没有替任何人回答。签完后，她把钱和收条分别收好。",
    9: "关岚把明天要问的事列在纸条背面：账本有没有见到，柜子是谁打开的。邱朗笑她问得细，她说自己只能带着答复结账，不能带着别人的猜测结账。",
}
SCENES.update({chapter: f"# 第{chapter}章 南岸短景\n\n{text}\n" for chapter, text in SOUTH_SCENES.items()})


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class CLIError(RuntimeError):
    pass


class Session:
    def __init__(self, root, report):
        self.root, self.report = root, report
        self.revision = 0
        self.serial = 0
        self.plans = {}
        self.chapter_paths = {}

    def file(self, name, value):
        path = self.root / name if name == "创作约定.md" else self.root / ".acceptance-inputs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path.write_bytes(text.encode("utf-8"))
        self.report["inputs"][name] = {"sha256": sha(text), "content": value}
        return path

    def run(self, label, command, *args, payload=None, expect_revision=False, expected_error=None):
        self.serial += 1
        argv = [sys.executable, "-B", "-X", "utf8", str(TOOL), command, "--book", str(self.root),
                *map(str, args)]
        if payload is not None:
            path = self.file(f"{self.serial:03d}-{command}.json", payload)
            argv.extend(["--input", str(path)])
        if expect_revision:
            argv.extend(["--expect", str(self.revision)])
        started = time.perf_counter()
        process = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=120)
        record = {"label": label, "command": argv, "returncode": process.returncode,
                  "elapsed_seconds": round(time.perf_counter() - started, 6),
                  "stdout": process.stdout, "stderr": process.stderr}
        self.report["commands"].append(record)
        stream = process.stdout if process.returncode == 0 else process.stderr
        try:
            value = json.loads(stream)
        except (ValueError, TypeError):
            value = None
        if expected_error:
            record["expected_error"] = expected_error
            record["ok"] = process.returncode != 0 and isinstance(value, dict) and value.get("error") == expected_error
        else:
            record["ok"] = process.returncode == 0 and isinstance(value, dict)
        if not record["ok"]:
            raise CLIError(f"{label}: exit {process.returncode}; {stream.strip()}")
        if isinstance(value.get("revision"), int):
            self.revision = value["revision"]
        print(json.dumps({"step": label, "ok": True, "revision": self.revision}, ensure_ascii=False), flush=True)
        return value

    def check(self, label, condition, **observations):
        self.report["checks"].append({"label": label, "ok": bool(condition), **observations})
        if not condition:
            raise AssertionError(label)


def review(text, *, changes=None):
    quote = next(line for line in text.splitlines() if line and not line.startswith("#"))
    return {"draft_sha256": sha(text), "checks": {
        "causality": {"note": "本段围绕纸条、票据或实物核对作出具体选择，结论限于角色实际取得的信息。", "quote": quote},
        "continuity": {"note": "核对实际前文的物件颜色、保管人与两岸人物身份；猜测不当作确认。", "quote": quote},
        "constraints": {"note": "本次只验收已写出的精选短段，不把这些功能样例称为完整连载或文学质量验证。", "quote": quote},
        "style": {"note": "短段有具体交接动作与对白；不据此评价完整长篇的文学质量。", "quote": quote}}, "issues": []}


def chapter_plan(chapter):
    return {"volume_dir": "第一卷 两岸账本", "goal": "当面交接钥匙并标明猜测" if chapter == 1 else "核对钥匙并开柜确认账本",
            "stop": "留下两个未验证的猜测" if chapter == 1 else "账本找到，两个老邱的关系仍未知",
            "beats": [{"choice": "保留实物与信息来源", "change": "区分人物猜测和已见事实"}],
            "constraints": ["现实悬疑，不出现超自然；猜测必须标明"],
            "requires": ["key", "limits"], "tags": ["north"], "length": [80, 800],
            "line": "north-line", "entities": ["jiang", "du", "north", "key-item", "ledger"],
            "time": {"clock": "main", "start": chapter, "end": chapter}}


def native_commit(session, chapter, plan, text, summary, key_text=None, world_changes=None):
    session.run(f"保存第{chapter}章计划", "plan", "--chapter", chapter, payload=plan, expect_revision=True)
    session.plans[chapter] = plan
    context = session.run(f"读取第{chapter}章上下文", "context", "--chapter", chapter, "--budget-bytes", 16000)
    draft = session.file(f"chapter-{chapter}.md", text)
    prepared = session.run(f"准备第{chapter}章审查", "prepare", "--chapter", chapter, "--draft", draft)
    delta = prepared["delta"]
    delta.update(summary=summary, review=review(text), changes=[])
    if key_text is not None:
        delta["changes"] = [{"id": "key", "text": key_text,
                  "quote": next(line for line in text.splitlines() if "钥匙" in line and not line.startswith("#"))}]
    if world_changes:
        delta["world_changes"] = world_changes
    result = session.run(f"提交第{chapter}章", "commit", "--chapter", chapter, "--draft", draft, payload=delta)
    published = Path(result["path"])
    session.chapter_paths[chapter] = published
    session.check(f"第{chapter}章真实导出与审稿SHA一致", result.get("committed") and result.get("exports_complete")
                  and hashlib.sha256(published.read_bytes()).hexdigest() == sha(text), source_sha256=sha(text))
    return context


def evidence(chapter, quote, scenes=SCENES):
    if quote not in scenes[chapter]:
        raise ValueError("Acceptance evidence must come from the actual scene")
    return {"kind": "chapter", "chapter": chapter, "sha256": sha(scenes[chapter]), "quote": quote}


def initial_world():
    return {"entities": [{"id": cid, "name": name, "kind": kind, "description": description}
        for cid, name, kind, description in (
            ("jiang", "江棠", "character", "北线整理人，猜测有别于亲见事实"),
            ("du", "杜承安", "character", "北线钥匙保管人"),
            ("guan", "关岚", "character", "南线船棚主人"),
            ("qiu-north", "邱衡", "character", "北岸被称为老邱的客人"),
            ("qiu-south", "邱朗", "character", "南岸被称为老邱的搬运工"),
            ("north", "北库", "place", "钥匙归还与开柜现场"),
            ("south", "南岸船棚", "place", "独立的南线现场"),
            ("key-item", "铜钥匙", "item", "须回看原文核对颜色与保管人"),
            ("ledger", "账本", "item", "开柜前去向未核"))],
        "aliases": [{"alias": "老邱", "entity": "qiu-north", "scope": "north-line"},
                    {"alias": "老邱", "entity": "qiu-south", "scope": "south-line"}],
        "facts": [{"id": cid, "subject": "ledger", "predicate": "第一日位置的争议命题", "value": value,
                   "clock": "main", "start": 1, "end": None, "hard": False,
                   "evidence": {"kind": "author_plan", "note": "角色将讨论的猜测，尚未证实，不是世界真相"}}
                  for cid, value in (("claim-north", "第一日账本仍在北库"),
                                     ("claim-south", "第一日账本被邱衡带到南岸"))]}


def first_changes(scenes=SCENES):
    color = "红线" if "红线" in scenes[1] else "蓝线"
    return {"facts": [{"id": "key-description", "subject": "key-item", "predicate": "外观", "value": f"缠着{color}的铜钥匙",
                       "clock": "main", "start": 1, "end": None, "hard": True,
                       "evidence": evidence(1, f"江棠把缠着{color}的铜钥匙装进透明袋，递给杜承安。", scenes)}],
        "knowledge": [{"id": "jiang-guess-1", "actor": "jiang", "fact": "claim-north", "state": "suspects",
                       "clock": "main", "at": 1, "channel": "本人提出未开柜验证的猜测",
                       "evidence": evidence(1, "江棠摇头：“我猜它还锁在北库。”她没有开柜，两个人的话都缺少实物证明。", scenes)},
                      {"id": "du-guess-1", "actor": "du", "fact": "claim-south", "state": "suspects",
                       "clock": "main", "at": 1, "channel": "本人推测客人带走账本",
                       "evidence": evidence(1, "杜承安说：“我猜账本被邱衡带到南岸了。”", scenes)}],
        "lines": [{"id": "north-after-1", "line": "north-line", "clock": "main", "at": 1, "place": "north",
                   "summary": "江棠交出钥匙，北线仍未开柜验证两种猜测", "unfinished": "十一日归还钥匙并当面开柜",
                   "entities": ["jiang", "du"], "evidence": evidence(1, "“十一日，我来北库取钥匙。”她说。", scenes)}],
        "hooks": [{"id": "key-seeded-1", "hook": "key-return", "state": "seeded", "clock": "main", "at": 1,
                   "description": f"十一日归还{color}铜钥匙，并以同一实物开柜核验", "entities": ["jiang", "du"],
                   "trigger_line": "north-line", "hard_deadline": 11, "window_start": 11, "window_end": 13,
                   "evidence": evidence(1, "“十一日，我来北库取钥匙。”她说。", scenes)}]}


def late_changes(scenes=SCENES):
    opened = "江棠接过钥匙，当着他的面打开柜门。账本平放在第二层，封面的纸角向上翘着。"
    return {"facts": [{"id": "ledger-seen-11", "subject": "ledger", "predicate": "十一日实物位置",
                       "value": "北库柜内第二层", "clock": "main", "start": 11, "end": None, "hard": True,
                       "evidence": evidence(11, opened, scenes)}],
        "knowledge": [{"id": f"{actor}-seen-11", "actor": actor, "fact": "ledger-seen-11", "state": "knows",
                       "clock": "main", "at": 11, "channel": "当面开柜看到实物", "evidence": evidence(11, opened, scenes)}
                      for actor in ("jiang", "du")] +
                     [{"id": "du-retracted-11", "actor": "du", "fact": "claim-south", "state": "unknown",
                       "clock": "main", "at": 11, "channel": "撤回带走猜测，未确认过去十天的全部去向",
                       "evidence": evidence(11, "杜承安盯着账本，收回了“邱衡带走”的猜测。", scenes)}],
        "lines": [{"id": "north-after-11", "line": "north-line", "clock": "main", "at": 11, "place": "north",
                   "summary": "钥匙归还，账本在柜中找到", "unfinished": "两个老邱的关系仍待核",
                   "entities": ["jiang", "du"], "evidence": evidence(11, "江棠说：“现在只能确认账本在这里，不能凭这件事断定两个老邱是不是一伙。”", scenes)}],
        "hooks": [{"id": "key-fulfilled-11", "hook": "key-return", "state": "fulfilled", "clock": "main", "at": 11,
                   "description": "钥匙已归还并完成当面开柜", "entities": ["jiang", "du"], "trigger_line": "north-line",
                   "hard_deadline": 11, "window_start": 11, "window_end": 13, "evidence": evidence(11, opened, scenes)}]}


def acceptance(session):
    session.run("创建独立中文书", "init", "--title", "北库双线验证", "--kind", "long")
    session.file("创作约定.md", "这是一组原创中文短段的真实CLI功能验收，章号1—11各存一段实际短景，非11个达到平台字数的完整章节。adopt只能初始化fresh baseline，不用SQL在现有工程跳号。故事时间main按日计数，1至11为十日间隔。当前授权为完成此独立工程的CLI验证，不修改历史作品，不证明完整长篇质量或总token账单。\n")
    session.run("保存创作限制与物件卡", "notes", payload=[
        {"id": "limits", "kind": "contract", "text": "现实悬疑；猜测不能当作实证；未写章节不得声称已完成。",
         "source": "本次验收约定", "critical": True},
        {"id": "key", "kind": "fact", "text": "铜钥匙缠着蓝线；本轮核对交付、保管和归还。",
         "source": "作者初始物件设计", "tags": ["north"]}], expect_revision=True)
    session.run("建立人物身份、分线化名及待证命题", "world-save", payload=initial_world(), expect_revision=True)
    native_commit(session, 1, chapter_plan(1), SCENES[1], "江棠交出蓝线铜钥匙，约十一日取回；她与杜承安各持未核猜测。",
                  "杜承安保管透明袋中的蓝线铜钥匙，江棠约十一日取回。", first_changes())
    baseline = session.file("adopt-10.md", SCENES[10])
    session.run("确认已有工程不能靠adopt静默跳号", "adopt", "--chapter", 10, "--draft", baseline,
                "--summary", "尝试明示跳号基线，若工具拒绝就实际提交精选短景，不绕过工具。",
                expect_revision=True, expected_error="adopt_nonempty")
    session.report["workflow_observations"] = [{"kind": "documented_boundary",
        "observation": "adopt refuses a nonempty book. To retain chapter-1 evidence and return at chapter 11 without SQL, this acceptance actually commits nine short south-line scenes; it does not claim unwritten chapters."}]
    for chapter in range(2, 11):
        plan = {"volume_dir": "第一卷 两岸账本", "goal": "南岸核对材料并保留信息来源", "stop": "仍等十一日北岸核验的答复",
                "beats": [{"choice": "当面确认或保留未知", "change": "不把同名与转述写成已确认结论"}],
                "constraints": ["这是一段短景功能夹具，不冒称完整长篇章节"], "requires": ["limits"],
                "tags": ["south"], "length": [40, 800], "line": "south-line",
                "entities": ["guan", "south", "qiu-south"], "time": {"clock": "main", "start": chapter, "end": chapter}}
        quote = next(line for line in SCENES[chapter].splitlines() if line and not line.startswith("#"))
        changes = {"lines": [{"id": f"south-after-{chapter}", "line": "south-line", "clock": "main", "at": chapter,
                              "place": "south", "summary": quote, "unfinished": "等待十一日北岸开库的实际答复",
                              "entities": ["guan", "qiu-south"], "evidence": evidence(chapter, quote)}]}
        native_commit(session, chapter, plan, SCENES[chapter], "南线短景功能夹具：" + quote, world_changes=changes)
    late_plan = chapter_plan(11)
    late_plan["entities"].append("老邱")
    context = native_commit(session, 11, late_plan, SCENES[11], "北线归还钥匙并亲眼找到柜内账本；两名老邱的关系仍未知。",
                           "江棠取回蓝线铜钥匙并当面开柜。", late_changes())
    world = context["world"]
    session.check("隔十章回北线取第1章断点，不把第10章南线当北线现场", world["line"]["id"] == "north-after-1",
                  line=world["line"], global_previous_chapter=context["previous"]["chapter"])
    session.check("同一化名按北线解析为邱衡", "qiu-north" in [e["id"] for e in world["entities"]]
                  and "qiu-south" not in [e["id"] for e in world["entities"]], entities=world["entities"])
    knowledge = {(k["actor"], k["fact"], k["state"]) for k in world["knowledge"]}
    session.check("两人矛盾猜测分别保留，不升级为世界真相", {("jiang", "claim-north", "suspects"),
                  ("du", "claim-south", "suspects")}.issubset(knowledge), knowledge=world["knowledge"],
                  propositions=world["propositions"])
    session.check("晚章取回有第1章原文SHA的物件铺垫", any(f["id"] == "key-description" for f in world["facts"])
                  and any(h["hook"] == "key-return" for h in world["hooks"]), facts=world["facts"], hooks=world["hooks"])
    session.report["related_packet"] = {"budget": context["budget"], "chapter": 11,
                                        "world_json_utf8_bytes": len(json.dumps(world, ensure_ascii=False).encode("utf-8")),
                                        "scope": "Only selected entities and north-line state; short scene acceptance, not token billing"}
    checked = session.run("检验已提交双线与伏笔状态", "world-check", "--chapter", 11)
    session.check("领域检查无确定性阻断", not checked.get("blockers"), result=checked)
    recalled = session.run("检索早期物件的精确原文", "recall", "--query", "蓝线")
    session.check("检索包含第1章与第11章正文", {1, 11}.issubset({item.get("chapter") for item in recalled["matches"]}),
                  result=recalled)
    historical_branch(session)


def historical_branch(session):
    # Historical dependency review uses the current cards actually read here,
    # not invented snapshots of what those cards contained before each chapter.
    required = {cid for plan in session.plans.values() for cid in plan["requires"]}
    review_plan = chapter_plan(max(SCENES) + 1)
    review_plan["requires"] = sorted(required)
    session.run("保存历史复核所需的下一章取材计划", "plan", "--chapter", max(SCENES) + 1,
                payload=review_plan, expect_revision=True)
    context = session.run("读取历史复核实际使用的现态卡片", "context", "--chapter", max(SCENES) + 1,
                          "--budget-bytes", 16000)
    cards = {card["id"]: card for card in context["required_cards"]}
    session.check("历史复核已读卡片覆盖每章明确requires", required <= cards.keys(),
                  required=sorted(required), read_cards=sorted(cards))
    card_dependencies = {cid: {"kind": "card", "ref": cid,
        "sha": sha(json.dumps(cards[cid], ensure_ascii=False, sort_keys=True, separators=(",", ":")))}
        for cid in sorted(required)}
    session.report["history_card_review"] = {
        "revision": context["revision"], "cards": {cid: cards[cid] for cid in sorted(required)},
        "dependencies": list(card_dependencies.values()),
        "scope": "Current card values and sources rechecked against all eleven scenes for historical review; not pre-chapter card snapshots."}
    reviewed_dependencies = {}
    for chapter in sorted(SCENES):
        dependency = 1 if chapter == 11 else chapter - 1 if 3 <= chapter <= 10 else None
        dependencies = [dict(card_dependencies[cid]) for cid in session.plans[chapter]["requires"]]
        if dependency is not None:
            dependencies.append({"kind": "chapter", "ref": dependency, "sha": sha(SCENES[dependency])})
        reviewed_dependencies[chapter] = dependencies
        session.run(f"记录第{chapter}章实际复核的依赖", "history-deps", payload={
            "chapter": chapter, "chapter_sha": sha(SCENES[chapter]), "dependencies": dependencies, "complete": True,
            "note": "逐段复核全文和刚读取的现态卡片及来源：每章保留plan.requires中的限制卡，北线另核对钥匙卡；北线末段依赖首次交接，南线依赖自己的前一段。卡片SHA是本次历史复核现态，不冒称旧章起草快照。"}, expect_revision=True)
    session.run("保存修订前状态快照", "history-snapshot", "--label", "十一段短景的修订前状态", expect_revision=True)
    started = session.run("创建钥匙颜色修正的历史分支", "history-start", "--chapter", 1,
                          "--label", "把蓝线改成红线，同步晚章与领域证据", expect_revision=True)
    session.report["history_started"] = started
    branch = started.get("branch", started.get("branch_id"))
    if isinstance(branch, dict):
        branch = branch.get("id")
    if not isinstance(branch, str):
        raise CLIError("history-start did not expose a usable branch identifier")
    inspection = session.run("读取候选范围与状态修订要求", "history-inspect", "--branch", branch)
    session.report["history_initial_inspection"] = inspection
    affected = [item["chapter"] for item in inspection["affected"]]
    session.check("历史依赖将早期钥匙和十章后的再现一起纳入审查", {1, 11}.issubset(affected), affected=affected)
    revised = {chapter: text.replace("蓝线", "红线") if chapter in (1, 11) else text
               for chapter, text in SCENES.items()}
    candidates = []
    for chapter in affected:
        dependencies = [{**item, "sha": sha(revised[int(item["ref"])])} if item["kind"] == "chapter"
                        else dict(item) for item in reviewed_dependencies[chapter]]
        candidates.append({"chapter": chapter, "text": revised[chapter],
                           "summary": "修订颜色后的北线短景：" + next(line for line in revised[chapter].splitlines()
                                                                         if line and not line.startswith("#")),
                           "dependencies": dependencies, "complete": True})
    templates = inspection.get("state_review_template", [])
    if {item["id"] for item in templates} != set(inspection["required_state_ids"]):
        raise CLIError("State decisions are incomplete in the inspect response; request the missing template page")
    state_changes = []
    for template in templates:
        if template["id"] != "key":
            raise CLIError("Unexpected affected card requires a new semantic decision")
        after = json.loads(json.dumps(template["before"], ensure_ascii=False).replace("蓝线", "红线"))
        state_changes.append({"id": template["id"], "before_sha": template["before_sha"], "after": after,
                              "chapter": 11, "quote": revised[11].splitlines()[2],
                              "note": "最终仍由江棠取回并开柜，仅把现卡片和来源引文中的颜色与两章候选同步；不回放旧交付覆盖晚章归还结果。"})
    world_changes = {}
    for changes in (first_changes(revised), late_changes(revised)):
        for kind, records in changes.items():
            world_changes.setdefault(kind, []).extend(records)
    session.run("保存两章候选、最终卡片决策及全部受影响领域证据", "history-update", "--branch", branch,
                payload={"chapters": candidates, "state_changes": state_changes, "world_changes": world_changes}, expect_revision=True)
    session.check("候选尚未发布时原版正文仍有效", all(
        hashlib.sha256(session.chapter_paths[chapter].read_bytes()).hexdigest() == sha(SCENES[chapter])
        for chapter in affected))
    for candidate in candidates:
        details = session.run(f"读取第{candidate['chapter']}章前后对照与审查模板", "history-inspect",
                              "--branch", branch, "--chapter", candidate["chapter"])
        template = details["chapter_review_template"]
        candidate["review"] = {**review(candidate["text"]), "candidate_sha256": template["candidate_sha256"]}
        session.check(f"第{candidate['chapter']}章审查绑定工具返回的真实候选", template["draft_sha256"] == sha(candidate["text"]))
    session.run("保存逐章对读后的四项审查", "history-update", "--branch", branch,
                payload={"chapters": candidates}, expect_revision=True)
    inspection = session.run("读取最新整包审查指纹", "history-inspect", "--branch", branch)
    semantic = {**inspection["review_template"], "note": "逐段对读第1、11章原版与候选，唯一情节设计调整是钥匙缠线由蓝变红。",
                "state_review": "江棠最终取回钥匙、账本已找到及两个老邱关系未知均保留；key现卡片只改颜色与来源，11项领域证据重绑定新版本。",
                "coverage_review": "已复核全部11段。南线2—10不含钥匙颜色依赖，原稿保持；北线1与11及其人物认知、断点、伏笔均随新SHA更新。",
                "issues": []}
    session.run("保存整个历史分支的真实覆盖与状态审查", "history-update", "--branch", branch,
                payload={"semantic_review": semantic}, expect_revision=True)
    published = session.run("发布已审查历史分支", "history-publish", "--branch", branch, expect_revision=True)
    session.report["history_publication"] = published
    session.check("历史发布完成导出", published.get("exports_complete") is True, result=published)
    for chapter in affected:
        paths = [session.root / relative for relative in published["exported"]
                 if Path(relative).name.startswith(f"第{chapter}章 ")]
        if paths:
            session.check(f"第{chapter}章只有一个发布路径", len(paths) == 1)
            session.chapter_paths[chapter] = paths[0]
    session.check("两处钥匙同步为红线且九段南线原字节不变", all(
        hashlib.sha256(session.chapter_paths[chapter].read_bytes()).hexdigest() == sha(text)
        for chapter, text in revised.items()))
    backup_hashes = [hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in published.get("backups", [])]
    session.check("发布保留两章被替换的原始字节", {sha(SCENES[1]), sha(SCENES[11])}.issubset(backup_hashes),
                  backup_sha256=backup_hashes)
    next_plan = chapter_plan(12)
    session.run("保存发布后的下一章计划", "plan", "--chapter", 12, payload=next_plan, expect_revision=True)
    following = session.run("读取历史修订后的相关资料", "context", "--chapter", 12, "--budget-bytes", 16000)
    keys = [card for card in following["required_cards"] if card["id"] == "key"]
    session.check("历史修订保留晚章归还状态并更正颜色", len(keys) == 1 and "江棠取回红线" in keys[0]["text"], key=keys)
    session.check("领域对象重新绑定修订后原文", any(f["id"] == "key-description" and "红线" in f["value"]
                  and f["evidence"]["sha"] == sha(revised[1]) for f in following["world"]["facts"]),
                  facts=following["world"]["facts"])
    old_search = session.run("检查已撤回颜色不再作为当前正文命中", "recall", "--query", "蓝线")
    session.check("旧词索引失效而非保留过时正文", not old_search["matches"] and old_search.get("no_match_confirmed") is True)
    session.run("复核分支发布后的领域一致性", "world-check", "--chapter", 12)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = {"schema": 1, "date": datetime.now(timezone.utc).isoformat(), "ok": False,
              "scope": "Eleven original short Chinese scenes operated through real CLI calls, with north-line return ten chapter numbers later. These are short functional fixtures, not eleven full-length literary chapters. Nonempty adopt refusal is recorded; no SQL bypass, reused test fixtures, complete-long-novel or total-token-bill claim.",
              "inputs": {}, "commands": [], "checks": [],
              "runtime_files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(TOOL.parent.glob("*.py"))}}
    with tempfile.TemporaryDirectory(prefix="story-long-acceptance-") as name:
        root = Path(name) / "北库双线验证"
        session = Session(root, report)
        try:
            acceptance(session)
            report["ok"] = True
        except (Exception, KeyboardInterrupt) as error:
            report["error"] = {"type": type(error).__name__, "message": str(error)}
    report["temporary_data_removed"] = not root.exists()
    current = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(TOOL.parent.glob("*.py"))}
    report["runtime_stable"] = current == report["runtime_files"]
    report["ok"] = report["ok"] and report["runtime_stable"] and report["temporary_data_removed"]
    spec = importlib.util.spec_from_file_location("long_acceptance_writer", ROOT / "scripts/verify.py")
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    output = writer.write_report(report, args.output)
    print(json.dumps({"ok": report["ok"], "report": str(output)}, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
