"""Explicit adopted-outline setup for synthetic runtime tests.

Calling this helper is test preparation, not a claim of literary review. It never
wraps a runtime operation or silently refreshes an existing binding.
"""

import hashlib


def bind_adopted_outline(story, book, chapter, *, path=None, content=None):
    if story.outline.binding_for(book, chapter) is not None:
        raise AssertionError("Fixture chapter is already bound; review rebinding explicitly")
    plan = book.get_plan(chapter)
    relative = path or f"01_大纲细纲/第{chapter}章 测试细纲.md"
    target = book.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if content is None:
        content = (f"# 第{chapter}章 合成测试细纲\n状态：已采用\n\n"
                   "此文件仅用于状态机测试，不代表真实作品的采用或语义审查。\n\n"
                   f"本章目标：{plan['goal']}\n停笔点：{plan['stop']}\n")
        content += "".join(f"尝试：{beat['choice']}；结果：{beat['change']}\n"
                           for beat in plan["beats"])
        content += "".join(f"约束：{constraint}\n" for constraint in plan["constraints"])
    target.write_text(content, encoding="utf-8")
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    return story.outline.bind(book, chapter, relative, book.meta("revision"), sha)
