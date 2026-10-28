from pathlib import Path
import importlib.util, hashlib, json, sys, zipfile
ROOT=Path('/Users/cuining/Documents/GitHub/story-skill')
OUT=Path(__file__).resolve().parent
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
def load(name):
    spec=importlib.util.spec_from_file_location('audit_'+name, ROOT/'scripts'/(name+'.py'))
    mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod);return mod
pkg,claude,codex,installer,npm=map(load,('package','package_claude','package_codex','install','package_npm'))
entries=dict(pkg.source_entries());version=pkg.source_version(entries['story-skill/scripts/story.py'])
standard=pkg.package(OUT/f'story-skill-{version}.zip')
claude_result=claude.package(OUT/'claude.zip')
codex_result=codex.package(OUT/'codex-final')
results=[]
for typ,result,translate in [('standard',standard,lambda k:k),('claude',claude_result,lambda k:'story-skill/suite/'+(k.removesuffix('SKILL.md')+'GUIDE.md' if k.endswith('/SKILL.md') else k)),('codex',codex_result,lambda k:'plugins/story-skill/skills/'+k)]:
    with zipfile.ZipFile(result['archive']) as z:
        names=set(z.namelist());verified=[]
        for key,raw in entries.items():
            expected=raw.replace(b'SKILL.md',b'GUIDE.md') if typ=='claude' and key.endswith('.md') else raw
            if typ=='codex' and key.endswith('/agents/openai.yaml'):
                skill=key.split('/')[0]
                expected=raw.replace(('$'+skill+' ').encode(),('$story-skill:'+skill+' ').encode())
            target=translate(key)
            assert target in names,(typ,target)
            assert z.read(target)==expected,(typ,target)
            verified.append(key)
        assert z.testzip() is None
        for code in ('story_chrome_bridge.mjs','story_platform_browser.py','story_workbench.py','story_workbench_archives.py'):
            key='story-skill/scripts/'+code
            assert z.read(translate(key))==entries[key]
        results.append({'kind':typ,'archive':result['archive'],'members':len(names),'source_members_verified':len(verified),'latest_four_runtime_modules_exact':True})
assert set(installer.suite_files(version))==set(pkg.suite_files(version))==set(npm.payload_files(version))
assert installer.skill_names(version)==pkg.skill_names(version)==npm.skill_names(version)
assert len(installer.skill_names(version))==8
print(json.dumps({'version':version,'source_members':len(entries),'artifacts':results,'manifest_sets_equal':True,'host_scope_mappings':len(installer.HOST_DIRECTORIES)+len(installer.USER_HOST_DIRECTORIES),'no_installation':True},ensure_ascii=False,indent=2))
