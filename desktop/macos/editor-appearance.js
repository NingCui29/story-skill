/* Presentation-only compatibility for an already running local editor service.
   Moving existing presentation nodes preserves their handlers and editing session. */
(() => {
  const url = new URL(location.href);
  if (window !== window.top || url.protocol !== 'http:' || url.hostname !== '127.0.0.1' ||
      !/^[0-9]+$/.test(url.port) || Number(url.port) < 1 || Number(url.port) > 65535 ||
      url.username || url.password || url.search || !/^\/[A-Za-z0-9_-]{20,128}\/$/.test(url.pathname)) {
    return {applied: false, reason: 'outside-editor'};
  }
  const modern = !!document.getElementById('book-info');
  if (document.getElementById('story-editor-appearance')) return {applied: true, legacy: !modern};
  const header = document.querySelector('body > header');
  const heading = header?.querySelector('.book-heading');
  const identity = heading?.querySelector('.book-identity');
  const actions = header?.querySelector('.header-actions');
  const title = document.getElementById('book-title');
  const kind = document.getElementById('book-kind');
  const progress = document.getElementById('book-progress');
  const classification = document.getElementById('book-classification');
  const bookLocation = document.getElementById('current-book-location');
  const rootLocation = document.getElementById('root-location');
  const maintenance = document.getElementById('maintenance');
  if (!header || !heading || !identity || !actions || !title || !kind || !progress ||
      !classification || !bookLocation || !rootLocation || !maintenance ||
      !heading.contains(title) || !heading.contains(progress) || !actions.contains(maintenance)) {
    return {applied: false, reason: 'unknown-layout'};
  }
  const element = (tag, id, className) => {
    const node = document.createElement(tag);
    if (id) node.id = id;
    if (className) node.className = className;
    return node;
  };
  if (!modern) {
    const info = element('details', 'book-info', 'menu');
    const summary = element('summary');
    summary.textContent = '作品信息';
    const panel = element('div', 'book-info-panel', 'menu-panel book-info-panel');
    const fullTitle = element('strong', 'book-info-title');
    const fullProgress = element('p', 'book-progress-detail');
    panel.append(fullTitle, fullProgress, classification, bookLocation, rootLocation);
    info.append(summary, panel);
    identity.prepend(title);
    heading.prepend(identity);
    const eyebrow = heading.querySelector('.eyebrow');
    if (eyebrow) eyebrow.hidden = true;
    actions.insertBefore(info, maintenance);
    const settings = maintenance.querySelector('summary');
    if (settings) settings.textContent = '设置';
    document.addEventListener('pointerdown', event => {
      if (info.open && !info.contains(event.target)) info.open = false;
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && info.open) {
        info.open = false;
        summary.focus();
      }
    });
  }
  const info = document.getElementById('book-info');
  const fullTitle = document.getElementById('book-info-title');
  const fullProgress = document.getElementById('book-progress-detail');
  const compactProgress = value => {
    const formal = value.match(/(?:^|：\s*|·\s*)正式\s*(\d+)\s*章/);
    const next = value.match(/下一章\s*(?:第\s*)?(\d+)\s*章?/);
    return formal && next ? `正式 ${formal[1]} 章 · 下一章 第${next[1]}章` : value;
  };
  const syncLabels = () => {
    if (fullTitle && fullTitle.textContent !== title.textContent) fullTitle.textContent = title.textContent;
    if (!fullProgress || !info?.contains(fullProgress)) return;
    const current = progress.textContent;
    // New services already provide the short label plus a complete tooltip.
    // Old services still replace the full label on refresh; retain that text first.
    const supplied = progress.title || '';
    const currentSummary = compactProgress(current);
    const full = current === currentSummary && supplied.length > current.length && compactProgress(supplied) === currentSummary ? supplied : current;
    const summary = compactProgress(full);
    if (fullProgress.textContent !== full) fullProgress.textContent = full;
    if (progress.title !== full) progress.title = full;
    if (progress.textContent !== summary) progress.textContent = summary;
  };
  syncLabels();
  // Observe only these two display labels, never document buffers or inputs.
  if (typeof MutationObserver !== 'undefined') {
    const labels = new MutationObserver(syncLabels);
    for (const node of [title, progress]) labels.observe(node, {childList: true, characterData: true, subtree: true});
  }
  info?.addEventListener('toggle', () => { if (info.open) syncLabels(); });
  const settingsPanel = maintenance.querySelector('.menu-panel');
  const refresh = document.getElementById('refresh');
  const compactRefresh = document.getElementById('refresh-compact');
  if (settingsPanel && refresh && header.contains(refresh)) {
    if (refresh.parentElement !== settingsPanel) settingsPanel.prepend(refresh);
    refresh.textContent = '刷新目录';
    refresh.title = '刷新目录与状态，不丢弃未保存文字';
    if (compactRefresh && maintenance.contains(compactRefresh)) compactRefresh.hidden = true;
  }
  const locate = document.getElementById('locate');
  const directorySearch = document.getElementById('full-query')?.closest('details');
  const directory = document.getElementById('book-nav');
  if (locate && directorySearch && directory?.contains(locate) && directory.contains(directorySearch)) {
    if (locate.parentElement !== directorySearch) directorySearch.insertBefore(locate, document.getElementById('full-query'));
    const summary = directorySearch.querySelector('summary');
    if (summary?.textContent === '全文搜索') summary.textContent = '查找与定位';
  }
  // Reuse only a control already provided by this service and its own theme code.
  // Older services without an appearance selector keep their original capabilities.
  const theme = document.getElementById('workbench-theme');
  const themeLabel = theme?.closest('.workbench-theme-control');
  if (themeLabel && header.contains(themeLabel) && themeLabel.parentElement !== actions) {
    actions.insertBefore(themeLabel, maintenance);
  }
  // Fold low-frequency version navigation, preserving the original select node.
  const chapterPicker = document.getElementById('chapter-picker');
  const metadata = chapterPicker?.parentElement;
  if (chapterPicker && metadata?.classList.contains('document-meta') &&
      !document.getElementById('document-options')) {
    const options = element('details', 'document-options');
    const summary = element('summary');
    summary.textContent = '稿件与材料';
    metadata.insertBefore(options, chapterPicker);
    options.append(summary, chapterPicker);
  }
  const style = element('style', 'story-editor-appearance');
  style.textContent = `
.native-compact-editor{--paper:#f5f8fb;--surface:#fff;--ink:#253b4d;--muted:#5b7082;--line:#dde7ef;--accent:#147dad;--wb-panel:#f2f6fa;--wb-tint:#edf4f9;--wb-hover:#eaf4fb;--wb-selected:#e2f2fc;--wb-selected-ink:#125a7e;--wb-button:#38bdf8;--wb-button-ink:#082f49;color-scheme:light;color:var(--ink);background:var(--paper)}
html[data-theme="dark"] .native-compact-editor{--paper:#141c24;--surface:#1b2732;--ink:#e0ebf3;--muted:#9eb2c2;--line:#334858;--accent:#79cbf2;--wb-panel:#17222c;--wb-tint:#253746;--wb-hover:#2b4052;--wb-selected:#23485f;--wb-selected-ink:#c1eafe;--wb-button:#38bdf8;--wb-button-ink:#082f49;color-scheme:dark}
@media(prefers-color-scheme:dark){html:not([data-theme]) .native-compact-editor{--paper:#141c24;--surface:#1b2732;--ink:#e0ebf3;--muted:#9eb2c2;--line:#334858;--accent:#79cbf2;--wb-panel:#17222c;--wb-tint:#253746;--wb-hover:#2b4052;--wb-selected:#23485f;--wb-selected-ink:#c1eafe;--wb-button:#38bdf8;--wb-button-ink:#082f49;color-scheme:dark}}
body.native-compact-editor>header{align-items:center;gap:20px;padding:12px 22px;background:var(--surface);border-color:var(--line)}
.native-compact-editor .book-heading{flex:1;min-width:0}.native-compact-editor .book-identity{display:flex;align-items:center;gap:9px;min-width:0;font-size:12px}
.native-compact-editor #book-title{display:block;min-width:0;font-size:16px;font-weight:650;letter-spacing:.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.native-compact-editor #book-kind{flex-shrink:0;font-size:10px;border-radius:5px;padding:2px 7px;color:var(--muted);background:var(--wb-tint)}
.native-compact-editor #book-progress{font-size:11px;line-height:1.6;margin:4px 0 0;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.native-compact-editor .header-actions{flex-shrink:0;gap:5px;max-width:none}.native-compact-editor .header-actions>button,.native-compact-editor .header-actions>details>summary{font-size:12px;padding:7px 9px;white-space:nowrap;border-color:transparent;background:transparent}
.native-compact-editor .header-actions>.workbench-theme-control{gap:6px;margin-left:5px;padding-left:12px;border-left:1px solid var(--line);font-size:11px;color:var(--muted)}
.native-compact-editor .workbench-theme-control select{min-height:31px;max-width:108px;font-size:12px;border-radius:7px;background:var(--wb-tint);border-color:transparent;padding:5px 7px}
.native-compact-editor button,.native-compact-editor select,.native-compact-editor input,.native-compact-editor .menu>summary{color:var(--ink);border-color:var(--line);background:var(--surface)}
.native-compact-editor button:hover:not(:disabled),.native-compact-editor .menu[open]>summary{background:var(--wb-hover)}
.native-compact-editor button:focus-visible,.native-compact-editor input:focus-visible,.native-compact-editor textarea:focus-visible,.native-compact-editor select:focus-visible,.native-compact-editor summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.native-compact-editor .menu-panel{padding:10px;border-radius:12px;border-color:var(--line);background:var(--surface);box-shadow:0 12px 30px #102a3b14}.native-compact-editor .menu-panel>button{padding:8px 10px;margin:2px 0;border-radius:7px;font-size:12px}.native-compact-editor .menu-panel>strong{display:block;padding:12px 10px 4px;font-size:10px;letter-spacing:.06em}
.native-compact-editor .menu-panel.book-info-panel{width:360px;padding:18px}.native-compact-editor .book-info-panel>strong{display:block;padding:0;font-size:14px;letter-spacing:0;color:var(--ink);overflow-wrap:anywhere}.native-compact-editor .book-info-panel>p{font-size:12px;color:var(--muted);margin:7px 0 12px;overflow-wrap:anywhere}.native-compact-editor .book-info-panel>details{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}.native-compact-editor .book-info-panel>details>summary{font-size:12px;margin:0}
.native-compact-editor #current-book-location{position:static}.native-compact-editor #current-book-root{position:static;width:auto;max-width:100%;font-size:12px;margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;padding:0;border:0;box-shadow:none;background:transparent}
.native-compact-editor nav,.native-compact-editor aside{padding:22px 16px;background:var(--wb-panel);border-color:var(--line)}
.native-compact-editor nav>details>summary{font-size:12px;color:var(--muted)}.native-compact-editor nav>details:last-child{margin-top:20px;padding-top:4px;border-top:1px solid var(--line)}
.native-compact-editor .navigation-mode{display:grid;grid-template-columns:1fr 1fr;gap:3px;padding:4px;background:var(--wb-tint);border-radius:9px;margin:18px 0 14px}.native-compact-editor .navigation-mode button{font-size:11px;padding:6px 4px;border-color:transparent;background:transparent;border-radius:6px}.native-compact-editor .navigation-mode [aria-pressed="true"]{background:var(--surface)!important;color:var(--wb-selected-ink);font-weight:600;box-shadow:0 1px 3px #102a3b10}.native-compact-editor #locate{grid-column:1/-1;padding:5px;font-size:11px;color:var(--muted)}
.native-compact-editor #search,.native-compact-editor #full-query,.native-compact-editor #book-select{font-size:12px;border-radius:8px;background:var(--surface);color:var(--ink);border-color:var(--line);padding:9px 10px}.native-compact-editor .pager{gap:5px}.native-compact-editor .pager button{flex:1;font-size:11px;padding:5px;background:transparent}.native-compact-editor #range{font-size:10px;margin-bottom:14px}
.native-compact-editor .pager:has(>#newer:disabled):has(>#older:disabled){display:none}.native-compact-editor nav>details #locate{display:block;padding:5px 0;margin:0 0 10px;text-align:left}.native-compact-editor #refresh-compact[hidden]{display:none!important}
.native-compact-editor .document{padding:10px 11px;margin:3px 0;border-radius:8px;line-height:1.6;border-color:transparent;background:transparent}.native-compact-editor .document:hover{background:var(--wb-hover)}.native-compact-editor .document.selected{background:var(--wb-selected)!important;color:var(--wb-selected-ink);box-shadow:inset 3px 0 var(--accent);font-weight:600}.native-compact-editor .document-type,.native-compact-editor .document-location{font-size:10px;color:var(--muted)}.native-compact-editor #list>details{border-color:var(--line);padding-bottom:10px}.native-compact-editor #list>details>summary{font-size:12px;font-weight:600;color:var(--ink)}
.native-compact-editor main{padding:16px clamp(24px,3.5vw,54px) 80px;scroll-padding-top:80px;background:var(--surface)}
.native-compact-editor .tools{gap:8px;top:-16px;padding:10px 0 12px;background:var(--surface);border-color:var(--line)}.native-compact-editor .mode-actions,.native-compact-editor .save-actions{gap:5px}.native-compact-editor .tools button,.native-compact-editor .tools summary{font-size:12px;padding:7px 10px;border-radius:7px}.native-compact-editor .tools #edit,.native-compact-editor .tools #compare,.native-compact-editor .tools .menu>summary{border-color:transparent;background:transparent}.native-compact-editor .tools #edit[aria-pressed="true"]{background:var(--wb-selected);color:var(--wb-selected-ink)}
.native-compact-editor #save{min-height:34px;padding:7px 15px;font-weight:600;letter-spacing:.01em;background:var(--wb-button);border-color:var(--wb-button);color:var(--wb-button-ink)}.native-compact-editor #save:hover:not(:disabled){filter:brightness(.94)}.native-compact-editor #save:disabled{background:var(--wb-tint);border-color:transparent;color:var(--muted);opacity:1}
.native-compact-editor #title{max-width:780px;margin:26px auto 10px;font-size:27px;font-weight:600;line-height:1.45;letter-spacing:.01em}.native-compact-editor .document-meta{max-width:780px;gap:6px 12px;margin:0 auto 26px;padding-bottom:18px;border-bottom:1px solid var(--line)}.native-compact-editor #message{max-width:780px;margin:12px auto 0;font-size:11px;line-height:1.7;color:var(--muted)}.native-compact-editor #chapter-picker{font-size:11px;gap:10px;margin-top:4px}.native-compact-editor #chapter-version{font-size:11px;padding:6px 8px;background:var(--wb-panel);border-color:transparent;border-radius:6px}.native-compact-editor #count-details,.native-compact-editor #word-count,.native-compact-editor #state{font-size:11px}
.native-compact-editor #message:empty{display:none}.native-compact-editor #document-options{min-width:0;max-width:100%;font-size:11px;color:var(--muted)}.native-compact-editor #document-options>summary{font-size:11px;margin:0;padding:0;color:var(--muted)}.native-compact-editor #document-options[open]{flex-basis:100%}.native-compact-editor #document-options[open]>summary{margin-bottom:8px}.native-compact-editor #document-options:has(>#chapter-picker[hidden]){display:none}.native-compact-editor #chapter-picker-help{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.native-compact-editor .identity-badges{flex-basis:auto;margin:0;gap:5px}.native-compact-editor .identity-badge{font-size:10px;padding:2px 7px;border-radius:5px}.native-compact-editor #history-note{max-width:780px;margin:0 auto 22px}.native-compact-editor #document-body{max-width:780px;margin:0 auto}.native-compact-editor #prose,.native-compact-editor #text{color:var(--ink);background:transparent}.native-compact-editor #text:focus{box-shadow:none}.native-compact-editor #text{outline-offset:9px}
.native-compact-editor aside h3{margin:0 0 18px;font-size:11px;letter-spacing:.08em;color:var(--muted)}.native-compact-editor #chapter-info{font-size:12px;line-height:1.9}.native-compact-editor #chapter-info h4{font-size:10px;margin:20px 0 7px;color:var(--muted)}.native-compact-editor #chapter-info p{margin-bottom:14px}.native-compact-editor #version-details,.native-compact-editor #adoption-help{margin-top:20px;font-size:11px}.native-compact-editor #version-details>summary,.native-compact-editor #adoption-help>summary{padding-top:2px}.native-compact-editor #detail,.native-compact-editor #path{font-size:11px;color:var(--muted)}
@media(max-width:1100px){body.native-compact-editor>header{padding:11px 16px;gap:12px}.native-compact-editor .header-actions{gap:2px}.native-compact-editor .header-actions>button,.native-compact-editor .header-actions>details>summary{padding:7px}.native-compact-editor .header-actions>.workbench-theme-control{padding-left:8px;margin-left:2px}.native-compact-editor main{padding:16px 26px 70px}.native-compact-editor .tools button,.native-compact-editor .tools summary{padding:7px 8px}.native-compact-editor #title{font-size:25px}}
@media(max-width:820px){body.native-compact-editor>header{padding:11px 14px;gap:9px;flex-wrap:wrap}.native-compact-editor .book-heading{flex-basis:100%}.native-compact-editor #book-title{font-size:15px}.native-compact-editor .header-actions{width:100%;max-width:none;justify-content:flex-start;flex-wrap:wrap;gap:4px}.native-compact-editor .header-actions>.workbench-theme-control{margin-left:auto;border-left:0;padding-left:0}.native-compact-editor .header-actions>#refresh{display:none}.native-compact-editor .workbench-theme-control select{max-width:100px}.native-compact-editor .menu-panel.book-info-panel{position:fixed;top:112px;right:14px;left:14px;width:auto;max-width:none;max-height:calc(100vh - 138px)}.native-compact-editor main{padding:12px 20px 64px}.native-compact-editor .tools{top:-12px;padding:8px 0 10px;gap:6px}.native-compact-editor .tools button,.native-compact-editor .tools summary{font-size:11px;padding:7px}.native-compact-editor #save{padding:7px 12px}.native-compact-editor #title{font-size:23px;margin-top:22px}.native-compact-editor .document-meta{margin-bottom:22px}.native-compact-editor nav,.native-compact-editor aside{width:min(340px,88vw);padding:20px 18px}}
@media(max-width:480px){.native-compact-editor .header-actions>button,.native-compact-editor .header-actions>details>summary{font-size:11px;padding:6px}.native-compact-editor .header-actions>.workbench-theme-control{font-size:10px;gap:4px}.native-compact-editor .workbench-theme-control select{font-size:11px;max-width:91px;min-height:29px}.native-compact-editor main{padding:10px 17px 56px}.native-compact-editor .tools{top:-10px;row-gap:9px}.native-compact-editor .mode-actions{gap:1px}.native-compact-editor .save-actions{margin-left:auto}.native-compact-editor .tools button,.native-compact-editor .tools summary{padding:6px}.native-compact-editor #title{font-size:21px}.native-compact-editor #chapter-picker{flex-wrap:wrap}.native-compact-editor #chapter-picker label{flex-basis:100%}}
@media(prefers-reduced-motion:reduce){.native-compact-editor button{transition:none}}
`;
  document.head.append(style);
  document.body.classList.add('native-compact-editor');
  return {applied: true, legacy: !modern};
})();
