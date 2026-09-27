// Collector view: every set with completion (#/sets) and one set's full checklist (#/sets/<code>). docs/SETUP.md.
import { h, $, clear, int, money, spinner, errorBox, store, debounce, titleCase, copyText, toast } from './util.js';
import { api } from './api.js';
import { lazyImg, rarityGem } from './cards.js';
import { openCard } from './detail.js';
import { coverageRing } from './build.js';
import { dialog } from './setup.js';

const PREFS_KEY = 'gallery.sets';
const prefs = { q: '', types: [], owned: true, sort: 'released', ...store.get(PREFS_KEY, {}) };
const PAGE_OPTS_KEY = 'gallery.setPage';
const pageOpts = { mode: 'printing', finish: 'any', variants: false, missingOnly: false, ...store.get(PAGE_OPTS_KEY, {}) };
const SORTS = [['released', 'Release date'], ['name', 'Name'], ['completion', 'Completion'], ['owned_value', 'Owned value']];
const CHUNK = 80;

let built = false;
let home, page, listBox, typeBox, countBox;
let listData = null;
let listToken = 0, pageToken = 0;
let sentinelObserver = null;

export function showSets(code) {
  const root = $('#view-sets');
  if (!built) {
    built = true;
    home = h('div.sets-home');
    page = h('div.set-page', { hidden: true });
    root.append(home, page);
    buildHome();
  }
  if (code) { home.hidden = true; page.hidden = false; showSetPage(code); }
  else { pageToken++; page.hidden = true; home.hidden = false; loadList(); }
}

export const setHref = code => `#/sets/${encodeURIComponent(String(code || '').toLowerCase())}`;
export function setIcon(icon, cls = 'set-icon') {
  return h('span', { class: cls, 'aria-hidden': 'true' }, icon ? h('img', { src: icon, alt: '', loading: 'lazy', onerror: e => e.target.remove() }) : null);
}
const fmtDate = d => (d ? new Date(d + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—');
const pct = p => `${Math.floor((p || 0) * 100)}%`;

// ---------- set list ----------
function save() { store.set(PREFS_KEY, prefs); }

function buildHome() {
  const search = h('input.text-input', { type: 'search', value: prefs.q, placeholder: 'Set name or code…', 'aria-label': 'Search sets' });
  search.addEventListener('input', debounce(() => { prefs.q = search.value.trim(); save(); loadList(); }, 300));
  const owned = h('input', { type: 'checkbox', checked: prefs.owned, onchange: e => { prefs.owned = e.target.checked; save(); loadList(); } });
  const sort = h('select.select', { 'aria-label': 'Sort sets', onchange: e => { prefs.sort = e.target.value; save(); loadList(); } },
    SORTS.map(([v, l]) => h('option', { value: v, selected: v === prefs.sort }, l)));
  typeBox = h('div.type-chips', { role: 'group', 'aria-label': 'Set types' });
  countBox = h('p.muted.small');
  listBox = h('div.set-rows');
  home.append(
    h('section.panel.sets-head',
      h('div.panel-head', h('h2', 'Sets'), h('div.panel-tools', search, sort, h('label.inline-label.check', owned, ' Only sets I own cards from'))),
      h('p.muted', 'Every paper set, with how much of it you own. Open one for the full checklist, owned cards in color and missing ones dimmed.'),
      typeBox, countBox),
    listBox);
}

function drawTypes(types) {
  clear(typeBox);
  const toggle = t => { prefs.types = prefs.types.includes(t) ? prefs.types.filter(x => x !== t) : [...prefs.types, t]; save(); loadList(); };
  typeBox.append(...types.map(t => h('button.mini-chip', { type: 'button', 'aria-pressed': String(prefs.types.includes(t)), onclick: () => toggle(t) }, titleCase(t))));
  if (prefs.types.length) typeBox.append(h('button.link-btn', { type: 'button', onclick: () => { prefs.types = []; save(); loadList(); } }, 'all types'));
}

async function loadList() {
  const mine = ++listToken;
  if (!listData) clear(listBox).append(h('div.panel', spinner('Gathering sets…')));
  let data;
  try { data = await api.sets.list({ q: prefs.q || null, types: prefs.types.join(',') || null, owned: prefs.owned ? 1 : null, sort: prefs.sort }); }
  catch (error) { if (mine === listToken) clear(listBox).append(errorBox(error.message)); return; }
  if (mine !== listToken) return;
  listData = data;
  drawTypes(data.set_types || []);
  renderList(data.sets || []);
}

/** Rows are appended in chunks as you scroll, so 1,000+ sets stay quick. */
function renderList(sets) {
  sentinelObserver?.disconnect();
  clear(listBox);
  const owned = sets.filter(s => s.owned).length;
  countBox.textContent = `${int(sets.length)} set${sets.length === 1 ? '' : 's'}${owned !== sets.length ? ` · you own cards from ${int(owned)}` : ''} · ${int(sets.filter(s => s.completion >= 1).length)} complete`;
  if (!sets.length) { listBox.append(h('div.chart-empty', 'No sets match these filters.')); return; }
  const byYear = prefs.sort === 'released';
  let shown = 0, lastYear = null;
  const sentinel = h('div.sentinel', { 'aria-hidden': 'true' });
  const more = () => {
    const frag = document.createDocumentFragment();
    for (const s of sets.slice(shown, shown + CHUNK)) {
      const year = (s.released_at || '').slice(0, 4);
      if (byYear && year !== lastYear) { frag.append(h('h3.year-head', year || 'Undated')); lastYear = year; }
      frag.append(setRow(s));
    }
    shown = Math.min(sets.length, shown + CHUNK);
    sentinel.before(frag);
    if (shown >= sets.length) { sentinelObserver?.disconnect(); sentinel.remove(); }
  };
  listBox.append(sentinel);
  more();
  sentinelObserver = new IntersectionObserver(entries => { if (entries.some(e => e.isIntersecting)) more(); }, { rootMargin: '800px 0px' });
  if (shown < sets.length) sentinelObserver.observe(sentinel);
}

function setRow(s) {
  const full = s.completion >= 1;
  return h('a', { class: 'set-line' + (s.owned ? '' : ' none'), href: setHref(s.code), 'aria-label': `${s.name}: ${s.owned} of ${s.cards} cards` },
    setIcon(s.icon),
    h('span.sl-name', h('b', s.name), h('span.muted.small', ` ${s.code.toUpperCase()} · ${titleCase(s.set_type)} · ${fmtDate(s.released_at)}`)),
    h('span.progress', { role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.floor(s.completion * 100), 'aria-label': 'Completion' },
      h('span.progress-fill', { class: full ? 'full' : null, style: { width: `${Math.min(100, s.completion * 100)}%` } })),
    h('span.sl-count', `${int(s.owned)}/${int(s.cards)}`, h('span.muted', ` ${pct(s.completion)}`)),
    h('span.sl-value', s.owned_value_usd ? money(s.owned_value_usd, { whole: s.owned_value_usd >= 100 }) : h('span.muted', '—')));
}

// ---------- set page ----------
async function showSetPage(code) {
  const mine = ++pageToken;
  clear(page).append(h('a.back-link', { href: '#/sets' }, '← All sets'), h('div.panel', spinner('Laying out the binder page…')));
  const params = { mode: pageOpts.mode, finish: pageOpts.finish, variants: pageOpts.variants ? 1 : 0 };
  let data;
  try { data = await api.sets.get(code, params); } catch (error) {
    if (mine === pageToken) clear(page).append(h('a.back-link', { href: '#/sets' }, '← All sets'), errorBox(error.message));
    return;
  }
  if (mine !== pageToken) return;
  renderSetPage(code, data, params);
  document.title = `${data.set.name} · Sets · Cardclops`;
}

function renderSetPage(code, data, params) {
  const s = data.set, t = data.totals || {};
  const reload = () => { store.set(PAGE_OPTS_KEY, pageOpts); showSetPage(code); };
  const seg = (label, key, options) => h('div.seg', { role: 'group', 'aria-label': label },
    options.map(([v, l]) => h('button.seg-btn', { type: 'button', class: pageOpts[key] === v ? 'on' : null, 'aria-pressed': String(pageOpts[key] === v), onclick: () => { pageOpts[key] = v; reload(); } }, l)));
  const variants = h('input', { type: 'checkbox', checked: pageOpts.variants, onchange: e => { pageOpts.variants = e.target.checked; reload(); } });
  const missingOnly = h('input', { type: 'checkbox', checked: pageOpts.missingOnly, onchange: e => { pageOpts.missingOnly = e.target.checked; store.set(PAGE_OPTS_KEY, pageOpts); drawGrid(); } });
  const missingUrl = api.sets.missingUrl(code, params);
  const rarities = ['common', 'uncommon', 'rare', 'mythic'].filter(r => t.by_rarity?.[r]).concat(Object.keys(t.by_rarity || {}).filter(r => !['common', 'uncommon', 'rare', 'mythic'].includes(r)));
  const grid = h('div.binder');
  const cards = data.cards || [];
  const nav = list => i => ({ index: i, count: () => list.length, get: j => Promise.resolve(list[j]), lightbox: true });
  const drawGrid = () => {
    const list = pageOpts.missingOnly ? cards.filter(c => c.counts && !isOwned(c)) : cards;
    clear(grid);
    if (!list.length) { grid.append(h('div.chart-empty', pageOpts.missingOnly ? 'Nothing missing under these filters — this set is complete!' : 'No cards.')); return; }
    grid.append(...list.map((c, i) => binderCard(c, () => openCard(c.scryfall_id, nav(list)(i)))));
  };
  const isOwned = c => (pageOpts.mode === 'card' ? c.owned + (c.owned_elsewhere || 0) : pageOpts.finish === 'foil' ? c.owned_foil : pageOpts.finish === 'nonfoil' ? c.owned_nonfoil : c.owned) > 0;

  clear(page).append(
    h('a.back-link', { href: '#/sets' }, '← All sets'),
    h('section.panel.set-head',
      h('div.sh-top',
        setIcon(s.icon, 'set-icon big'),
        h('div.sh-title', h('h1', s.name), h('p.muted', `${s.code.toUpperCase()} · ${titleCase(s.set_type)} · released ${fmtDate(s.released_at)} · ${int(s.cards)} cards${s.cards_with_variants > s.cards ? ` (${int(s.cards_with_variants)} with variants)` : ''}`)),
        coverageRing(t.completion, 84)),
      h('div.sh-body',
        h('div.bignums.sh-nums',
          h('div.bignum.c-lime', h('div.bn-value', `${int(t.owned)}/${int(t.cards)}`), h('div.bn-label', 'Owned')),
          h('div.bignum.c-pink', h('div.bn-value', int(t.missing)), h('div.bn-label', 'Missing')),
          h('div.bignum.c-yellow', h('div.bn-value', money(t.owned_value_usd, { whole: t.owned_value_usd >= 1000 })), h('div.bn-label', 'Owned value')),
          h('div.bignum.c-orange', h('div.bn-value', money(t.cost_to_complete_usd, { whole: t.cost_to_complete_usd >= 1000 })), h('div.bn-label', 'Cost to complete'))),
        h('div.rarity-bars', rarities.map(r => {
          const x = t.by_rarity[r];
          const share = x.cards ? x.owned / x.cards : 0;
          return h('div.rarity-bar', rarityGem(r), h('span.rb-label', titleCase(r)),
            h('span.progress', h('span.progress-fill', { class: share >= 1 ? 'full' : null, style: { width: `${share * 100}%`, background: share >= 1 ? null : `var(--r-${r}, var(--cyan))` } })),
            h('span.rb-count', `${int(x.owned)}/${int(x.cards)}`));
        }))),
      h('div.form-row.sh-controls',
        h('span.inline-label', 'Count ', seg('Completion mode', 'mode', [['printing', 'This printing'], ['card', 'Any printing']])),
        h('span.inline-label', 'Finish ', seg('Finish', 'finish', [['any', 'Any'], ['nonfoil', 'Nonfoil'], ['foil', 'Foil']])),
        h('label.inline-label.check', { title: 'Showcase, borderless, extended art, promos and other cards beyond the main set' }, variants, ' Include variants'),
        h('label.inline-label.check', missingOnly, ' Show missing only')),
      h('div.form-row',
        h('button.btn.small', { type: 'button', onclick: async () => {
          try {
            const r = await fetch(missingUrl);
            if (!r.ok) throw new Error(`Server answered ${r.status}`);
            const text = await r.text();
            const n = text.split('\n').filter(Boolean).length;
            if (await copyText(text)) toast(`Copied ${n} missing card${n === 1 ? '' : 's'}`);
            else {
              // The clipboard can refuse (the click's permission expires while the list loads): offer it to copy by hand.
              const area = h('textarea.deck-text', { readonly: true, 'aria-label': 'Missing cards' });
              area.value = text;
              dialog(`Missing from ${s.name} · ${n}`, h('div', h('p.small.muted', 'Press Ctrl+C to copy the selected list.'), area));
              area.focus(); area.select();
            }
          } catch (error) { toast('Could not copy: ' + error.message); }
        } }, '⧉ Copy missing list'),
        h('a.btn.small.ghost', { href: missingUrl, target: '_blank', rel: 'noopener' }, 'Missing list as text ↗'),
        h('a.btn.small.ghost', { href: `#/gallery?q=${encodeURIComponent('s:' + s.code)}` }, 'My cards from this set'))),
    h('section.panel.binder-panel', h('div.binder-legend.small.muted',
      h('span', h('span.legend-own'), ' owned'), h('span', h('span.legend-miss'), ' missing'), h('span', h('span.legend-skip'), ' not counted under these filters')), grid));
  drawGrid();
}

function binderCard(c, open) {
  const owned = c.owned > 0;
  const rawPrice = c.prices?.usd ?? c.prices?.usd_foil ?? c.prices?.usd_etched;
  const price = rawPrice == null ? null : Number(rawPrice);
  const label = `${c.name} #${c.collector_number}${owned ? `, you own ${c.owned}` : ', missing'}${c.counts ? '' : ', not counted'}`;
  return h('button', { type: 'button', class: 'bcard' + (owned ? ' own' : ' miss') + (c.counts ? '' : ' uncounted'), 'aria-label': label, title: label, onclick: open },
    h('span.bcard-face', lazyImg(c.image, '', 'card-img')),
    owned ? h('span.qty.small', '×' + int(c.owned)) : null,
    c.owned_foil ? h('span.bfoil', { title: `${c.owned_foil} foil` }, '✦') : null,
    !owned && c.owned_elsewhere ? h('span.belse', { title: `You own ${c.owned_elsewhere} in other printings` }, `+${c.owned_elsewhere} other`) : null,
    h('span.bcard-cap', h('span', `#${c.collector_number}`), rarityGem(c.rarity), h('span.bprice', price != null ? money(price) : '—')));
}
