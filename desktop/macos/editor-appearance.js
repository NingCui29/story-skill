/* Presentation-only compatibility for an already running local editor service.
   Moving existing header nodes preserves their handlers and the editing session. */
(() => {
  const url = new URL(location.href);
  if (window !== window.top || url.protocol !== 'http:' || url.hostname !== '127.0.0.1' ||
      !/^[0-9]+$/.test(url.port) || Number(url.port) < 1 || Number(url.port) > 65535 ||
      url.username || url.password || url.search || !/^\/[A-Za-z0-9_-]{20,128}\/$/.test(url.pathname)) {
    return {applied: false, reason: 'outside-editor'};
  }
  if (document.getElementById('book-info')) return {applied: true, legacy: false};
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
  const info = element('details', 'book-info', 'menu');
  const summary = element('summary');
  summary.textContent = '作品信息';
  const panel = element('div', 'book-info-panel', 'menu-panel book-info-panel');
  const fullTitle = element('strong', 'book-info-title');
  const fullProgress = element('p', 'book-progress-detail');
  const syncLabels = () => {
    fullTitle.textContent = title.textContent;
    fullProgress.textContent = progress.textContent;
  };
  syncLabels();
  // Observe only these two display labels, never document buffers or inputs.
  if (typeof MutationObserver !== 'undefined') {
    const labels = new MutationObserver(syncLabels);
    for (const node of [title, progress]) labels.observe(node, {childList: true, characterData: true, subtree: true});
  }
  panel.append(fullTitle, fullProgress, classification, bookLocation, rootLocation);
  info.append(summary, panel);
  identity.prepend(title);
  heading.prepend(identity);
  const eyebrow = heading.querySelector('.eyebrow');
  if (eyebrow) eyebrow.hidden = true;
  actions.insertBefore(info, maintenance);
  const refresh = document.getElementById('refresh');
  if (refresh) {
    refresh.textContent = '刷新';
    refresh.title = '刷新目录与状态，不丢弃未保存文字';
  }
  const settings = maintenance.querySelector('summary');
  if (settings) settings.textContent = '设置';
  info.addEventListener('toggle', () => { if (info.open) syncLabels(); });
  document.addEventListener('pointerdown', event => {
    if (info.open && !info.contains(event.target)) info.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && info.open) {
      info.open = false;
      summary.focus();
    }
  });
  const style = element('style', 'story-editor-appearance');
  style.textContent = `
body.native-compact-editor > header{align-items:center;gap:16px;padding:10px 20px}
.native-compact-editor .book-heading{flex:1;min-width:0}
.native-compact-editor .book-identity{display:flex;align-items:center;gap:9px;min-width:0;font-size:12px}
.native-compact-editor #book-title{display:block;min-width:0;font-size:17px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.native-compact-editor #book-kind{flex-shrink:0}
.native-compact-editor #book-progress{font-size:11px;line-height:1.6;margin:3px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.native-compact-editor .header-actions{flex-shrink:0;gap:6px;max-width:none}
.native-compact-editor .header-actions>button,.native-compact-editor .header-actions>details>summary{font-size:12px;padding:6px 9px;white-space:nowrap}
.native-compact-editor .menu-panel.book-info-panel{width:380px}
.native-compact-editor .book-info-panel>strong{display:block;font-size:14px;color:var(--ink);overflow-wrap:anywhere}
.native-compact-editor .book-info-panel>p{font-size:12px;color:var(--muted);margin:7px 0 12px;overflow-wrap:anywhere}
.native-compact-editor .book-info-panel>details{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}
.native-compact-editor .book-info-panel>details>summary{font-size:12px;margin:0}
.native-compact-editor #current-book-location{position:static}
.native-compact-editor #current-book-root{position:static;width:auto;max-width:100%;font-size:12px;margin:8px 0 0;white-space:pre-wrap;overflow-wrap:anywhere;padding:0;border:0;box-shadow:none;background:transparent}
.native-compact-editor nav,.native-compact-editor aside{padding:18px 16px}
.native-compact-editor main{padding:14px clamp(22px,3vw,48px) 60px;scroll-padding-top:72px}
.native-compact-editor .tools{gap:6px;top:-14px;padding:7px 0}
.native-compact-editor .tools button,.native-compact-editor .tools summary{font-size:12px;padding:6px 9px}
.native-compact-editor h1{font-size:24px;line-height:1.45;margin:14px 0 7px}
.native-compact-editor .document-meta{gap:4px 12px;margin-bottom:14px}
.native-compact-editor #message{margin:8px 0}
.native-compact-editor .identity-badges{flex-basis:auto;margin:0}
.native-compact-editor .identity-badge{font-size:11px;padding:1px 7px}
@media(max-width:1100px){body.native-compact-editor > header{padding:10px 16px;gap:12px}.native-compact-editor .header-actions{gap:5px}.native-compact-editor main{padding:14px 24px 60px}}
@media(max-width:820px){body.native-compact-editor > header{padding:9px 14px;gap:8px;flex-wrap:wrap;align-items:center}.native-compact-editor .book-heading{flex-basis:100%}.native-compact-editor #book-title{font-size:15px}.native-compact-editor .header-actions{width:100%;max-width:none;justify-content:flex-start;gap:5px}.native-compact-editor .menu-panel.book-info-panel{position:fixed;top:100px;right:14px;left:14px;width:auto;max-width:none;max-height:calc(100vh - 124px)}.native-compact-editor main{padding:12px 18px 60px}.native-compact-editor .tools{top:-12px}.native-compact-editor h1{font-size:22px}}
`;
  document.head.append(style);
  document.body.classList.add('native-compact-editor');
  return {applied: true, legacy: true};
})();
