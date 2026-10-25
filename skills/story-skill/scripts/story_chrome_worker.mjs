/* Run inside one real Codex Node REPL turn. Only the installed Chrome SDK's
 * public browser API reaches Chrome; local files/CLI carry the private queue.
 * Repeated checkpoints retain this module and every consumed request. */
import {constants} from 'node:fs';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {execFile as execFileCallback} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
import {platform,userInfo,constants as osConstants} from 'node:os';

const execFile = promisify(execFileCallback);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const WORKERS = new Map();
const MAX_PACKET = 16 * 1024 * 1024;
const PLATFORM = platform();
const OWNER_UID = PLATFORM === 'win32' ? null : userInfo().uid;
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const BOOTSTRAP_CANCELLED = Symbol('initial Chrome wait cancelled');
const own = value => value && typeof value === 'object' && !Array.isArray(value);
const hex = (value, length) => typeof value === 'string' && new RegExp('^[0-9a-f]{'+length+'}$').test(value);
const error = code => { const failure = new Error(code); failure.code = code; return failure; };
const requireValue = (value, code = 'platform_chrome_worker_invalid_config') => { if (!value) throw error(code); };
const ERROR_NAMES = new Set(['Error','ReferenceError','TypeError','RangeError','SyntaxError','AggregateError','URIError','EvalError','AbortError','TimeoutError']);
const ERROR_MARKERS = new Set(['none','origin_permission_required','browser_unattached','missing_turn_metadata','missing_browser_service','missing_runtime_global']);
const DRIVER_PHASES = new Set(['guard','catalog','listing','pages','all_rows','read']);
const DRIVER_WORKER_CODES = new Set([
  'unsupported_sdk','invalid_scroll','unavailable','ambiguous_tab','unsafe_user_tab',
  'browser_changed','existing_tab_required','target_tab_changed','target_tab_closed','invalid_config','binding_mismatch'
].map(code => 'platform_chrome_worker_'+code));
const DRIVER_ERRORS = new Set([
  'ambiguous_dom','ambiguous_draft','chapter_exists_not_draft','editor_changed','identity_mismatch',
  'identity_unavailable','incomplete_dom','incomplete_list','incomplete_scope','invalid_chapter',
  'invalid_identity','invalid_profile','invalid_remote_id','login_required','navigation_timeout','read_timeout',
  'not_draft','not_new_draft','not_open','operation_failed','save_unverified','target_unverified',
  'unnumbered_draft','unsafe_create','unsafe_navigation','unsafe_save','unsafe_url','unsupported_editor',
  'bridge_scope','bridge_expired','editor_in_use','readback_required'
].map(code => 'platform_browser_'+code));
const CLI_DIAGNOSTICS = Symbol('bounded CLI diagnostics');
const CLI_CHROME_ERRORS = new Set([
  'platform_chrome_invalid_handle','platform_chrome_invalid_profile','platform_chrome_unsafe_file',
  'platform_chrome_invalid_message','platform_chrome_message_limit','platform_chrome_worker_unavailable',
  'platform_chrome_worker_busy','platform_chrome_session_limit','platform_chrome_invalid_health',
  'platform_chrome_health_expired','platform_chrome_worker_changed','platform_chrome_already_claimed',
  'platform_chrome_request_expired','platform_chrome_already_responded','platform_chrome_request_timeout'
]);
const CLI_KNOWN_ERRORS = new Set([
  'io_or_input_error','book_missing','schema_mismatch','invalid_input',...CLI_CHROME_ERRORS,
  'platform_draft_plan_unusable','platform_draft_source_changed','platform_draft_profile_mismatch',
  'platform_draft_invalid_profile_path','platform_draft_invalid_profile_file','platform_draft_profile_changed',
  'platform_draft_book_changed','platform_draft_busy'
]);
const CLI_ERRNOS = ['EPERM','EACCES','EROFS','ENOENT','ENOTDIR','ENOSPC','EIO','EMFILE','ENFILE','ETIMEDOUT'];
const cliFailure = (failure, completedExitCode = null) => {
  let packet;
  if (typeof failure?.stderr === 'string' && Buffer.byteLength(failure.stderr) <= MAX_PACKET) {
    try { const value = JSON.parse(failure.stderr); if (own(value)) packet = value; } catch {}
  }
  const known = CLI_KNOWN_ERRORS.has(packet?.error) ? packet.error : 'other';
  let errno = CLI_ERRNOS.find(name => failure?.code === name || failure?.errno === name) || 'none';
  // Python's OSError has a fixed numeric prefix. Never infer a permission
  // from prose, paths or SQLite messages, and never retain the original text.
  if (errno === 'none' && known === 'io_or_input_error' && typeof packet?.message === 'string') {
    const match = /^\[Errno ([1-9][0-9]{0,2})\](?: |$)/.exec(packet.message);
    if (match) errno = CLI_ERRNOS.find(name => osConstants.errno[name] === Number(match[1])) || 'none';
  }
  const code = Number.isSafeInteger(failure?.code) && failure.code >= 0 && failure.code <= 255 ? failure.code : completedExitCode;
  const result = error(CLI_CHROME_ERRORS.has(known) ? known : 'platform_chrome_worker_cli_failed');
  Object.defineProperty(result,CLI_DIAGNOSTICS,{value:Object.freeze({cli_exit_code:code,cli_known_error:known,cli_errno:errno})});
  return result;
};
const permissionDenialPhrases = ['The user declined permission','could not acquire authority to trigger network egress',
  'origin approval gate did not grant access'];
const deniedByCode = failure => typeof failure?.code === 'string' &&
  /auth|permission|denied|forbid|cancel/i.test(failure.code);
const permissionDenied = failure => deniedByCode(failure) ||
  permissionDenialPhrases.some(phrase => typeof failure?.message === 'string' && failure.message.includes(phrase));
const originPermissionDenied = message => {
  if (!permissionDenialPhrases.some(phrase => message.includes(phrase))) return false;
  const urls = message.match(/(?<![A-Za-z0-9_:+/-])https?:\/\/[^\s"'`<>\)\]}]+/g) || [];
  // SDK refusals can omit the URL. Keep their fixed refusal diagnostic;
  // a URL naming another origin is never attributed to the bound origin.
  if (!urls.length) return true;
  return urls.some(value => {
    try { const url = new URL(value); return url.origin === 'https://fanqienovel.com' && !url.username && !url.password; }
    catch { return false; }
  });
};
const errorMarker = failure => {
  const message = typeof failure?.message === 'string' ? failure.message : '';
  if (deniedByCode(failure) || originPermissionDenied(message)) return 'origin_permission_required';
  if (message.includes('Debugger unattached')) return 'browser_unattached';
  if (/missing required codex turn metadata/i.test(message)) return 'missing_turn_metadata';
  if (message.includes('Browser use requires a trusted Node REPL browser service')) return 'missing_browser_service';
  if (/\b(?:process|Buffer|TextDecoder|URL|setTimeout|setInterval) is not defined\b/.test(message)) return 'missing_runtime_global';
  return 'none';
};
const cleanError = failure => {
  const marker = errorMarker(failure);
  if (marker === 'origin_permission_required') return 'platform_chrome_worker_origin_permission_required';
  if (marker === 'browser_unattached') return 'platform_chrome_worker_browser_unattached';
  if (marker === 'missing_turn_metadata') return 'platform_chrome_worker_turn_metadata_missing';
  if (marker === 'missing_browser_service') return 'platform_chrome_worker_runtime_unavailable';
  return /^platform_(?:chrome|browser)_[a-z_]+$/.test(failure?.code || '') ? failure.code : 'platform_chrome_worker_failed';
};
const absolute = value => typeof value === 'string' && value.length > 0 && path.isAbsolute(value) && path.resolve(value) === value;
const fanqieManagementURL = (url,target) => {
  if (typeof url !== 'string' || /[\\\u0000-\u0020\u007f]/.test(url) || target?.platform !== 'fanqie' || !/^[0-9]{1,200}$/.test(target.remote_book_id)) return false;
  try {
    const parsed = new URL(url),prefix = '/main/writer/chapter-manage/'+target.remote_book_id+'&';
    const encodedTitle = parsed.pathname.slice(prefix.length),title = decodeURIComponent(encodedTitle);
    return url.startsWith('https://fanqienovel.com/') && parsed.origin === 'https://fanqienovel.com' &&
      !parsed.username && !parsed.password && !parsed.hash && parsed.pathname.startsWith(prefix) &&
      /^(?:[A-Za-z0-9_.!~*'()-]|%[0-9A-Fa-f]{2})+$/.test(encodedTitle) &&
      title.trim().length > 0 && title.length <= 200 && !/[/\\\u0000-\u001f\u007f]/.test(title) && /^\?type=[12]$/.test(parsed.search);
  } catch { return false; }
};
const fanqieReadOnlyURL = (url,target) => {
  if (fanqieManagementURL(url,target)) return true;
  if (typeof url !== 'string' || /[\\\u0000-\u0020\u007f]/.test(url) || target?.platform !== 'fanqie' || !/^[0-9]{1,200}$/.test(target.remote_book_id)) return false;
  if (url === 'https://fanqienovel.com/main/writer/user-info?enter_from=avatar') return true;
  return new RegExp('^https://fanqienovel\\.com/main/writer/preview/'+target.remote_book_id+'&[0-9]{1,200}$').test(url);
};
const navigationTimeout = (failure,url) => {
  if (typeof failure?.message !== 'string' || !['Error','TimeoutError'].includes(failure.name) ||
      (typeof failure.code === 'string' && /auth|permission|denied|disconnect|unattach|closed|cancel|abort|forbid/i.test(failure.code))) return false;
  const waiting = /^Timed out waiting for tab [0-9]{1,30} to navigate to (.+)\.$/.exec(failure.message);
  return failure.message === 'Timed out after 10000ms waiting for CDP command Page.navigate.' || waiting?.[1] === url;
};
async function gotoNewTab(tab,url,{readOnly = false,beforeURL} = {}) {
  try { await tab.goto(url); }
  catch (failure) {
    if (readOnly !== true || beforeURL !== 'about:blank' || beforeURL === url || !navigationTimeout(failure,url)) throw failure;
    try {
      const deadline = Date.now()+5000;
      const remaining = () => { const value=deadline-Date.now();if (value <= 0) throw failure;return value; };
      while (true) {
        remaining();const current = await tab.url();remaining();
        if (current !== beforeURL && current !== url) throw failure;
        if (current === url) {
          const snapshot = await tab.playwright.domSnapshot();remaining();
          const after = await tab.url();remaining();
          if ((after !== beforeURL && after !== url) || typeof snapshot !== 'string') throw failure;
          if (after === url && snapshot.trim().length) return;
        }
        await wait(Math.min(100,remaining()));
      }
    } catch { throw failure; }
  }
}

// One queue per worker, not per operation: health can run between individual
// driver calls without overlapping the SDK's focus/setup/read work. Started
// calls are always awaited; this queue never retries a call or races its result.
class SDKCallGate {
  constructor(clock = Date.now) { this.clock = clock; this.tail = Promise.resolve(); }
  run(call,scope = null,checkAfter = true) {
    const check = () => {
      if (scope?.check) scope.check();
      if (scope?.deadline != null && this.clock() >= scope.deadline) throw error(scope.expiredCode || 'platform_browser_operation_failed');
    };
    const pending = this.tail.then(async() => { check(); const result = await call(); if (checkAfter) check(); return result; });
    this.tail = pending.catch(() => {});
    return pending;
  }
  async drain() { await this.tail; }
}

// Only the public methods used by this adapter/driver are exposed. Locator
// builders stay synchronous; every asynchronous leaf shares the same gate.
function sdkBrowser(browser,run) {
  const tabs = new WeakMap(),locators = new WeakMap();
  const leaf = (source,name,output,transform = value => value,cleanup = false,checkAfter = true) => {
    if (typeof source?.[name] === 'function') output[name] = (...args) => run(() => source[name](...args),cleanup,checkAfter).then(transform);
  };
  const locator = source => {
    requireValue(source && typeof source === 'object','platform_chrome_worker_unsupported_sdk');
    if (locators.has(source)) return locators.get(source);
    const output = {};locators.set(source,output);
    for (const name of ['locator','nth']) if (typeof source[name] === 'function') output[name] = (...args) => locator(source[name](...args));
    for (const name of ['count','evaluate','evaluateAll','isVisible','isEnabled','getAttribute','innerText','click','fill','press','pressSequentially']) leaf(source,name,output);
    return output;
  };
  const playwright = source => {
    const output = {};
    for (const name of ['locator','getByRole']) if (typeof source?.[name] === 'function') output[name] = (...args) => locator(source[name](...args));
    for (const name of ['domSnapshot','evaluate']) leaf(source,name,output);
    return output;
  };
  const tab = source => {
    requireValue(source && typeof source === 'object','platform_chrome_worker_unsupported_sdk');
    if (tabs.has(source)) return tabs.get(source);
    const output = {id:source.id,playwright:playwright(source.playwright)};tabs.set(source,output);
    for (const name of ['url','goto','reload','screenshot']) leaf(source,name,output);
    for (const name of ['close','markDeliverable']) leaf(source,name,output,undefined,true);
    for (const [key,names] of [['ax',['paste','scroll']],['cua',['scroll']]]) Object.defineProperty(output,key,{get:() => {
      if (!source[key]) return undefined;
      const api = {};for (const name of names) leaf(source[key],name,api);return api;
    }});
    return output;
  };
  const output = {browserId:browser.browserId,tabs:{},user:{}};
  for (const name of ['documentation','nameSession']) leaf(browser,name,output);
  leaf(browser.tabs,'list',output.tabs);
  leaf(browser.tabs,'get',output.tabs,tab);
  // A returned allocation must reach the two inventory-checked ownership
  // registration sites before the next gated call rejects an expired scope.
  // Neither site performs another SDK call before registering its new ID.
  leaf(browser.tabs,'new',output.tabs,tab,false,false);
  leaf(browser.user,'openTabs',output.user);leaf(browser.user,'claimTab',output.user,tab);
  return output;
}

const closeFailureCanUseAbsence = failure => failure?.name === 'Error' && !failure.code &&
  errorMarker(failure) === 'none' && !/\b(?:permission|authori[sz]ation|forbidden|denied|unsupported|not supported|disconnected|cancelled|canceled|aborted)\b/i.test(failure.message || '');
async function waitForClosedTabs(list,records,clock,sleep,deadline = clock()+5000) {
  const terminal=records.find(record=>record.failure && !closeFailureCanUseAbsence(record.failure));
  if (terminal) throw terminal.failure;
  const pending=records.filter(record=>!record.absent);if (!pending.length) return;
  const remaining=()=>{
    const value=deadline-clock();
    if (value <= 0) throw pending.find(record=>!record.absent)?.failure || error('platform_chrome_worker_cleanup_failed');
    return value;
  };
  while (true) {
    remaining();const opened=await list();remaining();
    requireValue(Array.isArray(opened) && opened.every(tab=>typeof tab?.id === 'string'),'platform_chrome_worker_unavailable');
    for (const record of pending) if (!opened.some(tab=>tab.id === record.id)) record.absent=true;
    if (pending.every(record=>record.absent)) return;
    await sleep(Math.min(100,remaining()));
  }
}

async function privateDirectory(directory) {
  const stat = await fs.lstat(directory);
  requireValue(stat.isDirectory() && !stat.isSymbolicLink() &&
    (PLATFORM === 'win32' || !(stat.mode & 0o077)) &&
    (OWNER_UID == null || stat.uid === OWNER_UID), 'platform_chrome_worker_unsafe_file');
  return stat;
}

export async function readPrivateJSON(filename, maximum = MAX_PACKET) {
  requireValue(absolute(filename), 'platform_chrome_worker_unsafe_file');
  const directory = path.dirname(filename); const parent = await privateDirectory(directory);
  const before = await fs.lstat(filename);
  requireValue(before.isFile() && !before.isSymbolicLink() && before.size <= maximum &&
    (PLATFORM === 'win32' || !(before.mode & 0o077)) &&
    (OWNER_UID == null || before.uid === OWNER_UID), 'platform_chrome_worker_unsafe_file');
  const file = await fs.open(filename, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    const opened = await file.stat();
    requireValue(opened.dev === before.dev && opened.ino === before.ino && opened.size === before.size &&
      opened.mtimeMs === before.mtimeMs, 'platform_chrome_worker_unsafe_file');
    const raw = await file.readFile(); const after = await file.stat(); const final = await fs.lstat(filename);
    const finalParent = await privateDirectory(directory);
    requireValue(raw.length <= maximum && after.size === before.size && after.mtimeMs === before.mtimeMs &&
      final.dev === before.dev && final.ino === before.ino && final.size === before.size && final.mtimeMs === before.mtimeMs &&
      finalParent.dev === parent.dev && finalParent.ino === parent.ino, 'platform_chrome_worker_unsafe_file');
    try { return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(raw)); } catch { throw error('platform_chrome_worker_invalid_packet'); }
  } finally { await file.close(); }
}

async function writePrivate(filename, value) {
  await privateDirectory(path.dirname(filename));
  const raw = typeof value === 'string' || value instanceof Uint8Array ? value : JSON.stringify(value);
  requireValue(Buffer.byteLength(raw) <= MAX_PACKET, 'platform_chrome_worker_invalid_packet');
  const file = await fs.open(filename, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW || 0), 0o600);
  try { await file.writeFile(raw); await file.sync();const stat=await file.stat();return {dev:stat.dev,ino:stat.ino}; }
  finally { await file.close(); }
}

export function validateWorkerConfig(config) {
  const required = ['schema','book_root','book_id','plan_id','profile_id','handle_path','binding_sha256','target','worker',
    'python_executable','story_cli','sdk_path','driver_path','health_interval_seconds','idle_timeout_seconds','stop_path'];
  const allowed = new Set([...required,'checkpoint_ms','poll_interval_ms','browser_id','comparison_policy','body_encoding','bootstrap_deadline_ms']);
  requireValue(own(config) && required.every(key => Object.hasOwn(config,key)) && Object.keys(config).every(key => allowed.has(key)) && config.schema === 1);
  for (const key of ['book_root','handle_path','python_executable','story_cli','sdk_path','driver_path','stop_path']) requireValue(absolute(config[key]));
  for (const key of ['book_id','plan_id','profile_id']) requireValue(typeof config[key] === 'string' && config[key].length > 0 &&
    config[key].length <= 200 && !/[\u0000-\u001f\u007f]/.test(config[key]));
  requireValue(path.basename(config.profile_id) === config.profile_id && !['.','..'].includes(config.profile_id));
  requireValue(config.story_cli === path.join(HERE,'story.py') && config.driver_path === path.join(HERE,'story_chrome_bridge.mjs'));
  requireValue(/[/\\]openai-bundled[/\\]chrome[/\\][^/\\]+[/\\]scripts[/\\]browser-client\.mjs$/.test(config.sdk_path));
  requireValue(hex(config.binding_sha256,64) && own(config.worker) && Object.keys(config.worker).length === 2 &&
    Number.isSafeInteger(config.worker.pid) && config.worker.pid > 0 && hex(config.worker.instance,32));
  requireValue(own(config.target) && Object.keys(config.target).length === 3 && config.target.platform === 'fanqie' &&
    ['account_id','remote_book_id'].every(key => typeof config.target[key] === 'string' && /^[0-9]{1,200}$/.test(config.target[key])));
  requireValue(!Object.hasOwn(config,'comparison_policy') || ['strict','fanqie_terminal_lf_v1'].includes(config.comparison_policy));
  requireValue(!Object.hasOwn(config,'body_encoding') || ['verbatim','markdown_paragraphs_v1','markdown_paragraphs_v2','plain_paragraphs_v1'].includes(config.body_encoding));
  requireValue(config.browser_id == null || typeof config.browser_id === 'string' && config.browser_id.length > 0 && config.browser_id.length <= 200);
  requireValue(!Object.hasOwn(config,'bootstrap_deadline_ms') || Number.isSafeInteger(config.bootstrap_deadline_ms) && config.bootstrap_deadline_ms > 0);
  for (const [key,minimum,maximum] of [['health_interval_seconds',5,30],['idle_timeout_seconds',30,3600],['checkpoint_ms',1,45000],['poll_interval_ms',1,1000]]) {
    if (config[key] != null) requireValue(Number.isSafeInteger(config[key]) && config[key] >= minimum && config[key] <= maximum);
  }
  requireValue(path.dirname(config.handle_path) === path.dirname(config.stop_path) && config.handle_path !== config.stop_path);
  return config;
}

// This adapter uses only declarations in the installed Chrome docs/api.json.
// Use an effective public AX or CUA scroll API. The driver independently verifies
// table and document edges, dimensions, row stability, scope and identity.
export function chromeDriverAPI(browser, {selectAllKey = PLATFORM === 'darwin' ? 'Meta+A' : 'Control+A', target = null,
  sdkGate = new SDKCallGate(),sdkScope = () => null,cleanupSleep = wait} = {}) {
  const nativeBrowser=browser,cleanupList=()=>sdkGate.run(()=>nativeBrowser.tabs.list());
  browser = sdkBrowser(browser,(call,cleanup,checkAfter) => sdkGate.run(call,cleanup ? null : sdkScope(),checkAfter));
  const tabs = new Map(); const created = new Set(); const userTabs = new Map();
  const owned = new Map();let initialTabs=null,latestControlled=[];
  const aliases = new Map(); const claimedUsers = new Map(); let preserved = null;
  const sameIdentity = (left,right) => left.providerTabId != null && right.providerTabId != null ?
    left.providerTabId === right.providerTabId : left.id === right.id;
  const popupAllowed = url => {
    if (target?.platform !== 'fanqie' || !/^[0-9]{1,200}$/.test(target.remote_book_id)) return false;
    try {
      const parsed = new URL(url); const prefix = '/main/writer/'+target.remote_book_id+'/publish/';
      return parsed.origin === 'https://fanqienovel.com' && !parsed.username && !parsed.password && !parsed.hash &&
        parsed.pathname.startsWith(prefix) && /^[0-9]{1,200}\/?$/.test(parsed.pathname.slice(prefix.length)) &&
        parsed.search === '?enter_from=newdraft';
    } catch { return false; }
  };
  const register = tab => {
    if (!owned.has(tab.id)) owned.set(tab.id,{id:tab.id,tab,attempted:false,absent:false,failure:null,promise:null});
    return owned.get(tab.id);
  };
  const registerPopup = (tab,descriptor) => {
    if (owned.has(tab.id) || !initialTabs || sdkScope()?.operation !== 'create_draft' || !popupAllowed(descriptor?.url)) return;
    if (initialTabs.some(item=>item.id === tab.id || sameIdentity(item,descriptor))) return;
    register(tab);
  };
  const attemptClose = async record => {
    if (record.promise) return record.promise;
    if (record.attempted || record.absent) return;
    record.attempted=true;
    record.promise=(async()=>{try { await record.tab.close(); } catch (failure) { record.failure=failure; }})();
    try { await record.promise; } finally { record.promise=null; }
  };
  const closeOwned = async id => {
    const record=owned.get(id);requireValue(record,'platform_chrome_worker_unsafe_user_tab');
    if (record.failure && !closeFailureCanUseAbsence(record.failure)) throw record.failure;
    if (preserved === id || record.absent) return;
    await attemptClose(record);
    if (record.failure) await waitForClosedTabs(cleanupList,[record],sdkGate.clock,cleanupSleep);
  };
  const listTabs = async() => {
    const controlled = await browser.tabs.list(); const available = await browser.user.openTabs();
    requireValue(Array.isArray(controlled) && Array.isArray(available),'platform_chrome_worker_unavailable');
    latestControlled=controlled;
    if (!initialTabs) initialTabs=[...controlled,...available].map(tab=>({id:tab.id,providerTabId:tab.providerTabId}));
    const result = controlled.map(tab => {
      requireValue(typeof tab?.id === 'string','platform_chrome_worker_unavailable');
      return {...tab,id:aliases.get(tab.id) || tab.id};
    });
    userTabs.clear();
    for (const user of available) {
      requireValue(typeof user?.id === 'string' && (user.providerTabId == null || typeof user.providerTabId === 'string'), 'platform_chrome_worker_unavailable');
      let alias = '__story_user_tab__:'+Buffer.from(JSON.stringify([user.id,user.providerTabId ?? null])).toString('base64url');
      while (controlled.some(tab => tab.id === alias)) alias = '_'+alias;
      const claimedId = claimedUsers.get(alias);
      const matches = controlled.map((tab,index) => tab.id === claimedId || sameIdentity(tab,user) ? index : -1).filter(index => index >= 0);
      requireValue(matches.length <= 1,'platform_chrome_worker_ambiguous_tab');
      if (matches.length) {
        const index = matches[0];
        // The second public listing is fresher. Never hide an editor merely
        // because the controlled listing still had its previous URL.
        result[index] = {...result[index],...(typeof user.url === 'string' ? {url:user.url} : {})};
      } else {
        requireValue(!userTabs.has(alias),'platform_chrome_worker_ambiguous_tab');
        userTabs.set(alias,user); result.push({...user,id:alias});
      }
    }
    return result;
  };
  const wrap = (tab,alias = tab?.id) => {
    requireValue(tab && typeof tab.id === 'string' && tab.playwright && typeof tab.playwright.domSnapshot === 'function', 'platform_chrome_worker_unsupported_sdk');
    if (tabs.has(alias)) return tabs.get(alias);
    const wrapped = {id:alias, playwright:tab.playwright,
      url:() => tab.url(), goto:url => tab.goto(url), reload:() => tab.reload(),
      getAXState:() => tab.playwright.domSnapshot(),
      close:() => closeOwned(tab.id),
      pasteText:async text => {
        requireValue(typeof text === 'string','platform_chrome_worker_invalid_config');
        requireValue(typeof tab.ax?.paste === 'function','platform_chrome_worker_unsupported_sdk');
        await tab.ax.paste(null,text,{format:'text'});
      },
      scroll:async(coords,direction,pages) => {
        requireValue(Array.isArray(coords) && coords.length === 2 && coords.every(Number.isFinite) &&
          ['up','down'].includes(direction) && Number.isSafeInteger(pages) && pages > 0 && pages <= 100000,
          'platform_chrome_worker_invalid_scroll');
        if (typeof tab.ax?.scroll === 'function') { await tab.ax.scroll(coords,direction,pages); return; }
        requireValue(typeof tab.cua?.scroll === 'function','platform_chrome_worker_unsupported_sdk');
        const viewport = await tab.playwright.evaluate(() => ({height:document.documentElement.clientHeight}));
        requireValue(Number.isSafeInteger(viewport?.height) && viewport.height > 0,'platform_chrome_worker_invalid_scroll');
        const delta = viewport.height * pages;
        requireValue(Number.isSafeInteger(delta) && delta > 0 && delta <= 100000000,'platform_chrome_worker_invalid_scroll');
        await tab.cua.scroll({x:coords[0],y:coords[1],scrollX:0,scrollY:direction === 'down' ? delta : -delta});
      }};
    tabs.set(alias,wrapped); return wrapped;
  };
  return {selectAllKey,
    listTabs,
    getTab:async id => {
      id = String(id);
      if (claimedUsers.has(id)) return wrap(await browser.tabs.get(claimedUsers.get(id)),id);
      if (!userTabs.has(id)) {
        const tab=await browser.tabs.get(id);registerPopup(tab,latestControlled.find(item=>item.id === id));return wrap(tab);
      }
      const previous = userTabs.get(id);
      requireValue(popupAllowed(previous.url),'platform_chrome_worker_unsafe_user_tab');
      const fresh = await browser.user.openTabs();
      const candidates = fresh.filter(tab => tab.id === previous.id && tab.providerTabId === previous.providerTabId &&
        tab.url === previous.url && tab.title === previous.title);
      requireValue(candidates.length === 1,'platform_chrome_worker_ambiguous_tab');
      const tab = await browser.user.claimTab(candidates[0]);
      registerPopup(tab,candidates[0]);
      aliases.set(tab.id,id); claimedUsers.set(id,tab.id);
      return wrap(tab,id);
    },
    newTab:async (url,context) => {
      let readOnly = false;
      if (context != null) {
        requireValue(own(context) && typeof context.readOnly === 'boolean' &&
          Object.keys(context).every(key => ['readOnly','target'].includes(key)),'platform_chrome_worker_invalid_config');
        if (context.readOnly) {
          requireValue(Object.keys(context).length === 2 && own(context.target) && Object.keys(context.target).length === 3 && target &&
            ['platform','account_id','remote_book_id'].every(key => Object.hasOwn(context.target,key) && context.target[key] === target[key]),'platform_chrome_worker_binding_mismatch');
          requireValue(fanqieReadOnlyURL(url,target),'platform_chrome_worker_invalid_config');
          readOnly = true;
        } else requireValue(Object.keys(context).length === 1,'platform_chrome_worker_invalid_config');
      }
      const before = await browser.tabs.list();
      requireValue(Array.isArray(before),'platform_chrome_worker_unavailable');
      const tab = await browser.tabs.new();
      requireValue(typeof tab?.id === 'string' && !before.some(item => item.id === tab.id) && !tabs.has(tab.id),
        'platform_chrome_worker_ambiguous_tab');
      created.add(tab.id);register(tab);
      try { wrap(tab);const beforeURL = await tab.url();await gotoNewTab(tab,url,{readOnly,beforeURL});return wrap(tab); }
      catch (failure) { try { await closeOwned(tab.id); } catch {} throw failure; }
    },
    rememberReadback:async url => {
      const listed = await browser.tabs.list();
      for (const id of [...created].reverse()) {
        if (!listed.some(tab => tab.id === id)) continue;
        try {
          if (await (await browser.tabs.get(id)).url() === url) { preserved = id; return id; }
        } catch { /* Only an actually readable owned tab can be retained. */ }
      }
      return null;
    },
    releaseReadback:id => {
      requireValue(owned.has(id),'platform_chrome_worker_unsafe_user_tab');
      if (preserved === id) preserved = null;
    },
    preserveReadback:async () => {
      if (!preserved) return null;
      const tab = await browser.tabs.get(preserved); await tab.markDeliverable(); return tab;
    },
    readbackTab:async () => preserved ? browser.tabs.get(preserved) : null,
    discardReadback:() => { preserved = null; },
    closeCreated:async() => {
      const records=[...owned.values()].filter(record=>record.id !== preserved);
      if (!records.length) return;
      const deadline=sdkGate.clock()+5000;
      const check=()=>{if (sdkGate.clock() >= deadline) throw error('platform_chrome_worker_cleanup_failed');};
      check();const opened=await cleanupList();check();
      requireValue(Array.isArray(opened) && opened.every(tab=>typeof tab?.id === 'string'),'platform_chrome_worker_unavailable');
      for (const record of records) if (!opened.some(tab=>tab.id === record.id)) record.absent=true;
      for (const record of records) await attemptClose(record);
      // Five seconds bounds read-only disappearance confirmation, not close calls.
      await waitForClosedTabs(cleanupList,records,sdkGate.clock,cleanupSleep);
    }
  };
}

async function defaultCommand(config, action, extra = [], runFile = execFile) {
  let completedExitCode = null;
  try {
    const {stdout} = await runFile(config.python_executable, ['-I','-B',config.story_cli,'publish-chrome-'+action,
      '--book',config.book_root,'--handle',config.handle_path,...extra], {encoding:'utf8',maxBuffer:MAX_PACKET,timeout:20000,windowsHide:true});
    completedExitCode = 0;
    const result = JSON.parse(stdout); requireValue(result?.ok === true,'platform_chrome_worker_cli_failed'); return result;
  } catch (failure) {
    throw cliFailure(failure,completedExitCode);
  }
}

export class ChromeWorker {
  constructor(config, {sdkFactory = null, command = defaultCommand, commandRunner = execFile, driver = null, clock = Date.now, sleep = wait,
    interval = setInterval, cancelInterval = clearInterval} = {}) {
    this.config = validateWorkerConfig(config); this.command = (action,extra=[]) => command(config,action,extra,commandRunner);
    this.sdkFactory = sdkFactory; this.driver = driver; this.clock = clock; this.sleep = sleep;
    this.interval = interval; this.cancelInterval = cancelInterval;
    this.state = 'connecting'; this.phase = 'config_validated'; this.processed = 0; this.consumed = new Set(); this.lastActivity = clock();
    this.stopRequested = false; this.done = false; this.attached = false; this.closing = false; this.healthPromise = null;
    this.keepReadback = false; this.readbackApproved = false;
    this.sdkGate = new SDKCallGate(() => this.clock());this.requestScope = null;
  }
  setPhase(phase, background = false) {
    if (background) this.healthPhase = phase;
    if (!background || this.state !== 'busy') this.phase = phase;
  }
  recordFailure(failure, phase = this.phase, code = null) {
    if (this.fatalCode) return;
    this.fatalCode = code || cleanError(failure); this.failurePhase = phase;
    this.errorName = ERROR_NAMES.has(failure?.name) ? failure.name : 'Error';
    this.errorMarker = errorMarker(failure);
    this.cliDiagnostics = failure?.[CLI_DIAGNOSTICS];
    this.driverDiagnostics = this.activeDriverError;
  }
  recordDriverProgress(event) {
    if (DRIVER_PHASES.has(event?.phase)) this.driverPhase = event.phase;
    if (event?.phase !== 'error') return;
    const diagnostics = {};
    if (DRIVER_PHASES.has(this.driverPhase)) diagnostics.driver_phase = this.driverPhase;
    for (const key of ['line','column']) {
      if (Number.isSafeInteger(event[key]) && event[key] > 0 && event[key] <= 100000) diagnostics['driver_'+key] = event[key];
    }
    this.activeDriverError = Object.freeze(diagnostics);
  }
  async persistDriverFailure(code) {
    const packet = {schema:1,code:DRIVER_ERRORS.has(code) ? code : 'platform_browser_operation_failed',
      error_name:ERROR_NAMES.has(this.activeDriverFailure?.error_name) ? this.activeDriverFailure.error_name : 'Error',
      error_marker:ERROR_MARKERS.has(this.activeDriverFailure?.error_marker) ? this.activeDriverFailure.error_marker : 'none',
      worker_code:DRIVER_WORKER_CODES.has(this.activeDriverFailure?.worker_code) ? this.activeDriverFailure.worker_code : 'none'};
    if (DRIVER_PHASES.has(this.activeDriverError?.driver_phase)) packet.driver_phase = this.activeDriverError.driver_phase;
    for (const key of ['driver_line','driver_column']) {
      const value = this.activeDriverError?.[key];
      if (Number.isSafeInteger(value) && value > 0 && value <= 100000) packet[key] = value;
    }
    try { await writePrivate(path.join(path.dirname(this.config.handle_path),'worker-driver-error.json'),packet); }
    catch { /* Diagnostics cannot replace an observed outcome or repeat a write. */ }
  }
  async persistCatalogFailure(code) {
    if (!['verify_target','find_drafts','read_draft'].includes(this.activeOperation) ||
        code !== 'platform_browser_operation_failed' || typeof this.activeCatalogMessage !== 'string') return;
    try {
      await writePrivate(path.join(path.dirname(this.config.handle_path),'worker-catalog-error.private.json'),
        {schema:1,operation:this.activeOperation,phase:DRIVER_PHASES.has(this.driverPhase) ? this.driverPhase : null,message:this.activeCatalogMessage});
    } catch { /* Local troubleshooting must not hide the original result. */ }
  }
  async persistMutationFailure(code) {
    if (!['create_draft','update_draft'].includes(this.activeOperation) ||
        !['platform_browser_operation_failed','platform_browser_navigation_timeout','platform_browser_read_timeout'].includes(code) ||
        typeof this.activeCatalogMessage !== 'string') return;
    try {
      await writePrivate(path.join(path.dirname(this.config.handle_path),'worker-mutation-error.private.json'),
        {schema:1,operation:this.activeOperation,phase:DRIVER_PHASES.has(this.driverPhase) ? this.driverPhase : null,
          code,message:this.activeCatalogMessage});
    } catch { /* Diagnostics never authorize or repeat a write. */ }
  }
  async persistBootstrapFailure(failure) {
    try {
      if (this.initialized || !['sdk_setup','browser_inventory','waiting_for_chrome','browser_documentation','existing_tab_probe'].includes(this.phase) ||
          typeof failure?.message !== 'string') return;
      await writePrivate(path.join(path.dirname(this.config.handle_path),'worker-bootstrap-error.private.json'),
        {schema:1,phase:this.phase,message:failure.message.slice(0,4096)});
    } catch { /* Diagnostic refusal preserves the original failure and cleanup. */ }
  }
  probeURL(url) {
    return fanqieManagementURL(url,this.config.target);
  }
  async clearBootstrapHint(hint) {
    if (!hint) return;
    try {
      const current = await fs.lstat(hint.filename);
      if (current.dev !== hint.dev || current.ino !== hint.ino || current.isSymbolicLink()) return;
      if (JSON.stringify(await readPrivateJSON(hint.filename,4096)) !== JSON.stringify(hint.packet)) return;
      const final = await fs.lstat(hint.filename);
      if (final.dev === hint.dev && final.ino === hint.ino) await fs.unlink(hint.filename);
    } catch { /* An advisory hint never authorizes readiness or removal of a foreign file. */ }
  }
  async browserInventory(run,allowWait) {
    const signature = JSON.stringify(this.config);let waiting=false,waitDeadline,delay=1000,hint;
    const check = async() => {
      requireValue(JSON.stringify(this.config) === signature,'platform_chrome_worker_binding_mismatch');
      await this.checkStop();
      if (this.stopRequested) throw BOOTSTRAP_CANCELLED;
      if (this.config.bootstrap_deadline_ms != null && this.clock() >= this.config.bootstrap_deadline_ms) throw error('platform_chrome_worker_connect_timeout');
      if (waiting && this.clock() >= waitDeadline) throw error('platform_chrome_worker_browser_wait_timeout');
    };
    try {
      while (true) {
        if (waiting || allowWait) await check();
        const listed = await run(() => this.agent.browsers.list());
        requireValue(JSON.stringify(this.config) === signature,'platform_chrome_worker_binding_mismatch');
        requireValue(Array.isArray(listed) && listed.every(item => own(item) && typeof item.id === 'string' && item.id.length > 0 &&
          (!Object.hasOwn(item,'family') || typeof item.family === 'string') &&
          ['iab','extension','cdp','mcpapps'].includes(item.type)),'platform_chrome_worker_unavailable');
        const chrome = listed.filter(item => item.family === 'chrome');
        requireValue(chrome.every(item => item.type === 'extension'),'platform_chrome_worker_unsupported_sdk');
        requireValue(chrome.length <= 1,'platform_chrome_worker_ambiguous_browser');
        if (waiting || allowWait) await check();
        if (chrome.length) {
          requireValue(this.config.browser_id == null || chrome[0].id === this.config.browser_id,'platform_chrome_worker_browser_changed');
          return chrome[0];
        }
        requireValue(allowWait,'platform_chrome_worker_unavailable');
        if (!waiting) {
          this.setPhase('waiting_for_chrome');
          waitDeadline = Math.min(this.clock()+20000,(this.config.bootstrap_deadline_ms ?? Infinity)-15000);
          if (waitDeadline <= this.clock()) throw error('platform_chrome_worker_connect_timeout');
          waiting=true;
          if (this.config.bootstrap_deadline_ms != null) {
            const filename=path.join(path.dirname(this.config.handle_path),'bootstrap-state.json');
            const packet={schema:1,worker_instance:this.config.worker.instance,binding_sha256:this.config.binding_sha256,
              phase:'waiting_for_chrome',observed_at_ms:this.clock(),bootstrap_deadline_ms:this.config.bootstrap_deadline_ms};
            let receipt;
            try { receipt=await writePrivate(filename,packet); }
            catch { throw error('platform_chrome_worker_unsafe_file'); }
            hint={filename,packet,...receipt};
          }
        }
        await check();
        await this.sleep(Math.min(delay,waitDeadline-this.clock()));delay=Math.min(4000,delay*2);
      }
    } finally { await this.clearBootstrapHint(hint); }
  }
  async freshProbeTab(browser) {
    requireValue(!this.probeFallbackUsed && this.probeURL(this.probeManagementURL),'platform_chrome_worker_existing_tab_required');
    this.probeFallbackUsed = true; // A failed attempt never opens another tab.
    const before = await browser.tabs.list();
    requireValue(Array.isArray(before),'platform_chrome_worker_unavailable');
    const existing = new Set(before.map(tab => tab.id));
    const tab = await browser.tabs.new();
    requireValue(typeof tab?.id === 'string' && !existing.has(tab.id) && tab.id !== this.probeTabId,
      'platform_chrome_worker_ambiguous_tab');
    this.fallbackProbeTab = tab; this.probeTabId = tab.id;
    const beforeURL = await tab.url();
    if (beforeURL !== this.probeManagementURL) await gotoNewTab(tab,this.probeManagementURL,{readOnly:true,beforeURL});
    const deadline = this.clock()+15000;
    while (!this.closing && !this.stopRequested) {
      requireValue(this.clock() < deadline,'platform_chrome_worker_unavailable');
      requireValue(await tab.url() === this.probeManagementURL && this.probeURL(this.probeManagementURL),
        'platform_chrome_worker_existing_tab_required');
      const snapshot = await tab.playwright.domSnapshot();
      if (typeof snapshot === 'string' && snapshot.trim().length) {
        requireValue(this.clock() < deadline && await tab.url() === this.probeManagementURL,'platform_chrome_worker_existing_tab_required');
        return;
      }
      await this.sleep(Math.max(1,Math.min(100,deadline-this.clock())));
    }
    throw error('platform_chrome_worker_unavailable');
  }
  async probe({background = false,deadline = null} = {}) {
    const allowWait = !background && !this.initialInventoryChecked && !this.browser && !this.attached && !this.session &&
      !this.initialized && !this.api && this.consumed.size === 0;
    if (!background) this.initialInventoryChecked=true;
    const scope = {deadline:deadline ?? (!background && !this.attached ? this.config.bootstrap_deadline_ms : null),
      expiredCode:deadline != null ? 'platform_chrome_worker_invalid_challenge' : 'platform_chrome_worker_connect_timeout',check:() => {
      requireValue(!this.closing && !this.fatalCode,'platform_chrome_worker_unavailable');
    }};
    const run = (call,cleanup = false,checkAfter = true) => this.sdkGate.run(call,cleanup ? null : scope,checkAfter);
    this.setPhase('browser_inventory',background);
    this.healthStep = 'browser_inventory';
    const selected = await this.browserInventory(run,allowWait);
    this.setPhase('browser_inventory',background);
    if (this.browser) requireValue(selected.id === this.browser.browserId,'platform_chrome_worker_browser_changed');
    else this.browser = await run(() => this.agent.browsers.get(selected.id));
    requireValue(this.browser?.browserId === selected.id,'platform_chrome_worker_browser_changed');
    const browser = sdkBrowser(this.browser,run);
    if (!this.browserBootstrapped) {
      this.setPhase('browser_documentation',background);
      const documentation = await browser.documentation();
      requireValue(typeof documentation === 'string' && documentation.trim().length > 0,'platform_chrome_worker_unsupported_sdk');
      await browser.nameSession('📝 番茄草稿');
      this.browserBootstrapped = true;
    }
    this.setPhase('existing_tab_probe',background);
    this.healthStep = 'list_tabs';
    const tabs = await browser.tabs.list();
    requireValue(Array.isArray(tabs),'platform_chrome_worker_unavailable');
    let emptyProbe = false;
    try {
      if (!this.probeTabId) {
        const candidates = tabs.filter(tab => this.probeURL(tab.url));
        if (candidates.length) {
          const current = candidates.slice().sort((a,b) => String(a.id).localeCompare(String(b.id)))[0];
          this.probeTabId = current.id; this.probeManagementURL = current.url;
        } else {
          const available = await browser.user.openTabs();
          requireValue(Array.isArray(available),'platform_chrome_worker_unavailable');
          const matching = available.filter(tab => this.probeURL(tab.url));
          requireValue(matching.length > 0,'platform_chrome_worker_existing_tab_required');
          // This is a read-only probe of the explicitly configured book. Keep
          // one stable handle when duplicate management tabs already exist.
          const current = matching.slice().sort((a,b) => String(a.id ?? a.providerTabId).localeCompare(String(b.id ?? b.providerTabId)))[0];
          this.probeManagementURL = current.url;
          this.probeTabId = (await browser.user.claimTab(current)).id;
        }
      }
      requireValue(typeof this.probeTabId === 'string','platform_chrome_worker_unavailable');
      this.healthStep = 'get_tab';
      let tab;
      try { tab = await browser.tabs.get(this.probeTabId); }
      catch (failure) {
        // A claimed user tab need not appear in the agent inventory. Only
        // classify it as unavailable when its pinned handle also fails.
        if (this.attached && !tabs.some(item => item.id === this.probeTabId) &&
            !permissionDenied(failure) && errorMarker(failure) === 'none') {
          throw error('platform_chrome_worker_target_tab_closed');
        }
        throw failure;
      }
      this.healthStep = 'read_url';
      const observedURL = tab?.id === this.probeTabId ? await tab.url() : null;
      if (tab?.id !== this.probeTabId || !this.probeURL(observedURL)) {
        try {
          await writePrivate(path.join(path.dirname(this.config.handle_path),'worker-probe-error.private.json'),
            {schema:1,phase:'existing_tab_probe',attached:this.attached,
              expected_tab_id:this.probeTabId,observed_tab_id:typeof tab?.id === 'string' ? tab.id : null,
              expected_url:this.probeManagementURL,observed_url:typeof observedURL === 'string' ? observedURL.slice(0,4096) : null,
              listed:tabs.some(item => item.id === this.probeTabId)});
        } catch { /* Diagnosis never changes the health result or retries a browser action. */ }
        throw error(this.attached ? (tab?.id === this.probeTabId ?
          'platform_chrome_worker_target_tab_changed' : 'platform_chrome_worker_target_tab_closed') :
          'platform_chrome_worker_existing_tab_required');
      }
      this.healthStep = 'dom_snapshot';
      let snapshot;
      try { snapshot = await tab.playwright.domSnapshot(); }
      catch (failure) {
        // A single read-only retry for the SDK's exact transient CDP timeouts.
        // Refusals, lost handles, changed URLs and writes never enter this path.
        if (!['Error','TimeoutError'].includes(failure?.name) || permissionDenied(failure) ||
            (typeof failure.code === 'string' && /auth|permission|denied|disconnect|unattach|closed|cancel|abort|forbid/i.test(failure.code)) ||
            !['Timed out after 3000ms waiting for CDP command Runtime.evaluate.',
              'Timed out after 3000ms waiting for CDP command Emulation.setFocusEmulationEnabled.'].includes(failure?.message)) throw failure;
        this.healthStep = 'retry_url';
        requireValue(await tab.url() === observedURL && this.probeURL(observedURL),'platform_chrome_worker_target_tab_changed');
        this.healthStep = 'retry_dom_snapshot';
        snapshot = await tab.playwright.domSnapshot();
      }
      emptyProbe = typeof snapshot === 'string' && !snapshot.trim().length;
      requireValue(typeof snapshot === 'string' && snapshot.trim().length > 0,'platform_chrome_worker_unavailable');
      this.healthStep = 'final_url';
      requireValue(await tab.url() === observedURL,'platform_chrome_worker_target_tab_changed');
    } catch (failure) {
      if (permissionDenied(failure) || (!emptyProbe && errorMarker(failure) !== 'browser_unattached') ||
          this.attached || this.probeFallbackUsed || !this.probeURL(this.probeManagementURL)) throw failure;
      await this.freshProbeTab(browser);
    }
    return selected.id;
  }
  async refreshHealth() {
    if (this.closing) return;
    if (this.healthPromise) return this.healthPromise;
    this.healthPromise = (async() => {
      this.setPhase('bridge_health',true);
      this.healthStep = 'challenge';
      const challenge = (await this.command('health')).challenge;
      requireValue(own(challenge) && challenge.schema === 1 && challenge.session_id === this.session &&
        challenge.binding_sha256 === this.config.binding_sha256 && typeof challenge.nonce === 'string' &&
        /^[A-Za-z0-9_-]{32,200}$/.test(challenge.nonce) && Number.isFinite(challenge.deadline_epoch) &&
        challenge.deadline_epoch * 1000 > this.clock(),'platform_chrome_worker_invalid_challenge');
      const browserId = await this.probe({background:true,deadline:challenge.deadline_epoch * 1000});
      requireValue(challenge.deadline_epoch * 1000 > this.clock(),'platform_chrome_worker_invalid_challenge');
      if (challenge.browser_id != null) requireValue(challenge.browser_id === browserId,'platform_chrome_worker_browser_changed');
      if (challenge.worker_instance != null) requireValue(challenge.worker_instance === this.config.worker.instance,'platform_chrome_worker_invalid_challenge');
      const filename = path.join(path.dirname(this.config.handle_path),'worker-health-'+challenge.nonce+'.json');
      await writePrivate(filename,{schema:1,session_id:this.session,binding_sha256:this.config.binding_sha256,nonce:challenge.nonce,
        browser:{kind:'chrome',id:browserId},capabilities:{list_tabs:true,get_tab:true,dom_snapshot:true},worker:{...this.config.worker}});
      this.setPhase('bridge_health',true);
      this.healthStep = 'receipt';
      const receipt = await this.command('health',['--input',filename]);
      requireValue(receipt.ready === true,'platform_chrome_worker_unavailable');
      this.healthAt = this.clock();
    })();
    try { return await this.healthPromise; }
    catch (failure) {
      this.recordFailure(failure,this.healthPhase || this.phase);
      try {
        await writePrivate(path.join(path.dirname(this.config.handle_path),'worker-health-error.private.json'),
          {schema:1,phase:this.healthPhase || this.phase,step:this.healthStep || null,
            message:typeof failure?.message === 'string' ? failure.message.slice(0,4096) : null});
      } catch { /* Private diagnostics cannot change health or authorize a retry. */ }
      throw failure;
    }
    finally {
      this.healthPromise = null;
      if (this.state === 'ready' && !this.closing) this.phase = 'queue_wait';
    }
  }
  async initialize() {
    await privateDirectory(path.dirname(this.config.handle_path));
    try { await fs.lstat(this.config.handle_path); throw error('platform_chrome_worker_handle_exists'); }
    catch (failure) { if (failure.code !== 'ENOENT') throw failure; }
    this.phase = 'sdk_setup';
    this.agent = this.sdkFactory ? await this.sdkFactory() : await (await import(pathToFileURL(this.config.sdk_path).href)).setupBrowserRuntime();
    await this.probe();
    this.api = chromeDriverAPI(this.browser,{target:this.config.target,sdkGate:this.sdkGate,sdkScope:() => this.requestScope,cleanupSleep:ms=>this.sleep(ms)});
    this.api.onProgress = event => this.recordDriverProgress(event);
    this.api.onDriverError = failure => {
      this.activeDriverFailure = Object.freeze({error_name:ERROR_NAMES.has(failure?.name) ? failure.name : 'Error',error_marker:errorMarker(failure),
        worker_code:DRIVER_WORKER_CODES.has(failure?.code) ? failure.code : 'none'});
      if (['verify_target','find_drafts','read_draft','create_draft','update_draft'].includes(this.activeOperation) && typeof failure?.message === 'string') {
        this.activeCatalogMessage = failure.message.slice(0,4096);
      }
    };
    this.driver ||= await import(pathToFileURL(this.config.driver_path).href);
    requireValue(typeof this.driver.executeChromeBridgeRequest === 'function' && typeof this.driver.closeChromeBridgeSession === 'function','platform_chrome_worker_invalid_config');
    this.phase = 'bridge_attach';
    const attached = await this.command('attach',['--id',this.config.plan_id,'--profile',this.config.profile_id,
      ...(this.config.comparison_policy && this.config.comparison_policy !== 'strict' ? ['--comparison-policy',this.config.comparison_policy] : []),
      ...(this.config.body_encoding && this.config.body_encoding !== 'verbatim' ? ['--body-encoding',this.config.body_encoding] : [])]);
    this.attached = true; this.session = attached.session_id;
    requireValue(hex(this.session,32),'platform_chrome_worker_invalid_packet');
    const handle = await readPrivateJSON(this.config.handle_path,65536);
    requireValue(handle.schema === 1 && handle.session_id === this.session && handle.book_root === this.config.book_root &&
      handle.book_id === this.config.book_id && handle.binding_sha256 === this.config.binding_sha256,'platform_chrome_worker_binding_mismatch');
    await this.refreshHealth(); this.state = 'ready'; this.phase = 'queue_wait';
  }
  async executeClaim(claim) {
    this.phase = 'queue_operation';
    requireValue(claim.claimed_once === true && hex(claim.request_id,32) && !this.consumed.has(claim.request_id),'platform_chrome_worker_duplicate_request');
    this.consumed.add(claim.request_id); // Consumption precedes any browser call.
    const expected = path.join(this.config.book_root,'.story','platform-chrome-bridge',this.session,'claimed-'+claim.request_id+'.json');
    requireValue(claim.request_path === expected,'platform_chrome_worker_binding_mismatch');
    const request = await readPrivateJSON(expected);
    requireValue(request.schema === 1 && request.session_id === this.session && request.id === claim.request_id &&
      request.binding_sha256 === this.config.binding_sha256 && request.binding?.target &&
      (Object.hasOwn(request.binding,'comparison_policy') ? request.binding.comparison_policy : 'strict') ===
        (this.config.comparison_policy || 'strict') &&
      (Object.hasOwn(request.binding,'body_encoding') ? request.binding.body_encoding : 'verbatim') ===
        (this.config.body_encoding || 'verbatim') &&
      ['platform','account_id','remote_book_id'].every(key => request.binding.target[key] === this.config.target[key]) &&
      ['verify_target','find_drafts','read_draft','create_draft','update_draft'].includes(request.operation),'platform_chrome_worker_binding_mismatch');
    const deadline = Date.parse(request.deadline);
    requireValue(Number.isFinite(deadline),'platform_chrome_worker_invalid_packet');
    this.requestScope = {deadline,operation:request.operation,check:() => {
      requireValue(!this.closing && !this.fatalCode,'platform_chrome_worker_unavailable');
    }};
    try {
    this.state = 'busy'; this.lastActivity = this.clock();
    this.driverPhase = null; this.activeDriverError = null; this.activeDriverFailure = null;
    this.activeOperation = request.operation; this.activeCatalogMessage = null;
    let response;
    try {
      response = await this.driver.executeChromeBridgeRequest(this.api,request);
      if (response.ok !== true) {
        await this.persistDriverFailure(response.error); await this.persistCatalogFailure(response.error);
        await this.persistMutationFailure(response.error);
      }
    } finally { this.activeOperation = null; this.activeCatalogMessage = null; }
    const filename = path.join(path.dirname(this.config.handle_path),'worker-response-'+request.id+'.json');
    await writePrivate(filename,response);
    await this.command('respond',['--request-id',request.id,'--input',filename]);
    this.processed++; this.lastActivity = this.clock();
    if (response.ok !== true) {
      this.recordFailure(error(/^platform_browser_[a-z_]+$/.test(response.error || '') ? response.error : 'platform_chrome_worker_failed'));
      return;
    }
    if (response.ok) {
      const observation = request.operation === 'read_draft' ? response.result :
        request.operation === 'find_drafts' && Array.isArray(response.result) ? response.result.at(-1) : null;
      const url = observation?.url || (['create_draft','update_draft'].includes(request.operation) && typeof response.result === 'string' ?
        (request.profile.readback?.url || request.profile.urls.edit).replace('{remote_id}',response.result) : null);
      if (typeof url === 'string') await this.api.rememberReadback(url);
      if (['create_draft','update_draft'].includes(request.operation)) {
        const tab = await this.api.readbackTab();
        if (tab) {
          try { await writePrivate(path.join(path.dirname(this.config.handle_path),'worker-proof-'+request.id+'.png'),await tab.screenshot({fullPage:false})); }
          catch { /* A screenshot failure never repeats a recorded remote write. */ }
        }
      }
    }
    this.driverPhase = null; this.activeDriverError = null; this.activeDriverFailure = null;
    this.state = 'ready'; this.phase = 'queue_wait';
    } finally { this.requestScope = null; }
  }
  async finish() {
    if (this.done) return;
    this.closing = true; this.phase = 'cleanup';
    if (this.healthPromise) { try { await this.healthPromise; } catch (failure) { this.recordFailure(failure,this.healthPhase || this.phase); } }
    if (this.attached) { try { await this.command('detach'); } catch (failure) { this.recordFailure(failure,this.phase,'platform_chrome_worker_detach_failed'); } }
    if (this.api) {
      if (this.keepReadback && this.readbackApproved && !this.fatalCode) {
        try { await this.api.preserveReadback(); } catch (failure) { this.recordFailure(failure); }
      }
      if (!this.keepReadback || !this.readbackApproved || this.fatalCode) this.api.discardReadback();
      if (this.session) {
        try { await this.driver.closeChromeBridgeSession(this.api,this.session,{keepReadback:false}); }
        catch (failure) { this.recordFailure(failure,this.phase,'platform_chrome_worker_cleanup_failed'); this.api.discardReadback(); }
      }
      try { await this.api.closeCreated(); }
      catch (failure) { this.recordFailure(failure,this.phase,'platform_chrome_worker_cleanup_failed'); }
    }
    if (this.fallbackProbeTab) {
      const record={id:this.fallbackProbeTab.id,absent:false,failure:null};
      try {
        try { await this.fallbackProbeTab.close(); } catch (failure) { record.failure=failure; }
        await waitForClosedTabs(()=>this.sdkGate.run(()=>this.browser.tabs.list()),[record],()=>this.clock(),ms=>this.sleep(ms));
      }
      catch (failure) { this.recordFailure(failure,this.phase,'platform_chrome_worker_cleanup_failed'); }
      this.fallbackProbeTab = null;
    }
    this.done = true;
  }
  async checkStop() {
    try {
      await fs.lstat(this.config.stop_path);
      const stop = await readPrivateJSON(this.config.stop_path,4096);
      requireValue(own(stop) && Object.keys(stop).length === 3 && stop.schema === 1 &&
        stop.worker_instance === this.config.worker.instance && typeof stop.keep_readback === 'boolean','platform_chrome_worker_invalid_stop');
      this.keepReadback = stop.keep_readback; this.readbackApproved = stop.keep_readback; this.stopRequested = true;
    } catch (failure) { if (failure.code !== 'ENOENT') throw failure; }
  }
  async loop(deadline) {
      do {
        if (this.stopRequested || this.fatalCode) break;
        await this.checkStop();
        if (this.stopRequested) break;
        if (this.clock() - this.lastActivity >= this.config.idle_timeout_seconds * 1000) { this.state = 'idle_timeout'; break; }
        if (this.healthPromise) await this.healthPromise;
        if (this.clock() - this.healthAt >= this.config.health_interval_seconds * 1000) await this.refreshHealth();
        this.phase = 'queue_wait';
        const claim = await this.command('next');
        if (claim.request_id != null) await this.executeClaim(claim);
        else await this.sleep(this.config.poll_interval_ms || 250);
      } while (this.clock() < deadline);
  }
  snapshot() {
    return {done:this.done,state:this.state,phase:this.phase,processed:this.processed,...(this.done && this.state === 'failed' && this.fatalCode ? {
      code:this.fatalCode,failure_phase:this.failurePhase || this.phase,error_name:this.errorName || 'Error',error_marker:this.errorMarker || 'none',
      ...this.cliDiagnostics,...this.driverDiagnostics} : {})};
  }
  async checkpoint() {
    if (this.checkpointPromise) return this.checkpointPromise;
    if (this.done) return this.snapshot();
    this.checkpointPromise = (async() => {
      let healthTimer,active=true;
      try {
        if (!this.stopRequested && !this.fatalCode) {
          await this.checkStop();
          if (!this.stopRequested && this.clock()-this.lastActivity >= this.config.idle_timeout_seconds*1000) this.state='idle_timeout';
        }
        if (!this.stopRequested && !this.fatalCode && this.state !== 'idle_timeout') {
          if (!this.initialized) { await this.initialize(); this.initialized = true; }
          else await this.refreshHealth();
          healthTimer = this.interval(() => {
            if (active && !this.closing && !this.stopRequested) this.refreshHealth().catch(failure => this.recordFailure(failure,this.healthPhase || this.phase));
          },this.config.health_interval_seconds * 1000);
          // Yield only between complete operations. An in-flight browser call
          // must stay awaited by this actual Node REPL tool invocation.
          await this.loop(this.clock()+(this.config.checkpoint_ms || 20000));
        }
      } catch (failure) {
        if (failure !== BOOTSTRAP_CANCELLED || !this.stopRequested) { this.recordFailure(failure);await this.persistBootstrapFailure(failure); }
      }
      finally {
        active=false;this.cancelInterval(healthTimer);
        if (this.healthPromise) {
          try { await this.healthPromise; }
          catch (failure) { this.recordFailure(failure,this.healthPhase || this.phase); }
        }
        await this.sdkGate.drain();
        if (this.stopRequested) this.state = 'stopped';
        if (this.stopRequested || this.fatalCode || this.state === 'idle_timeout') {
          await this.finish();
          if (this.fatalCode) this.state = 'failed';
        }
      }
      return this.snapshot();
    })();
    try { return await this.checkpointPromise; }
    finally { this.checkpointPromise = null; }
  }
  async stop() {
    this.keepReadback = false; this.readbackApproved = false; this.stopRequested = true;
    await (this.checkpointPromise || this.checkpoint());
  }
}

export async function runChromeWorker(configPath) {
  let worker;
  try {
    const config = validateWorkerConfig(await readPrivateJSON(configPath,65536));
    requireValue(path.dirname(configPath) === path.dirname(config.handle_path),'platform_chrome_worker_unsafe_file');
    const signature = createHash('sha256').update(JSON.stringify(config)).digest('hex');
    const previous = WORKERS.get(configPath);
    if (previous) {
      worker = previous.worker;
      requireValue(previous.signature === signature,'platform_chrome_worker_config_changed');
    } else { worker = new ChromeWorker(config); WORKERS.set(configPath,{signature,worker}); }
    return await worker.checkpoint();
  } catch (failure) {
    worker ||= WORKERS.get(configPath)?.worker;
    if (worker) { worker.recordFailure(failure); worker.stopRequested = true; if (worker.done) worker.state = 'failed'; return worker.checkpoint(); }
    return {done:true,state:'failed',processed:worker?.processed || 0,code:cleanError(failure),failure_phase:'config_validated',
      error_name:ERROR_NAMES.has(failure?.name) ? failure.name : 'Error',error_marker:errorMarker(failure)};
  }
}

export const runWorker = runChromeWorker;
