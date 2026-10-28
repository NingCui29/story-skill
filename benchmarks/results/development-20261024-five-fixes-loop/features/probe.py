"""Fresh static HTML invariants and distribution contracts; no network/install."""
from pathlib import Path
import hashlib, importlib.util, json, random, sys
ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'tests'))
import test_rank_sources as f
import test_rank as ranks
import test_research as research
rng=random.Random(20261024)
rows=[]
for n in range(240):
 title=('龙令归位','The Last Door','甲&乙','真假𠮷😀','左 门 右')[n%5]
 pieces=[]
 for ch in title:
  value=ch.replace('&','&amp;')
  for _ in range(rng.randrange(4)):
   tag=rng.choice(['span','strong','em','i'])
   value=f'<{tag}>{value}</{tag}>'
  hidden=rng.choice(['<template><b>旧名</b></template>','<span hidden>旧名</span>','<span hidden="false">旧名</span>','<script>旧名</script>','<style>旧名</style>'])
  pieces.append(hidden+value)
 card=f.card('100').replace('样本&amp;书名',''.join(pieces))
 cards='<template>'+f.card('900')+'</template>'+card+'<div hidden>'+f.card('901')+'</div>'
 html=f.page(cards=cards)
 entries=f.sources.parse_qimao_page(html,1)
 assert len(entries)==1 and entries[0]['title']==title and entries[0]['rank']==1
 rows.append({'title':title,'book_ids':[x['book_id'] for x in entries]})
negative=0
for wrapper in ('<template>{}</template>','<div hidden>{}</div>','<div hidden="false">{}</div>','<div hidden="until-found">{}</div>'):
 for fragment in ('<a class="tab-inner active">按点击量</a>','<a class="page-btn num active">1</a>',f.card()):
  html=f.page().replace(fragment,wrapper.format(fragment))
  try:f.sources.parse_qimao_page(html,1)
  except ValueError:negative+=1
  else:raise AssertionError('inert evidence accepted')
def load(name,path):
 spec=importlib.util.spec_from_file_location(name,ROOT/path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
p=load('distribution_package','scripts/package.py');i=load('distribution_install','scripts/install.py');n=load('distribution_npm','scripts/package_npm.py')
v=p.current_version();members=set(p.suite_files(v))
assert members==set(i.suite_files(v))==set(n.payload_files(v))
assert p.skill_names(v)==i.skill_names(v)==n.skill_names(v)
entries=dict(p.source_entries());target='skills/story-skill/scripts/story_rank_sources.py'
# package entries use the repository's suite-relative layout.
assert any(data==(ROOT/target).read_bytes() for data in entries.values())
result={'ok':True,'seed':20261024,'inline_and_inert_invariance_cases':len(rows),'inert_only_evidence_rejection_cases':negative,'distribution':{'version':v,'members':len(members),'skill_entries':len(p.skill_names(v)),'modified_source_present_byte_exact':True},'source_sha256':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in [target,'skills/story-skill/scripts/story_rank.py','skills/story-skill/scripts/story_research.py','scripts/package.py','scripts/install.py','scripts/package_npm.py']}}
(OUT/'probe-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
