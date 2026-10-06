#!/usr/bin/env python3
"""Prepare isolated synthetic tasks; never run the writer or certify its quality."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[2]
CASES = Path(__file__).resolve().parent / "cases"
LOCAL_RUNS = ROOT / ".local-writing-samples" / "skill-execution"
VOLUME = "第一卷 雨夜"
SEED_LENGTH_QUOTE = "合成夹具的既有第1、2章正文各60—240字，不计首行章名。"
NEW_LENGTH_QUOTE = "后续新写的完整章及第3章候选正文各300—500字，不计首行章名。"

# These are deliberately tiny synthetic transaction fixtures, not a test novel
# or a claim of literary review. Each state change cites text present below.
SEEDS = [
    {
        "chapter": 1, "title": "交出钥匙",
        "text": "第1章 交出钥匙\n\n沈禾站在旧账房门前，把父亲留下的编号念给许伯听。她只想核对那一笔，不问整本账的来历。\n\n许伯说：\n\n“账本不能离房。若要进去，把小柜的钥匙交给我，我陪你看。”\n\n沈禾看了看身后渐亮的天，又摸了摸衣袋。沈禾把唯一的钥匙交给许伯。\n\n许伯收好钥匙，推门让她进去。她失去独自开柜的办法，终于有了查阅的机会。\n",
        "quote": "沈禾把唯一的钥匙交给许伯。",
        "goal": "沈禾以交钥匙为代价取得陪同查阅机会",
        "choice": "同意交钥匙并接受许伯陪同",
        "change": "获准进入账房，钥匙转由许伯保管",
        "stop": "刚进入账房，尚未查到账目",
        "summary": "沈禾交出唯一钥匙，许伯允许陪同入房，账本不得离房。",
    },
    {
        "chapter": 2, "title": "柜前一行",
        "text": "第2章 柜前一行\n\n许伯用沈禾交来的钥匙打开小柜，只把账本摊在柜前，自己守在旁边。\n\n沈禾找到父亲留下的编号。那一行只有日期和一个模糊的数目，远不够解释父亲为何失踪。她伸手想将账本挪到窗边，许伯按住书角。\n\n“不能离开这间房。天就快亮了，你想办法看清。”\n\n沈禾收回手，望向许伯脚边的油灯。她决定先求一张纸，抄下这一行再逐字核对。许伯仍握着那把钥匙。\n",
        "quote": "许伯仍握着那把钥匙。",
        "goal": "找到指定编号并面对不能外借的查阅限制",
        "choice": "按编号翻查，放弃将账本移出房间",
        "change": "发现尚未看清的一行，决定请求抄录",
        "stop": "想到纸和油灯，尚未获准抄录",
        "summary": "沈禾找到编号但未看清，准备请求纸灯抄录；账本留在房内，许伯仍持钥匙。",
    },
]


def write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def no_links(path):
    """Check every existing component before resolving or creating anything."""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    for entry in reversed((path, *path.parents)):
        try:
            mode = entry.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(f"Symbolic links are not permitted: {entry}")
    return path


def is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def output_path(requested, case_id):
    if requested:
        run = no_links(requested)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run = no_links(LOCAL_RUNS / f"{case_id}-{stamp}-{uuid.uuid4().hex[:10]}")
    if run.exists():
        raise ValueError(f"Output must be a new directory; refusing existing path: {run}")
    if is_within(run, ROOT) and not is_within(run, LOCAL_RUNS):
        raise ValueError("Inside this repository, use only .local-writing-samples/skill-execution/")
    if is_within(ROOT, run):
        raise ValueError("Output cannot contain the source repository")
    if run == LOCAL_RUNS:
        raise ValueError("Output must be a new run below the local samples directory")
    return run


def fixture_path(workspace, relative):
    parsed = PurePosixPath(relative)
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or parsed.is_absolute() or any(p in (".", "..") for p in relative.split("/"))):
        raise ValueError(f"Unsafe fixture path: {relative!r}")
    target = no_links(workspace.joinpath(*parsed.parts))
    if not is_within(target, workspace):
        raise ValueError(f"Fixture escapes workspace: {relative}")
    return target


def copy_skills(destination):
    source = no_links(ROOT / "skills")
    for directory, names, files in os.walk(source, followlinks=False):
        names[:] = [name for name in names if name != "__pycache__"]
        for name in [*names, *files]:
            entry = Path(directory) / name
            if entry.is_symlink():
                raise ValueError(f"Skill snapshot contains a symbolic link: {entry}")
            if entry.is_file() and entry.stat().st_nlink > 1:
                raise ValueError(f"Skill snapshot contains a hard-linked file: {entry}")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def load_runtime(skills):
    runtime_path = skills / "story-skill" / "scripts" / "story.py"
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("execution_fixture_story", runtime_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, runtime_path


def plan_for(chapter, title, goal, choice, change, stop):
    return {
        "title": title, "volume_dir": VOLUME,
        "goal": goal, "stop": stop,
        "beats": [{"choice": choice, "change": change}],
        "constraints": ["账本不能离开账房", "没有法术或新的钥匙", "不揭晓父亲失踪的全部真相"],
        "requires": ["key", "room_rule"], "tags": ["沈禾", "许伯"],
        "length": [60, 240] if chapter < 3 else [300, 500],
        "count_method": "visible_nonspace_v2", "count_title": False,
        "length_exception": {
            "source": "book_agreement", "path": "创作约定.md",
            "quote": SEED_LENGTH_QUOTE if chapter < 3 else NEW_LENGTH_QUOTE,
        },
    }


def bind_plan(story, book, chapter, plan):
    book.save_plan(chapter, plan, book.meta("revision"))
    relative = f"01_大纲细纲/第{chapter}章 {plan['title']}.md"
    target = fixture_path(book.root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = (f"# 第{chapter}章 {plan['title']}细纲\n状态：已采用\n\n"
            "合成任务夹具的已采用计划，不代表真实作品或文学评阅。\n\n"
            f"目标：{plan['goal']}\n尝试：{plan['beats'][0]['choice']}\n"
            f"结果：{plan['beats'][0]['change']}\n停笔点：{plan['stop']}\n"
            f"篇幅：正文{plan['length'][0]}—{plan['length'][1]}字，不计首行章名。\n"
            + "".join(f"约束：{item}\n" for item in plan["constraints"]))
    with target.open("x", encoding="utf-8") as stream:
        stream.write(text)
    return story.outline.bind(book, chapter, relative, book.meta("revision"), sha256(target))


def managed_fixture(workspace, skills, configuration):
    if configuration != {"profile": "keys-two-chapters"}:
        raise ValueError(f"Unknown managed fixture configuration: {configuration!r}")
    story, runtime_path = load_runtime(skills)
    agreement = ("# 创作约定\n\n本书为隔离执行验收的合成练习。\n"
                 + SEED_LENGTH_QUOTE + "\n" + NEW_LENGTH_QUOTE
                 + "\n既有章的局部修订不自动扩写；本轮用户的明确例外按其范围执行。\n"
                   "第三人称固定跟随沈禾；沿用已发生事实，不以候选替代正式前文。\n"
                   "所有正式章节使用第一卷 雨夜。每轮只完成用户授权范围。\n")
    (workspace / "创作约定.md").write_text(agreement, encoding="utf-8")
    story.Book.create(workspace, "雨夜查账（合成夹具）", "long")
    book = story.Book(workspace)
    receipts = []
    try:
        book.save_notes([
            {"id": "key", "kind": "character", "text": "沈禾持有父亲留下的唯一钥匙，可以开启账房内小柜。", "source": "合成夹具初始设定", "critical": True},
            {"id": "room_rule", "kind": "contract", "text": "许伯保管账房，账本不能离房；没有法术或额外钥匙。", "source": "合成夹具初始设定", "critical": True},
        ], book.meta("revision"))
        for seed in SEEDS:
            chapter = seed["chapter"]
            plan = plan_for(chapter, seed["title"], seed["goal"], seed["choice"], seed["change"], seed["stop"])
            bind_plan(story, book, chapter, plan)
            draft = fixture_path(workspace, f".story/drafts/seed-{chapter}.md")
            draft.parent.mkdir(parents=True, exist_ok=True)
            with draft.open("x", encoding="utf-8") as stream:
                stream.write(seed["text"])
            packet = book.prepare(chapter, draft)
            if not packet["lint"]["ok"]:
                raise ValueError(f"Synthetic fixture lint failed: {packet['lint']}")
            delta = packet["delta"]
            delta["summary"] = seed["summary"]
            delta["changes"] = [{"id": "key", "text": "许伯持有沈禾交出的唯一钥匙，沈禾不能独自开柜。", "quote": seed["quote"]}]
            notes = {
                "causality": "合成夹具准备核对：交出钥匙换入房，第二章由持钥匙者开柜；只记录本文明写事实。",
                "continuity": "合成夹具准备核对：钥匙先由沈禾交给许伯，后仍由许伯持有，账本未离房。",
                "constraints": "合成夹具准备核对：沿用明示的极短章幅例外，未添加法术、额外钥匙或揭晓全部真相。",
                "style": "合成夹具准备核对：文字仅用于执行和状态测试，不声称完成独立文学评阅。",
            }
            delta["review"]["checks"] = {
                key: {"note": notes.get(key, "合成夹具准备核对：仅据所附原句登记状态，不作文学质量认证。"), "quote": seed["quote"]}
                for key in story.CHECKS
            }
            delta["review"]["issues"] = []
            receipt = book.commit(chapter, draft, delta)
            if not receipt["committed"] or not receipt["exports_complete"]:
                raise ValueError(f"Synthetic fixture commit or export incomplete: {receipt}")
            receipts.append(receipt)
        plan = plan_for(
            3, "灯下抄录", "在不能带走账本的条件下准确抄得指定一行",
            "沈禾请求纸和灯；许伯要求先念清原行并在抄完后逐字核对，她接受条件",
            "许伯递纸移灯，沈禾完成抄录并请他核对；拿到可带走的一行抄件",
            "抄件核对完成；沈禾仍在账房，账本留柜前，钥匙仍在许伯手中，不写离房或后续调查",
        )
        bind_plan(story, book, 3, plan)
    finally:
        book.close()
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, str(runtime_path), "status", "--book", str(workspace)],
                            check=True, capture_output=True, text=True, encoding="utf-8", env=environment)
    status_result = json.loads(result.stdout)
    if status_result.get("last_chapter") != 2 or status_result.get("next_chapter") != 3:
        raise ValueError(f"Unexpected synthetic fixture status: {status_result}")
    return {"synthetic": True, "literary_review": False, "creation": "Book API and outline binding API; no direct SQLite writes",
            "commits": receipts, "cli_status": status_result}


def prepare(case_id, requested_output):
    case_path = no_links(CASES / f"{case_id}.json")
    case = json.loads(case_path.read_text(encoding="utf-8"))
    if case.get("id") != case_id:
        raise ValueError("Case ID does not match its filename")
    run = output_path(requested_output, case_id)
    evaluator = no_links(ROOT / "scripts" / "execution_eval.py")
    if not evaluator.is_file():
        raise ValueError("scripts/execution_eval.py is required to capture the baseline")
    run.parent.mkdir(parents=True, exist_ok=True)
    no_links(run.parent)
    run.mkdir()
    workspace = run / "workspace"
    workspace.mkdir()
    skills = run / "skills"
    copy_skills(skills)
    write_json(run / "case.json", case)
    seed_receipt = None
    if case.get("managed_fixture"):
        seed_receipt = managed_fixture(workspace, skills, case["managed_fixture"])
    for relative, text in case.get("fixtures", {}).items():
        target = fixture_path(workspace, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(text)
    task = f"工作目录：{workspace}\n\n{case['prompt']}\n"
    invocation = case["invocation"]
    if invocation.startswith("provided_skill_path:"):
        skill_name = invocation.split(":", 1)[1]
        if not skill_name or "/" in skill_name or "\\" in skill_name or skill_name in (".", ".."):
            raise ValueError("Invalid skill invocation name")
        skill_path = skills / skill_name / "SKILL.md"
        if not skill_path.is_file():
            raise ValueError(f"Invoked skill is missing from snapshot: {skill_path}")
        task += f"\n本任务使用这份技能及其同目录套件：{skill_path}\n"
    elif invocation != "natural_language":
        raise ValueError(f"Unsupported invocation mode: {invocation}")
    with (run / "task.txt").open("x", encoding="utf-8") as stream:
        stream.write(task)
    receipt = {"case_id": case_id, "synthetic": True, "prepared_at": datetime.now(timezone.utc).isoformat(),
               "run": str(run), "workspace": str(workspace), "skills_root": str(skills),
               "case_sha256": sha256(case_path), "task_sha256": sha256(run / "task.txt"),
               "generator_input": "task.txt plus workspace fixtures and explicitly supplied skills only; review criteria remain outside the task",
               "seed": seed_receipt, "task_execution": "not_started", "quality_assessed": False}
    write_json(run / "fixture.json", receipt)
    result = subprocess.run([sys.executable, str(evaluator), "start", "--case-file", str(run / "case.json"),
                             "--workspace", str(workspace), "--skills-root", str(skills),
                             "--output", str(run / "baseline.json")],
                            check=True, capture_output=True, text=True, encoding="utf-8",
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if not (run / "baseline.json").is_file():
        raise ValueError(f"Baseline command returned without creating baseline.json: {result.stdout}")
    return {"prepared": True, "case_id": case_id, "run": str(run), "workspace": str(workspace),
            "task": str(run / "task.txt"), "baseline": str(run / "baseline.json"),
            "fixture": str(run / "fixture.json"), "task_execution": "not_started", "quality_assessed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=sorted(path.stem for path in CASES.glob("*.json")))
    parser.add_argument("--output", help="New run directory; inside this repository only the ignored local samples directory is allowed")
    args = parser.parse_args()
    try:
        result = prepare(args.case, args.output)
    except Exception as error:
        print(json.dumps({"prepared": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
