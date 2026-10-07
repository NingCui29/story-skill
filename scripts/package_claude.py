#!/usr/bin/env python3
"""Build one self-contained Story Skill ZIP for Claude Desktop skill import."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("story_suite_package", ROOT / "scripts/package.py")
suite = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(suite)
BUNDLE_REVISION = 2

ENTRY = """---
name: story-skill
description: 中文小说创作与维护；用于开书、书名与简介、大纲、正文续写、拆文分析、审稿修改、榜单与素材调研、封面及离线投稿材料。按本次需求选择内嵌流程，不用于软件开发或普通文案。
---

# Story Skill · Claude 桌面版

这是一个可导入的技能，内含八个按需读取的流程及共享工具。用户以自然语言提出任务，或明确指定规划、写作、审稿等流程；内嵌的 `GUIDE.md` 是本技能的流程资源，不需要另行安装或假定有八个独立桌面命令。整个包只有本文件作为 `SKILL.md` 入口。

使用前读取 [共享规则与流程路由](suite/story-skill/GUIDE.md)，按本次任务进入其中一个对应流程；同版已读内容复用。内嵌文件的相对路径以其自身所在目录为基准，八个目录保留同级关系。共享工具位于 `suite/story-skill/scripts/story.py`，先从本技能实际位置转为绝对路径，再使用可用的 Python 3.10+ 执行。

先根据当前会话判断可访问的文件和可用工具。普通聊天的代码执行环境与用户电脑分开，上传稿件后在当前可写目录处理；桌面任务能访问已连接文件夹时，只在实际可访问且获授权的书目录操作。用户电脑上的路径不能直接当作执行环境路径，文件夹可读写也不证明能在用户电脑上运行 Python。

已有托管作品须取得完整书目录及 `.story/` 状态，先按共享规则核对正式断点；只有正文或摘要时不得冒称恢复了原账本。不能访问既有状态时，先处理授权的独立稿件或规划；只有用户授权建立新基线时才初始化。需要跨会话继续托管作品时，交付可下载的成稿和含 `.story/` 的完整书目录备份，说明下一次须提供该备份；会话摘要不能代替书库。不要向用户索取密码或其他账号凭据。

默认通过可下载文件交付桌面成稿。只有实际能在用户电脑运行工具且已验证页面可达时才交付本机工作台地址；隔离环境中的 `127.0.0.1` 不代表用户电脑已启动工作台。联网调研和图像生成按当前会话实际工具执行，封面缺少图片工具时按封面流程说明具体缺口。投稿流程仍仅准备离线材料，不声称已上传、审核通过或发布。
"""


def package(output=None):
    original = suite.source_entries()
    files = dict(original)
    version = suite.source_version(files["story-skill/scripts/story.py"])
    entry_mapping = {name: "story-skill/suite/" + name.removesuffix("SKILL.md") + "GUIDE.md"
                     for name in files if name.endswith("/SKILL.md")}
    # Desktop import checks the entire ZIP. Embedded entries are ordinary guides;
    # only their Markdown paths and descriptions change, never runtime/config bytes.
    embedded = [(entry_mapping.get(name, "story-skill/suite/" + name),
                 raw.replace(b"SKILL.md", b"GUIDE.md") if name.endswith(".md") else raw)
                for name, raw in original]
    entries = sorted([
        ("story-skill/SKILL.md", ENTRY.encode("utf-8")),
        ("story-skill/LICENSE", files["story-skill/LICENSE"]),
        *embedded,
    ])
    skill_entry_count = sum(Path(name).name.casefold() == "skill.md" for name, _ in entries)
    if skill_entry_count != 1:
        raise ValueError("Claude Desktop bundle must contain exactly one SKILL.md")
    output = (Path(output).expanduser() if output is not None else
              ROOT / f"dist/story-skill-claude-desktop-{version}-r{BUNDLE_REVISION}.zip").absolute()
    if output.suffix.lower() != ".zip":
        raise ValueError("Claude Desktop output must be a .zip file")
    # This artifact has a different layout from the canonical eight-skill ZIP.
    if re.fullmatch(r"story-skill-[0-9]+\.[0-9]+\.[0-9]+\.zip", output.name, re.IGNORECASE):
        raise ValueError("Desktop bundle must not replace the canonical suite archive")
    if output.resolve().is_relative_to(Path(suite.SOURCE).resolve()):
        raise ValueError("Desktop bundle output must be outside the source directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".story-skill-package-", suffix=".zip", dir=output.parent)
    stage = Path(name)
    try:
        os.close(fd)
        with zipfile.ZipFile(stage, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for entry_name, raw in entries:
                info = zipfile.ZipInfo(entry_name, date_time=(2026, 9, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, raw)
        suite.validate_archive(stage, entries)
        archive_bytes = stage.read_bytes()
        result = {"archive": str(output), "version": version, "files": len(entries),
                  "source_files": len(original), "skill_name": "story-skill",
                  "bundle_revision": BUNDLE_REVISION, "skill_entry_count": skill_entry_count,
                  "entry_path_mapping": entry_mapping,
                  "path_transformation": "Embedded SKILL.md files become GUIDE.md; embedded Markdown "
                                         "references and path descriptions use GUIDE.md. Non-Markdown source bytes are unchanged.",
                  "sha256": hashlib.sha256(archive_bytes).hexdigest(), "bytes": len(archive_bytes)}
        os.replace(stage, output)
        return result
    finally:
        if stage.exists():
            stage.unlink()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="ZIP path; defaults to dist/story-skill-claude-desktop-<version>-r2.zip")
    args = parser.parse_args()
    print(json.dumps(package(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
