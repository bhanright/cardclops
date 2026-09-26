// Dashboard: portfolio value, movers, breakdowns, acquisitions, gain, set completion, legality feed.
import { h, $, clear, int, money, moneyShort, signedMoney, signedClass, changeChip, spinner, errorBox, formatLabel, titleCase, store } from './util.js';
import { renderRadarPanel } from './radar.js';
import { api } from './api.js';
import { timeSeriesPanel, columnChart } from './charts.js';
import { breakdownPanels } from './breakdown.js';
import { thumb, lazyImg, rarityGem } from './cards.js';
import { openCard } from './detail.js';

let hooks;
let built = false;
let loadedAt = 0;
const panels = {};
const moverState = { window: 7, min_price: 1, ...store.get('gallery.movers', {}) };

export function initDashboard(context) { hooks = context; }

/** Build on first visit; refresh data if the tab is revisited after a while. */
export function showDashboard() {
  const root = $('#view-dashboard');
  if (!built) {
    built = true;
    const panel = (key, title, cls = '', extra = null) => {
      panels[key] = h('div.panel-body');
      return h('section', { class: `panel dash-${key} ${cls}` }, h('div.panel-head', h('h2', title), extra), panels[key]);
    };
    panels.moverControls = h('div.panel-tools');
    panels.monthControls = h('div.panel-tools');
    panels.setControls = h('div.panel-tools');
    root.append(
      h('div.dash-kpis', { id: 'dashKpis' }),
      h('div.dash-grid',
        panel('portfolio', 'Portfolio value', 'wide'),
        panel('gain', 'Paid vs. now'),
        panel('decks', 'Decks'),
        panel('radar', 'Reprint radar'),
        panel('movers', 'Top movers', 'wide', panels.moverControls),
        panel('legality', 'Legality changes affecting your cards'),
        panel('top', 'Most valuable cards', 'full'),
        panel('breakdown', 'Whole collection', 'full'),
        panel('months', 'Acquisitions by month', 'full', panels.monthControls),
        panel('sets', 'Set completion', 'full', panels.setControls)));
  }
  if (Date.now() - loadedAt > 5 * 60 * 1000) {
    loadedAt = Date.now();
    loadAll();
  }
}

function busy(node, label = 'Loading…') { clear(node).append(spinner(label)); }
function fail(node, error) { clear(node).append(errorBox(error.message)); }

function loadAll() {
  const kpis = $('#dashKpis');
  busy(kpis);
  for (const key of ['portfolio', 'gain', 'decks', 'movers', 'legality', 'top', 'breakdown', 'months', 'sets']) busy(panels[key]);

  renderRadarPanel(panels.radar);
  api.summary().then(renderKpis, e => { fail(kpis, e); fail(panels.decks, e); });
  api.portfolio().then(renderPortfolio, e => fail(panels.portfolio, e));
  loadMovers();
  api.legalityChanges().then(renderLegality, e => fail(panels.legality, e));
  api.search({ q: '', limit: 1 }).then(r => renderBreakdown(r), e => fail(panels.breakdown, e));
  api.stats().then(stats => {
    renderGain(stats.gain || {});
    renderTop(stats.top_value || []);
    renderMonths(stats.added_by_month || []);
    renderSets(stats.sets || []);
  }, error => { for (const key of ['gain', 'top', 'months', 'sets']) fail(panels[key], error); });
}

function kpi(label, value, color, sub) {
  return h('div', { class: `bignum c-${color}` }, h('div.bn-value', value), h('div.bn-label', label), sub ? h('div.bn-sub', sub) : null);
}

function renderDecks(d) {
  const box = clear(panels.decks);
  if (!d || (!d.active && !d.inactive)) {
    box.append(h('div.chart-empty', 'No decks yet. ', h('a', { href: '#/decks' }, 'Import one'), ' to see which cards they use.'));
    return;
  }
  box.append(
    h('div.deck-kpis',
      h('div', h('b', int(d.active)), ' active'), h('div', h('b', int(d.inactive)), ' inactive'),
      h('div', h('b', int(d.copies_in_decks)), ' copies in decks'),
      h('div', { class: d.conflicts ? 'conflict' : '' }, h('b', int(d.conflicts)), d.conflicts ? ' ⚠ conflicts' : ' conflicts')),
    d.conflicts ? h('p.small.muted', 'A conflict is a deck line short of copies because another active deck holds them.') : '',
    h('div.gain-links', h('a.btn.small', { href: '#/decks' }, 'Open decks →'),
      h('button.btn.small.ghost', { type: 'button', onclick: () => hooks.search('-deck:any usd>5', { sort: 'usd', dir: 'desc' }) }, 'Valuable cards in no deck')));
}

function renderKpis(s) {
  renderDecks(s.decks);
  const kpis = clear($('#dashKpis'));
  const gain = s.purchase_total_usd != null ? s.value_usd - s.purchase_total_usd : null;
  const ph = s.price_history || {};
  kpis.append(
    kpi('Collection value', money(s.value_usd, { whole: true }), 'yellow'),
    kpi('Copies', int(s.copies), 'pink'),
    kpi('Unique cards', int(s.unique_cards), 'lime'),
    kpi('Printings', int(s.printings), 'cyan'),
    kpi('Sets', int(s.sets), 'violet'),
    h('div.kpi-note',
      h('div', h('b', 'Price history: '), ph.first_day ? `${ph.first_day} → ${ph.last_day}, daily for the last ${ph.daily_days ?? 30} days and monthly closes before` : 'none yet',
        ph.sources?.length ? h('span.muted', ' · ' + ph.sources.map(s => (typeof s === 'string' ? s : s.label || s.source)).join(', ')) : null),
      s.scryfall_updated_at ? h('div.muted', 'Scryfall data from ' + s.scryfall_updated_at.slice(0, 10)) : null,
      gain != null ? h('div', 'Everything you logged a price for: ', h('span', { class: 'gain ' + signedClass(gain) }, signedMoney(gain, { whole: true })), ' vs. paid') : null));
}

function renderPortfolio(data) {
  const box = clear(panels.portfolio);
  const points = data.points || [];
  const chart = h('div');
  box.append(chart);
  timeSeriesPanel(chart, { series: [{ label: 'Collection value', points }], height: 230,
    format: v => money(v, { whole: true }), emptyNote: 'Portfolio history starts today; it grows each refresh.' });
  if (points.length > 1) {
    const first = points[0][1], last = points.at(-1)[1];
    box.prepend(h('div.panel-sub', 'Since ', points[0][0], ': ', h('span', { class: 'gain ' + signedClass(last - first) }, signedMoney(last - first, { whole: true })), ' ',
      changeChip(first ? ((last - first) / first) * 100 : null, { small: true })));
  }
  if (data.note) box.append(h('p.muted.small', data.note));
}

// ---------- movers ----------
function loadMovers() {
  const controls = clear(panels.moverControls);
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Window' }, [1, 7, 30].map(w =>
    h('button.seg-btn', { type: 'button', class: moverState.window === w ? 'on' : null, 'aria-pressed': String(moverState.window === w),
      onclick: () => { moverState.window = w; saveMovers(); loadMovers(); } }, `${w}d`)));
  const min = h('input.num-input', { type: 'number', min: 0, step: 0.5, value: moverState.min_price, 'aria-label': 'Minimum price in dollars' });
  min.addEventListener('change', () => { moverState.min_price = Math.max(0, +min.value || 0); saveMovers(); loadMovers(); });
  controls.append(seg, h('label.inline-label', 'min $', min));
  busy(panels.movers);
  api.movers({ window: moverState.window, min_price: moverState.min_price, limit: 10 }).then(renderMovers, e => fail(panels.movers, e));
}
function saveMovers() { store.set('gallery.movers', moverState); }

function renderMovers(data) {
  const box = clear(panels.movers);
  const gainers = data.gainers || [], losers = data.losers || [];
  if (!gainers.length && !losers.length) {
    box.append(h('div.chart-empty', `No price moves over ${data.window} day${data.window === 1 ? '' : 's'} yet — movers appear once there are two days of prices ${data.window} days apart.`));
    return;
  }
  if (data.from_day) box.append(h('p.panel-sub', `${data.from_day} → ${data.to_day}`));
  const list = (title, items, cls) => h('div', { class: `mover-col ${cls}` }, h('h3', title), items.length ? h('ol.movers', items.map((m, i) => h('li',
    h('button.mover', { type: 'button', onclick: () => openCard(m.scryfall_id, navOver(items, i)) },
      h('span.mv-thumb', lazyImg(m.image.replace(/\/normal$/, '/art_crop'), '', 'mv-img')),
      h('span.mv-name', h('b', m.name), h('span.muted', ` ${(m.set_code || '').toUpperCase()}${m.finish !== 'normal' ? ' ✦' : ''}${m.quantity > 1 ? ' ×' + m.quantity : ''}`)),
      h('span.mv-prices', money(m.old_price), ' → ', h('b', money(m.new_price))),
      changeChip(m.change_pct),
      h('span', { class: 'mv-total gain ' + signedClass(m.change_total_usd), title: 'Change across the copies you hold' }, signedMoney(m.change_total_usd)))))) : h('p.muted', 'Nothing here.'));
  box.append(h('div.mover-cols', list('▲ Gainers', gainers, 'gainers'), list('▼ Losers', losers, 'losers')));
}

export function navOver(items, index) {
  return { index, count: () => items.length, get: i => Promise.resolve(items[i]) };
}

// ---------- legality ----------
function renderLegality(changes) {
  const box = clear(panels.legality);
  if (!changes.length) { box.append(h('div.chart-empty', 'No bans, unbans or rotations have touched your cards since tracking began.')); return; }
  const cls = s => (s === 'banned' ? 'down' : s === 'legal' ? 'up' : s === 'restricted' ? 'warn' : 'flat');
  box.append(h('ul.feed', changes.slice(0, 40).map(c => h('li',
    h('span.feed-day', c.day),
    h('button.link-btn', { type: 'button', onclick: () => hooks.search(`!"${c.name}"`) }, c.name),
    h('span.muted', ` in ${formatLabel(c.format)}: `),
    h('span', { class: 'status ' + cls(c.old_status) }, titleCase(c.old_status || 'new')), ' → ',
    h('span', { class: 'status ' + cls(c.new_status) }, titleCase(c.new_status)),
    c.copies ? h('span.muted', ` · you hold ${int(c.copies)}`) : null))));
}

// ---------- gain ----------
function renderGain(gain) {
  const box = clear(panels.gain);
  const paid = gain.purchase_total_usd, now = gain.current_total_usd;
  if (paid == null || !gain.priced_rows) { box.append(h('div.chart-empty', 'No purchase prices recorded in the ManaBox export.')); return; }
  const diff = now - paid;
  const pct = paid ? (diff / paid) * 100 : null;
  const max = Math.max(paid, now) || 1;
  box.append(
    h('div.gain-big', { class: signedClass(diff) }, signedMoney(diff, { whole: Math.abs(diff) >= 1000 }), ' ', changeChip(pct)),
    h('div.gain-bars',
      h('div.gbar', h('span.gbar-label', 'Paid'), h('span.bar-track', h('span.bar-fill', { style: { width: (paid / max) * 100 + '%', background: 'var(--violet)' } })), h('b', money(paid, { whole: true }))),
      h('div.gbar', h('span.gbar-label', 'Now'), h('span.bar-track', h('span.bar-fill', { style: { width: (now / max) * 100 + '%', background: diff >= 0 ? 'var(--lime)' : 'var(--pink)' } })), h('b', money(now, { whole: true })))),
    h('p.muted.small', `Across ${int(gain.priced_rows)} holdings rows that have a purchase price.`),
    h('div.gain-links',
      h('button.btn.small', { type: 'button', onclick: () => hooks.search('gain>0', { sort: 'gain', dir: 'desc' }) }, '▲ Biggest winners'),
      h('button.btn.small.ghost', { type: 'button', onclick: () => hooks.search('gain<0', { sort: 'gain', dir: 'asc' }) }, '▼ Biggest losers')));
}

// ---------- top value ----------
function renderTop(cards) {
  const box = clear(panels.top);
  if (!cards.length) { box.append(h('p.muted', 'No priced cards.')); return; }
  box.append(h('div.thumb-row.big', cards.map((c, i) => thumb(c, { onOpen: () => openCard(c.scryfall_id, navOver(cards, i)), caption: [money(c.price_usd)] }))));
}

// ---------- breakdown ----------
function renderBreakdown(response) {
  const box = clear(panels.breakdown);
  box.append(h('div.bd-grid.large', breakdownPanels(response.breakdown, { large: true, onPick: (kind, key) => {
    if (kind === 'color') hooks.search(`c:${key.toLowerCase()}`);
    if (kind === 'type') hooks.search(`t:${key.toLowerCase()}`);
  } })));
}

// ---------- acquisitions ----------
let monthMetric = 'copies';
function renderMonths(months) {
  const controls = clear(panels.monthControls);
  const draw = () => {
    for (const b of controls.querySelectorAll('.seg-btn')) b.classList.toggle('on', b.dataset.m === monthMetric);
    const box = clear(panels.months);
    if (!months.length) { box.append(h('div.chart-empty', 'No added-at dates in the export.')); return; }
    const recent = months.slice(-36);
    const items = recent.map(([month, copies, value]) => ({
      label: month.slice(2).replace('-', '/'), value: monthMetric === 'copies' ? copies : value,
      tip: monthMetric === 'copies' ? `${month}: ${money(value)} of cards at today’s prices` : `${month}: ${int(copies)} copies`,
      color: monthMetric === 'copies' ? 'var(--pink)' : 'var(--yellow)',
    }));
    box.append(columnChart(items, { height: 170, format: monthMetric === 'copies' ? v => int(v) + ' copies' : v => money(v), labelEvery: Math.ceil(items.length / 12) }));
    if (months.length > recent.length) box.append(h('p.muted.small', `Last 36 of ${months.length} months.`));
  };
  controls.append(h('div.seg', { role: 'group', 'aria-label': 'Metric' },
    h('button.seg-btn', { type: 'button', dataset: { m: 'copies' }, onclick: () => { monthMetric = 'copies'; draw(); } }, 'Copies'),
    h('button.seg-btn', { type: 'button', dataset: { m: 'value' }, onclick: () => { monthMetric = 'value'; draw(); } }, 'Value')));
  draw();
}

// ---------- sets ----------
const SET_SORTS = { completion: 'Completion', value: 'Value', copies: 'Copies', released: 'Newest', name: 'Name' };
function renderSets(sets) {
  let sortKey = store.get('gallery.setsort', 'completion');
  let filter = '';
  let showAll = false;
  const completion = s => (s.set_size ? s.owned_unique / s.set_size : 0);
  const controls = clear(panels.setControls);
  const search = h('input.text-input', { type: 'search', placeholder: 'Filter sets…', 'aria-label': 'Filter sets' });
  const sortSelect = h('select.select', { 'aria-label': 'Sort sets' }, Object.entries(SET_SORTS).map(([k, v]) => h('option', { value: k, selected: k === sortKey }, v)));
  controls.append(search, sortSelect);
  search.addEventListener('input', () => { filter = search.value.trim().toLowerCase(); draw(); });
  sortSelect.addEventListener('change', () => { sortKey = sortSelect.value; store.set('gallery.setsort', sortKey); draw(); });

  const draw = () => {
    const box = clear(panels.sets);
    const sorters = {
      completion: (a, b) => completion(b) - completion(a) || b.owned_unique - a.owned_unique,
      value: (a, b) => b.value_usd - a.value_usd, copies: (a, b) => b.copies - a.copies,
      released: (a, b) => (b.released_at || '').localeCompare(a.released_at || ''), name: (a, b) => a.name.localeCompare(b.name),
    };
    const list = sets.filter(s => !filter || s.name.toLowerCase().includes(filter) || s.code.toLowerCase() === filter).sort(sorters[sortKey]);
    const shown = showAll || filter ? list : list.slice(0, 24);
    const complete = sets.filter(s => s.set_size && s.owned_unique >= s.set_size).length;
    box.append(h('p.panel-sub', `${int(sets.length)} sets · ${int(complete)} complete`));
    box.append(h('div.set-list', shown.map(s => {
      const pct = s.set_size ? Math.min(100, (s.owned_unique / s.set_size) * 100) : null;
      return h('button.set-row', { type: 'button', title: `Open the ${s.name} checklist`, onclick: () => { location.hash = `#/sets/${encodeURIComponent(s.code)}`; } },
        h('span.set-icon', s.icon ? h('img', { src: s.icon, alt: '', loading: 'lazy', onerror: e => { e.target.remove(); } }) : null),
        h('span.set-name', h('b', s.name), h('span.muted', ` ${s.code.toUpperCase()}${s.released_at ? ' · ' + s.released_at.slice(0, 4) : ''}`)),
        h('span.progress', { role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': pct == null ? null : Math.round(pct), 'aria-label': `${s.name} completion` },
          h('span.progress-fill', { class: pct >= 100 ? 'full' : null, style: { width: (pct ?? 0) + '%' } })),
        h('span.set-count', s.set_size ? `${int(s.owned_unique)}/${int(s.set_size)}` : int(s.owned_unique), pct != null ? h('span.muted', ` ${Math.floor(pct)}%`) : null),
        h('span.set-value', money(s.value_usd, { whole: s.value_usd >= 100 })));
    })));
    if (!showAll && !filter && list.length > shown.length) {
      box.append(h('button.btn.small.show-more', { type: 'button', onclick: () => { showAll = true; draw(); } }, `Show all ${int(list.length)} sets`));
    }
  };
  draw();
}

export { rarityGem, moneyShort };
