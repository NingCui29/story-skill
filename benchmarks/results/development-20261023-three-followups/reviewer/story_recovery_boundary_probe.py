from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys
sys.path.insert(0, '/Users/cuining/Documents/GitHub/story-skill/tests')
import test_workbench as base
story=base.story
w=story.workbench
archives=story.workbench_archives
old=set(w._material_sessions)
try:
    with TemporaryDirectory(prefix='recovery-boundary-probe-') as temp:
        root=Path(temp).resolve()/'书'
        story.Book.create(root,'分页边界','long')
        draft=root/'.story/drafts/workbench'
        (draft/'自动恢复').mkdir(parents=True)
        for n in range(7):
            (draft/f'ordinary-{n}.md').write_text('ordinary',encoding='utf-8')
        expected=set()
        for n in range(6):
            path=draft/'自动恢复'/f'回稿-{n}.md'
            path.write_text('待核对原文',encoding='utf-8')
            expected.add('file:'+path.relative_to(root).as_posix())
        pending=draft/'有待核对.md.pending.json'
        pending.write_text('{}',encoding='utf-8')
        expected.add('pending:'+pending.relative_to(root).as_posix())
        ids=[]; cursor=None;offset=0;empty_partial=0
        with patch.object(w,'AUTHOR_LIMIT',2):
            for index in range(80):
                p=archives.recovery_catalog(root,cursor=cursor,offset=offset)
                ids.extend(r['id'] for r in p['recoveries'])
                if not p['recoveries'] and p['has_more']: empty_partial+=1
                assert p['next_offset']==offset+len(p['recoveries'])
                cursor=p['cursor'];offset=p['next_offset']
                if index==0:
                    try:w._material_catalog(root,cursor=cursor)
                    except story.StoryError as e:assert e.code=='invalid_input'
                    else:raise AssertionError('cross-mode cursor accepted')
                if not p['has_more']:break
            else:raise AssertionError('pagination did not terminate')
        assert p['complete'] is True,p
        assert len(ids)==len(set(ids))==len(expected),(ids,expected)
        assert set(ids)==expected,(ids,expected)
        assert empty_partial>0
        print({'pages':index+1,'empty_partial_pages':empty_partial,'returned':len(ids),'complete':p['complete'],'cross_mode':'rejected; recovery cursor retained'})
finally:
    for token in set(w._material_sessions)-old:w._material_discard(token)
