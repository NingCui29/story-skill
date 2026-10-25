"""Appearance changes preserve editing state and carry across loopback ports."""
import base64
import hashlib
import json
import re
import shutil
import subprocess
import unittest

import test_workbench as base


HARNESS = r"""
const assert=require('assert'),vm=require('vm');
function page({saved=null,jar={value:''},dark=false,known=true,storageFails=false,cookieFails=false,legacyMedia=false,protocol='http:',hostname='127.0.0.1'}={}){
 const attrs=known?{'data-story-workbench':''}:{},listeners={},mediaListeners=[];
 const root={hasAttribute:key=>key in attrs,setAttribute:(key,value)=>attrs[key]=value};
 const control={value:'system',listeners:{},addEventListener(key,fn){this.listeners[key]=fn;}};
 const text={scrollTop:123,selectionStart:17,selectionEnd:29};
 Object.defineProperty(text,'value',{get(){throw Error('Theme read unsaved buffer');},set(){throw Error('Theme replaced unsaved buffer');}});
 const body={children:[control,text],scrollTop:456},focused=text;
 const document={documentElement:root,body,activeElement:focused,getElementById(id){if(id==='workbench-theme')return control;throw Error('Theme accessed editor control: '+id);},
  querySelector(){throw Error('Theme inspected document content');},createElement(){throw Error('Theme recreated DOM');}};
 Object.defineProperty(document,'cookie',{get(){if(cookieFails)throw Error('Cookies disabled');return jar.value;},set(value){if(cookieFails)throw Error('Cookies disabled');jar.value=value.split(';',1)[0];}});
 const values=new Map(saved===null?[]:[['story-workbench-theme',saved]]),writes=[];
 const localStorage={getItem(key){if(storageFails)throw Error('Storage disabled');return values.get(key)||null;},setItem(key,value){if(storageFails)throw Error('Storage disabled');writes.push([key,value]);values.set(key,value);}};
 const media={matches:dark};
 if(legacyMedia)media.addListener=fn=>mediaListeners.push(fn);else media.addEventListener=(_,fn)=>mediaListeners.push(fn);
 const window={matchMedia:query=>{assert.equal(query,'(prefers-color-scheme: dark)');return media;},addEventListener:(event,fn)=>(listeners[event]??=[]).push(fn)};
 const context={window,document,location:{protocol,hostname},localStorage};
 for(const field of ['docs','active','recovering','recoveryKey'])Object.defineProperty(context,field,{get(){throw Error('Theme read protected state '+field);},set(){throw Error('Theme wrote protected state '+field);}});
 vm.runInNewContext(theme,context,{timeout:1000});
 return {attrs,control,root,document,body,text,focused,jar,values,writes,listeners,
  choose(value){control.value=value;control.listeners.change();},
  system(value){media.matches=value;mediaListeners.forEach(fn=>fn({matches:value}));},
  event(name,event={}){(listeners[name]||[]).forEach(fn=>fn(event));}};
}
"""


class WorkbenchThemeTests(unittest.TestCase):
    def js(self, test):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js required')
        source = 'const theme=' + json.dumps(base.story.workbench._workbench_theme_script()) + ';\n'
        result = subprocess.run([node, '-'], input=source + HARNESS + '\n' + test, encoding='utf-8',
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_launch_follows_system_and_updates_when_system_changes(self):
        self.js("const p=page({dark:true});assert.equal(p.attrs['data-theme'],'dark');assert.equal(p.control.value,'system');p.system(false);assert.equal(p.attrs['data-theme'],'light');assert.equal(p.writes.length,0);assert.equal(p.jar.value,'');")

    def test_switching_preserves_nodes_focus_scroll_selection_and_unsaved_buffers(self):
        self.js(r"""
const p=page(),children=p.body.children.slice();
for(const value of ['dark','light','system']){p.choose(value);assert.equal(p.attrs['data-theme-preference'],value);assert.strictEqual(p.document.activeElement,p.focused);assert.strictEqual(p.body.children[0],children[0]);assert.strictEqual(p.body.children[1],children[1]);assert.equal(p.body.scrollTop,456);assert.equal(p.text.scrollTop,123);assert.equal(p.text.selectionStart,17);assert.equal(p.text.selectionEnd,29);}
assert.deepEqual(p.writes.map(([key])=>key),Array(3).fill('story-workbench-theme'));
assert.equal(p.jar.value,'story_workbench_theme=system');
""")

    def test_explicit_choice_survives_new_port_and_overrides_stale_local_storage(self):
        self.js(r"""
const jar={value:''},shelf=page({jar,dark:false});shelf.choose('dark');
const editor=page({jar,saved:'light',dark:false});assert.equal(editor.attrs['data-theme'],'dark');assert.equal(editor.control.value,'dark');
editor.system(false);assert.equal(editor.attrs['data-theme'],'dark');editor.choose('light');
shelf.event('focus');assert.equal(shelf.attrs['data-theme'],'light');assert.equal(shelf.control.value,'light');
editor.choose('system');const reopened=page({jar,saved:'dark',dark:true});assert.equal(reopened.control.value,'system');assert.equal(reopened.attrs['data-theme'],'dark');reopened.system(false);assert.equal(reopened.attrs['data-theme'],'light');
""")

    def test_invalid_preferences_and_storage_events_do_not_change_other_state(self):
        self.js(r"""
const p=page({saved:'<script>invalid</script>',jar:{value:'unrelated=dark; story_workbench_theme=invalid'},dark:true});assert.equal(p.control.value,'system');assert.equal(p.attrs['data-theme'],'dark');
p.choose('bogus');assert.equal(p.control.value,'system');assert.equal(p.writes.length,0);
p.jar.value='story_workbench_theme=light';p.event('storage',{key:'editor-buffer'});assert.equal(p.attrs['data-theme'],'dark');p.event('storage',{key:'story-workbench-theme'});assert.equal(p.attrs['data-theme'],'light');
p.jar.value='story_workbench_theme=dark';p.event('pageshow');assert.equal(p.attrs['data-theme'],'dark');
""")

    def test_storage_denial_and_legacy_media_listener_still_allow_switching(self):
        self.js("const p=page({storageFails:true,cookieFails:true,legacyMedia:true,dark:true});assert.equal(p.attrs['data-theme'],'dark');p.choose('light');p.system(true);p.event('focus');assert.equal(p.attrs['data-theme'],'light');p.choose('system');p.system(false);assert.equal(p.attrs['data-theme'],'light');")

    def test_exported_file_uses_local_preference_without_loopback_cookie(self):
        self.js("const jar={value:'story_workbench_theme=light'},p=page({protocol:'file:',hostname:'',saved:'dark',jar});assert.equal(p.attrs['data-theme'],'dark');p.choose('system');assert.equal(jar.value,'story_workbench_theme=light');assert.equal(p.values.get('story-workbench-theme'),'system');")

    def test_unmarked_page_is_untouched(self):
        self.js("const p=page({known:false,saved:'dark'});assert.deepEqual(p.attrs,{});assert.deepEqual(Object.keys(p.listeners),[]);assert.deepEqual(Object.keys(p.control.listeners),[]);assert.equal(p.writes.length,0);")

    def test_all_workbench_views_include_one_control_and_valid_hashed_script(self):
        fixture = base.WorkbenchTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        workbench = base.story.workbench
        packet = workbench.snapshot(fixture.book)
        reading = workbench._reading_text(fixture.book, packet)
        pages = {'shelf': workbench._library_page('test').decode(),
                 'editor': workbench._editor_page(packet, {}, 'test').decode(),
                 'overview': workbench.render_html(packet),
                 'reader': workbench.render_html(packet, reading)}
        node = shutil.which('node')
        for view, page in pages.items():
            with self.subTest(view=view):
                self.assertIn('data-story-workbench data-workbench-view="' + view + '"', page)
                self.assertEqual(page.count('id="workbench-theme"'), 1)
                for value in ('system', 'light', 'dark'):
                    self.assertIn('<option value="' + value + '">', page)
                self.assertIn('@media(prefers-color-scheme:dark)', page)
                scripts = re.findall(r'<script>(.*?)</script>', page, re.S)
                self.assertEqual(len(scripts), 1)
                if view != 'shelf':
                    digest = base64.b64encode(hashlib.sha256(scripts[0].encode()).digest()).decode()
                    self.assertIn("script-src 'sha256-" + digest + "'", page)
                if node:
                    result = subprocess.run([node, '-e', "new (require('vm').Script)(require('fs').readFileSync(0,'utf8'));"],
                                            input=scripts[0], capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_dark_palette_covers_reading_editing_navigation_and_semantic_states(self):
        css = base.story.workbench._workbench_theme_css()
        for marker in ('.manuscript', '.desk>main', '.tools', '.menu-panel', 'textarea',
                       '#formatted code', '.comparison pre', '.diff-line.added',
                       '.diff-line.removed', '#match-text mark', '#history-note',
                       '#search', '#full-query', '#book-select', '#chapter-version',
                       '#pending-box', '.book-status', '.genre-tag', '.identity-badge',
                       '.selected', '.book>button:disabled'):
            self.assertIn(marker, css)
        # Readability of the actual dark-palette text colors, including metadata.
        def luminance(color):
            channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
            linear = [part / 12.92 if part <= .04045 else ((part + .055) / 1.055) ** 2.4 for part in channels]
            return sum(part * weight for part, weight in zip(linear, (.2126, .7152, .0722)))
        dark = css.split('[data-theme="dark"]{', 1)[1].split('}', 1)[0]
        colors = dict(re.findall(r'(--[a-z-]+):(#[0-9a-f]{6})', dark))
        pairs = (('--ink', '--surface'), ('--muted', '--surface'), ('--muted', '--wb-panel'),
                 ('--wb-selected-ink', '--wb-selected'), ('--wb-warning', '--wb-warning-bg'),
                 ('--wb-success', '--wb-success-bg'), ('--wb-danger', '--wb-danger-bg'))
        for foreground, background in pairs:
            brighter, darker = sorted((luminance(colors[foreground]), luminance(colors[background])), reverse=True)
            self.assertGreaterEqual((brighter + .05) / (darker + .05), 4.5, (foreground, background))


if __name__ == '__main__':
    unittest.main()
