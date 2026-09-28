// Gallery view: toolbar, answer strip, and a virtualised grid / list over paged search results.
import { fileDialog } from './binders.js';
import { h, $, clear, int, money, signedMoney, changeChip, manaCost, finishLabel, formatLabel, sortFormats, spinner, errorBox, signedClass } from './util.js';
import { handMark } from './addcards.js';
import { api } from './api.js';
import { cardTile, rarityGem, unobserve, attachHoverPreview, hideHoverPreview } from './cards.js';
import { breakdownPanels } from './breakdown.js';
import { showQueryInfo, renderChips } from './search.js';
import { openCard } from './detail.js';

const PAGE = 120;
const SORTS = [
  ['name', 'Name'], ['usd', 'Price'], ['value', 'Value (price × qty)'], ['change1', 'Change 1d'], ['change7', 'Change 7d'],
  ['change30', 'Change 30d'], ['gain', 'Gain vs paid'], ['qty', 'Quantity'], ['mv', 'Mana value'], ['color', 'Color'],
  ['rarity', 'Rarity'], ['set', 'Set'], ['released', 'Release date'], ['added', 'Date added'],
];
const LIST_COLUMNS = [
  ['Name', 'name'], ['Set', 'set'], ['Cost', 'mv'], ['Type', null], ['Qty', 'qty'], ['Finish', null],
  ['Price', 'usd'], ['Value', 'value'], ['7d', 'change7'], ['Gain', 'gain'], ['Used', null], ['Spare', null],
];

let ctx;            // {getState, setState}
let els = {};
let lastKey = null;
let current = null; // {store, view}
let generation = 0;
let controller = null;
let showAllFormats = false;
const PRIMARY_FORMATS = ['standard', 'pioneer', 'modern', 'legacy', 'vintage', 'pauper', 'commander', 'premodern', 'penny', 'historic', 'timeless', 'brawl', 'oathbreaker', 'paupercommander'];

export function initGallery(context) {
  ctx = context;
  els.strip = $('#answerStrip');
  els.toolbar = $('#toolbar');
  els.results = $('#results');
  buildToolbar();
}

/** Called whenever gallery state changes. Re-queries only when q/sort/dir/unique changed. */
export function updateGallery(state, { force = false } = {}) {
  syncToolbar(state);
  const key = JSON.stringify([state.q.trim(), state.sort, state.dir, state.unique]);
  if (key !== lastKey || force) {
    lastKey = key;
    search(state);
  } else if (current && current.view.mode !== state.view) {
    current.view.destroy();
    current.view = new VirtualResults(els.results, current.store, state.view);
  }
}

// ---------- toolbar ----------
function buildToolbar() {
  els.count = h('div.result-count', { 'aria-live': 'polite' });
  els.sort = h('select.select', { 'aria-label': 'Sort by', onchange: e => ctx.setState({ sort: e.target.value }) },
    SORTS.map(([value, label]) => h('option', { value }, label)));
  els.dir = h('button.btn.small.dir', { type: 'button', onclick: () => ctx.setState({ dir: ctx.getState().dir === 'asc' ? 'desc' : 'asc' }) });
  els.unique = segmented('Group results', [['prints', 'Prints'], ['cards', 'Cards']], v => ctx.setState({ unique: v }));
  els.view = segmented('Layout', [['grid', 'Grid'], ['list', 'List']], v => ctx.setState({ view: v }));
  els.file = h('button.btn.small.ghost', { type: 'button', hidden: true, title: 'Put every copy these results have in a binder (or take them out)',
    onclick: () => fileDialog({ title: 'Binders: these results', q: ctx.getState().q.trim() }) }, '📒 Put these in a binder…');
  els.toolbar.append(els.count, els.file, h('div.toolbar-controls', h('label.sort-label', h('span.sr-only', 'Sort'), els.sort), els.dir, els.unique, els.view));
}

function segmented(label, options, onPick) {
  return h('div.seg', { role: 'group', 'aria-label': label },
    options.map(([value, text]) => h('button.seg-btn', { type: 'button', dataset: { value }, onclick: () => onPick(value) }, text)));
}

function syncToolbar(state) {
  els.sort.value = state.sort;
  els.dir.textContent = state.dir === 'asc' ? '↑ Asc' : '↓ Desc';
  els.dir.setAttribute('aria-label', `Sort direction: ${state.dir === 'asc' ? 'ascending' : 'descending'}`);
  for (const [group, value] of [[els.unique, state.unique], [els.view, state.view]]) {
    for (const b of group.children) {
      b.classList.toggle('on', b.dataset.value === value);
      b.setAttribute('aria-pressed', String(b.dataset.value === value));
    }
  }
}

// ---------- searching ----------
async function search(state) {
  const mine = ++generation;
  controller?.abort();
  controller = new AbortController();
  const params = { q: state.q.trim(), sort: state.sort, dir: state.dir, unique: state.unique };
  els.results.classList.add('loading');
  clear(els.count).append(spinner('Searching…'));
  let first;
  try {
    first = await api.search({ ...params, offset: 0, limit: PAGE }, controller.signal);
  } catch (error) {
    if (error.name === 'AbortError' || mine !== generation) return;
    els.results.classList.remove('loading');
    clear(els.count);
    current?.view.destroy(); current = null;
    clear(els.results).append(errorBox(error.message));
    return;
  }
  if (mine !== generation) return;
  els.results.classList.remove('loading');
  showQueryInfo(first);
  renderChips();
  current?.view.destroy();
  current = null;
  if (first.error) {
    clear(els.count);
    clear(els.strip).hidden = true;
    clear(els.results).append(h('div.empty', h('div.blob-eye', { 'aria-hidden': 'true' }), h('p', 'Fix the query above and the cards will come back.')));
    return;
  }
  const store = new ResultStore(params, first);
  // A new result set starts at its top: if we were deep in the old grid, jump back to the strip.
  const stripTop = els.strip.getBoundingClientRect().top + scrollY - 90;
  if (scrollY > stripTop + 400) scrollTo({ top: Math.max(0, stripTop) });
  renderStrip(first, state);
  renderCount(store, state);
  if (!store.total) {
    clear(els.results).append(h('div.empty', h('div.blob-eye', { 'aria-hidden': 'true' }), h('p', 'No cards in your collection match that.')));
    return;
  }
  current = { store, view: new VirtualResults(els.results, store, state.view) };
}

function renderCount(store, state) {
  const noun = store.unit === 'cards' ? (store.total === 1 ? 'card' : 'cards') : (store.total === 1 ? 'print' : 'prints');
  clear(els.count).append(h('b', int(store.total)), ' ', noun, state.q.trim() ? '' : h('span.muted', ' — whole collection'));
  els.file.hidden = !state.q.trim() || !store.total;       // a search's results, not the whole collection
}

class ResultStore {
  constructor(params, first) {
    this.params = params;
    this.pages = new Map([[0, first.results]]);
    this.pending = new Map();
    this.listeners = new Set();
    this.onTotalChange = null;
    const q = params.q.toLowerCase();
    const cards = /(^|\s)unique:cards\b/.test(q) || (params.unique === 'cards' && !/(^|\s)unique:(prints|art)\b/.test(q));
    this.unit = cards ? 'cards' : 'prints';
    const t = first.totals || {};
    this.total = first.results.length < PAGE ? first.results.length : t.results ?? (cards ? t.unique_cards : t.rows) ?? first.results.length;
  }
  item(i) {
    const page = this.pages.get(Math.floor(i / PAGE));
    return page ? page[i % PAGE] : undefined;
  }
  load(pageIndex) {
    if (this.pages.has(pageIndex)) return Promise.resolve();
    if (this.pending.has(pageIndex)) return this.pending.get(pageIndex);
    const promise = api.search({ ...this.params, offset: pageIndex * PAGE, limit: PAGE })
      .then(response => {
        this.pages.set(pageIndex, response.results || []);
        const got = (response.results || []).length;
        if (got < PAGE && pageIndex * PAGE + got !== this.total) {
          this.total = pageIndex * PAGE + got;
          this.onTotalChange?.();
        }
        for (const fn of this.listeners) fn(pageIndex);
      })
      .catch(() => { /* leave the placeholders; scrolling retries */ })
      .finally(() => this.pending.delete(pageIndex));
    this.pending.set(pageIndex, promise);
    return promise;
  }
  async fetch(i) {
    if (i < 0 || i >= this.total) return undefined;
    await this.load(Math.floor(i / PAGE));
    return this.item(i);
  }
}

/** Windowed renderer: only tiles/rows near the viewport exist in the DOM. */
class VirtualResults {
  constructor(host, store, mode) {
    this.host = host;
    this.store = store;
    this.mode = mode;
    this.nodes = new Map();
    clear(host);
    host.classList.toggle('as-list', mode === 'list');
    this.canvas = h('div.vcanvas');
    if (mode === 'list') {
      const state = ctx.getState();
      this.header = h('div.lrow.lhead', LIST_COLUMNS.map(([label, sort]) => sort
        ? h('button.lh', { type: 'button', class: state.sort === sort ? 'on' : null, onclick: () => ctx.setState(state.sort === sort ? { dir: state.dir === 'asc' ? 'desc' : 'asc' } : { sort }) },
          label, state.sort === sort ? (state.dir === 'asc' ? ' ↑' : ' ↓') : '')
        : h('span.lh', label)));
      host.append(h('div.list-wrap', this.header, this.canvas));
    } else {
      host.append(this.canvas);
    }
    this.frame = 0;
    this.schedule = () => { if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; this.render(); }); };
    window.addEventListener('scroll', this.schedule, { passive: true });
    this.resizer = new ResizeObserver(() => { if (this.layout()) this.render(); });
    this.resizer.observe(this.canvas);
    this.onPage = () => this.fill();
    store.listeners.add(this.onPage);
    store.onTotalChange = () => { this.layout(true); this.render(); renderCount(store, ctx.getState()); };
    this.layout(true);
    this.render();
  }

  layout(force = false) {
    const width = this.canvas.clientWidth;
    if (!force && width === this.width) return false;
    this.width = width;
    if (this.mode === 'list') {
      this.cols = 1;
      this.rowH = 46;
      this.tileW = width;
    } else {
      const narrow = width < 560;
      this.gap = narrow ? 10 : 18;
      const minW = narrow ? 142 : 186;
      this.cols = Math.max(1, Math.floor((width + this.gap) / (minW + this.gap)));
      this.tileW = (width - this.gap * (this.cols - 1)) / this.cols;
      const chrome = 2 * (3 + 6);                      // border + padding each side
      const imageH = (this.tileW - chrome) * (680 / 488);
      this.tileH = Math.round(imageH + chrome + (narrow ? 50 : 54));
      this.rowH = this.tileH + this.gap;
      this.canvas.style.setProperty('--tile-w', this.tileW + 'px');
      this.canvas.style.setProperty('--tile-h', this.tileH + 'px');
    }
    this.rows = Math.ceil(this.store.total / this.cols);
    this.canvas.style.height = Math.max(0, this.rows * this.rowH - (this.gap || 0)) + 'px';
    for (const [i] of this.nodes) this.drop(i);
    return true;
  }

  position(node, i) {
    const row = Math.floor(i / this.cols), col = i % this.cols;
    node.style.transform = this.mode === 'list' ? `translateY(${row * this.rowH}px)` : `translate(${col * (this.tileW + this.gap)}px, ${row * this.rowH}px)`;
  }

  range() {
    const top = this.canvas.getBoundingClientRect().top;
    const buffer = innerHeight * 0.8;
    const firstRow = Math.max(0, Math.floor((-top - buffer) / this.rowH));
    const lastRow = Math.min(this.rows - 1, Math.floor((-top + innerHeight + buffer) / this.rowH));
    return [firstRow * this.cols, Math.min(this.store.total - 1, (lastRow + 1) * this.cols - 1)];
  }

  render() {
    if (!this.canvas.isConnected || !this.store.total) return;
    const [first, last] = this.range();
    for (const [i] of this.nodes) if (i < first || i > last) this.drop(i);
    if (last < first) return;
    for (let p = Math.floor(first / PAGE); p <= Math.floor(last / PAGE); p++) this.store.load(p);
    const fragment = document.createDocumentFragment();
    for (let i = first; i <= last; i++) {
      if (this.nodes.has(i)) continue;
      const node = this.make(i);
      this.position(node, i);
      this.nodes.set(i, node);
      fragment.append(node);
    }
    this.canvas.append(fragment);
  }

  fill() {
    for (const [i, node] of this.nodes) {
      if (!node.classList.contains('skeleton') || !this.store.item(i)) continue;
      const fresh = this.make(i);
      this.position(fresh, i);
      node.replaceWith(fresh);
      this.nodes.set(i, fresh);
    }
    this.render();
  }

  drop(i) {
    const node = this.nodes.get(i);
    if (!node) return;
    unobserve(node.img);
    node.remove();
    this.nodes.delete(i);
  }

  make(i) {
    const card = this.store.item(i);
    if (!card) return h(this.mode === 'list' ? 'div.lrow.skeleton' : 'div.tile.skeleton', { 'aria-hidden': 'true' });
    const open = () => openCard(card.scryfall_id, { index: i, count: () => this.store.total, get: j => this.store.fetch(j), onClose: j => this.reveal(j) });
    return this.mode === 'list' ? listRow(card, open) : cardTile(card, { onOpen: open });
  }

  /** Scroll so result i is visible (after moving through results in the detail view). */
  reveal(i) {
    if (i == null || !this.canvas.isConnected) return;
    const top = this.canvas.getBoundingClientRect().top + scrollY + Math.floor(i / this.cols) * this.rowH;
    if (top < scrollY + 80 || top + this.rowH > scrollY + innerHeight) scrollTo({ top: top - innerHeight / 3 });
  }

  destroy() {
    window.removeEventListener('scroll', this.schedule);
    cancelAnimationFrame(this.frame);
    this.resizer.disconnect();
    this.store.listeners.delete(this.onPage);
    for (const [i] of this.nodes) this.drop(i);
    hideHoverPreview();
  }
}

function listRow(card, open) {
  const paid = card.purchase_price;
  const gain = paid != null && card.price_usd != null ? (card.price_usd - paid) * card.quantity : null;
  const row = h('div.lrow', { role: 'button', tabindex: '0', 'aria-label': `${card.name}, ${card.set_code}`, onclick: open,
    onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } } },
    h('span.c-name', rarityGem(card.rarity), h('span.nm', card.name), handMark(card.source), card.finish !== 'normal' ? h('span.foil-mark', { title: finishLabel(card.finish) }, '✦') : null),
    h('span.c-set', { title: card.set_name }, h('a.set-link', { href: `#/sets/${encodeURIComponent((card.set_code || '').toLowerCase())}`, onclick: e => e.stopPropagation(), tabindex: '-1' }, (card.set_code || '').toUpperCase()), h('span.muted', ' #' + (card.collector_number || ''))),
    h('span.c-cost', manaCost(card.mana_cost)),
    h('span.c-type', { title: card.type_line }, card.type_line),
    h('span.c-num', int(card.quantity)),
    h('span.c-fin', finishLabel(card.finish)),
    h('span.c-num', money(card.price_usd)),
    h('span.c-num', money(card.value_usd)),
    h('span.c-num', changeChip(card.change?.d7, { small: true })),
    h('span.c-num', { class: 'gain ' + signedClass(gain) }, gain == null ? '—' : signedMoney(gain)),
    h('span.c-num', { class: card.used ? 'used-num' : 'muted', title: 'Copies in active decks' }, card.used ? int(card.used) : '—'),
    h('span.c-num', { title: 'Copies not in any active deck' }, card.spare == null ? '—' : int(card.spare)));
  attachHoverPreview(row, card);
  return row;
}

// ---------- answer strip ----------
function renderStrip(response, state) {
  const strip = clear(els.strip);
  strip.hidden = false;
  const t = response.totals || {};
  strip.append(h('div.bignums',
    bigNumber('Copies', int(t.copies), 'pink'),
    bigNumber('Unique cards', int(t.unique_cards), 'lime'),
    bigNumber('Printings', int(t.printings), 'cyan'),
    bigNumber('Value', money(t.value_usd, { whole: t.value_usd >= 1000 }), 'yellow')));

  const legality = response.legality || {};
  const formats = sortFormats(Object.keys(legality).filter(f => legality[f] && legality[f].cards > 0));
  const unique = t.unique_cards || 1;
  const q = state.q;
  if (formats.length) {
    const isOn = f => new RegExp(`(^|\\s)(f|format|legal):${f}(\\s|$)`, 'i').test(q);
    const chip = f => {
      const on = isOn(f);
      const pct = Math.round((legality[f].cards / unique) * 100);
      return h('button.fchip', { type: 'button', class: on ? 'on' : null, 'aria-pressed': String(on),
        title: `${int(legality[f].cards)} of ${int(unique)} cards (${int(legality[f].copies)} copies) are legal in ${formatLabel(f)}. Click to ${on ? 'remove' : 'add'} f:${f}.`,
        onclick: () => toggleToken(`f:${f}`, new RegExp(`(^|\\s)(f|format|legal):${f}(?=\\s|$)`, 'ig')) },
      h('span.fchip-fill', { style: { width: pct + '%' } }),
      h('span.fchip-name', formatLabel(f)), h('b', int(legality[f].cards)));
    };
    const main = formats.filter(f => PRIMARY_FORMATS.includes(f) || isOn(f));
    const rest = formats.filter(f => !main.includes(f));
    const box = h('div.format-chips', main.map(chip));
    if (rest.length) {
      const more = h('button.fchip.more', { type: 'button', 'aria-expanded': 'false', onclick: () => {
        more.replaceWith(...rest.map(chip));
        showAllFormats = true;
      } }, `+${rest.length} more`);
      if (showAllFormats) box.append(...rest.map(chip)); else box.append(more);
    }
    strip.append(h('div.formats', h('span.formats-label', 'Legal in'), box));
  }
  if (response.breakdown) {
    const details = h('details.breakdown', { open: sessionStorageGet('bd-open', innerWidth < 700 ? '0' : '1') === '1' },
      h('summary', 'Breakdown of these cards'),
      h('div.bd-grid', breakdownPanels(response.breakdown, { onPick: (kind, key) => {
        if (kind === 'color') addToken(`c:${key.toLowerCase()}`);
        if (kind === 'type') addToken(`t:${key.toLowerCase()}`);
      } })));
    details.addEventListener('toggle', () => sessionStorageSet('bd-open', details.open ? '1' : '0'));
    strip.append(details);
  }
}

function bigNumber(label, value, color) {
  return h('div', { class: `bignum c-${color}` }, h('div.bn-value', value), h('div.bn-label', label));
}

function addToken(token) {
  const q = ctx.getState().q.trim();
  if (new RegExp(`(^|\\s)${token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(\\s|$)`, 'i').test(q)) return;
  ctx.setState({ q: (q + ' ' + token).trim() }, { push: true, record: true });
}

function toggleToken(token, pattern) {
  const q = ctx.getState().q;
  if (pattern.test(q)) ctx.setState({ q: q.replace(pattern, ' ').replace(/\s+/g, ' ').trim() }, { push: true, record: true });
  else addToken(token);
}

function sessionStorageGet(key, fallback) { try { return sessionStorage.getItem(key) ?? fallback; } catch { return fallback; } }
function sessionStorageSet(key, value) { try { sessionStorage.setItem(key, value); } catch { /* ignore */ } }
