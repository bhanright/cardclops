// Reprint radar: cards you own that are being reprinted soon (or were recently), most valuable first.
// A full view (#/radar) and a compact Dashboard panel. Data: GET /api/reprints (docs/TOOLS.md).
import { h, $, clear, int, money, changeChip, spinner, errorBox, store, finishLabel } from './util.js';
import { api, resize } from './api.js';
import { lazyImg, attachHoverPreview } from './cards.js';
import { openCard } from './detail.js';

const PREFS_KEY = 'gallery.radar';
const prefs = { impact: 'all', min_value: 1, spareOnly: false, ...store.get(PREFS_KEY, {}) };
const IMPACT_TIP = {
  wide: 'Wide reprint: the new printing is in a set opened in quantity (expansion, core, masters, commander…). Wide reprints add real supply and tend to lower the price of copies you already hold.',
  limited: 'Limited reprint: Secret Lair, promos, masterpieces or The List. Small print runs rarely move the price of existing copies.',
};
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

let built = false;
let body, controls, totalsBox;
let data = null;
let loadedAt = 0;

const dayMs = 86400000;
const parseDay = d => Date.parse(d + 'T00:00:00Z');
export function shortDate(day) {
  if (!day) return '—';
  const d = new Date(parseDay(day));
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}
/** "in 6 days", "tomorrow", "today", "3 days ago" relative to the server's as_of day. */
export function countdown(day, asOf) {
  const n = Math.round((parseDay(day) - parseDay(asOf)) / dayMs);
  if (n === 0) return 'today';
  if (n === 1) return 'tomorrow';
  if (n === -1) return 'yesterday';
  return n > 0 ? `in ${n} days` : `${-n} days ago`;
}

export function impactBadge(impact) {
  return h('span', { class: 'impact ' + impact, title: IMPACT_TIP[impact] || '' }, impact === 'wide' ? '▼ Wide' : '○ Limited');
}

// ---------- full view ----------
export function showRadar() {
  const root = $('#view-radar');
  if (!built) {
    built = true;
    controls = h('div.panel-tools');
    totalsBox = h('div.radar-totals');
    body = h('div.radar-body');
    root.append(
      h('section.panel.radar-head',
        h('div.panel-head', h('h2', 'Reprint radar'), controls),
        h('p.muted', 'Cards you own that are being reprinted. More supply usually means lower prices around release, so this is the time to sell spares or hold off buying. ',
          h('span', { class: 'impact wide', title: IMPACT_TIP.wide }, '▼ Wide'), ' reprints are the ones that move prices.'),
        totalsBox),
      body);
    buildControls();
  }
  if (!data || Date.now() - loadedAt > 10 * 60 * 1000) load();
  else render();
}

function buildControls() {
  clear(controls);
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Impact' },
    [['all', 'All reprints'], ['wide', 'Wide only']].map(([v, l]) => h('button.seg-btn', { type: 'button', class: prefs.impact === v ? 'on' : null,
      'aria-pressed': String(prefs.impact === v), onclick: () => { prefs.impact = v; save(); buildControls(); render(); } }, l)));
  const min = h('input.num-input', { type: 'number', min: 0, step: 1, value: prefs.min_value, 'aria-label': 'Minimum value held in dollars' });
  min.addEventListener('change', () => { prefs.min_value = Math.max(0, +min.value || 0); save(); load(); });
  const spare = h('input', { type: 'checkbox', checked: prefs.spareOnly, onchange: e => { prefs.spareOnly = e.target.checked; save(); render(); } });
  controls.append(seg, h('label.inline-label', 'min $', min),
    h('label.inline-label.check', { title: 'Hide cards whose copies are all in active decks' }, spare, ' Only cards with spare copies'));
}
function save() { store.set(PREFS_KEY, prefs); }

async function load() {
  clear(body).append(h('div.panel', spinner('Scanning upcoming sets…')));
  try {
    data = await api.reprints({ min_value: prefs.min_value });
    loadedAt = Date.now();
  } catch (error) {
    clear(body).append(errorBox(error.message));
    return;
  }
  render();
}

const visible = list => (list || []).filter(r => (prefs.impact === 'all' || r.impact === 'wide') && (!prefs.spareOnly || r.spare > 0));

function nextRelease(d) {
  const days = (d.upcoming || []).flatMap(r => (r.reprints || []).map(x => x.released_at)).filter(x => x && x > d.as_of).sort();
  return days[0] || null;
}

function render() {
  if (!data) return;
  const t = data.totals || {};
  const next = nextRelease(data);
  clear(totalsBox).append(h('div.bignums',
    h('div.bignum.c-orange', h('div.bn-value', int(t.upcoming_cards ?? 0)), h('div.bn-label', 'Cards being reprinted')),
    h('div.bignum.c-yellow', h('div.bn-value', money(t.upcoming_value_usd ?? 0, { whole: (t.upcoming_value_usd ?? 0) >= 1000 })), h('div.bn-label', 'Value at stake')),
    h('div.bignum.c-pink', { title: IMPACT_TIP.wide }, h('div.bn-value', money(t.upcoming_wide_value_usd ?? 0, { whole: (t.upcoming_wide_value_usd ?? 0) >= 1000 })), h('div.bn-label', 'In wide reprints')),
    h('div.bignum.c-cyan', h('div.bn-value', next ? shortDate(next) : '—'), h('div.bn-label', next ? `Next release · ${countdown(next, data.as_of)}` : 'Next release')),
    h('div.bignum.c-violet', h('div.bn-value', money(t.recent_value_usd ?? 0, { whole: (t.recent_value_usd ?? 0) >= 1000 })), h('div.bn-label', `Reprinted lately · ${int(t.recent_cards ?? 0)}`))));
  const upcoming = visible(data.upcoming);
  const recent = visible(data.recent);
  clear(body).append(
    section('Upcoming reprints', 'up', upcoming, 'Nothing you own (at this value) is in an announced set. Enjoy the calm.'),
    section('Recently reprinted', 'recent', recent, 'No reprints of your cards in the last 90 days.'));
}

function section(title, kind, list, empty) {
  const nav = i => ({ index: i, count: () => list.length, get: j => Promise.resolve(list[j].card) });
  return h('section', { class: `panel radar-section ${kind}` },
    h('div.panel-head', h('h2', title), h('span.muted', `${int(list.length)} card${list.length === 1 ? '' : 's'} · ${money(list.reduce((s, r) => s + (r.held_value_usd || 0), 0), { whole: true })}`)),
    list.length ? h('div.radar-list', list.map((r, i) => radarRow(r, () => openCard(r.card.scryfall_id, nav(i))))) : h('div.chart-empty', empty));
}

function radarRow(r, open) {
  const c = r.card;
  const reprints = [...(r.reprints || [])].sort((a, b) => (a.released_at || '').localeCompare(b.released_at || ''));
  return h('div', { class: 'radar-row impact-' + r.impact },
    h('button.rr-card', { type: 'button', onclick: open, 'aria-label': `${c.name}: open details` },
      h('span.rr-img', lazyImg(resize(c.image, 'small'), '', 'rr-thumb')),
      h('span.rr-name', h('b', c.name),
        h('span.muted.small', `${(c.set_code || '').toUpperCase()}${c.finish && c.finish !== 'normal' ? ' · ' + finishLabel(c.finish) : ''}`))),
    h('div.rr-held',
      h('span.rr-value', money(r.held_value_usd)),
      h('span.small', `${int(r.held_copies)} held`),
      h('span.small', r.used ? [h('span.used-num', `${int(r.used)} in decks`), ` · ${int(r.spare)} spare`] : `${int(r.spare)} spare`)),
    h('div.rr-reprints', reprints.map(x => {
      const chip = h('span', { class: 'reprint-chip ' + x.impact, tabindex: '0',
        title: `${x.set_name} (${x.set_code?.toUpperCase()}) #${x.collector_number} · ${x.set_type?.replace(/_/g, ' ')} · ${x.released_at}` },
      h('b', x.set_name), h('span.rc-meta', `${(x.set_type || '').replace(/_/g, ' ')} · ${shortDate(x.released_at)} · `, h('span.rc-when', countdown(x.released_at, data?.as_of || x.released_at))));
      if (x.image) attachHoverPreview(chip, { image: x.image });
      return chip;
    })),
    impactBadge(r.impact),
    h('div.rr-change', { title: 'Price of your copy now vs. when the reprint was previewed (or 30 days before release)' },
      changeChip(r.change_since_preview_pct), h('span.muted.small', 'since preview')));
}

// ---------- dashboard panel ----------
/** Compact panel: next release, cards affected, value at stake, top 5. */
export async function renderRadarPanel(box) {
  clear(box).append(spinner('Scanning upcoming sets…'));
  let d;
  try { d = await api.reprints({ min_value: 1 }); } catch (error) { clear(box).append(errorBox(error.message)); return; }
  const t = d.totals || {};
  const next = nextRelease(d);
  clear(box);
  if (!t.upcoming_cards) {
    box.append(h('div.chart-empty', 'None of your cards are in announced sets right now.'), h('a.btn.small', { href: '#/radar' }, 'Open the radar →'));
    return;
  }
  const top = (d.upcoming || []).slice(0, 5);
  const nav = i => ({ index: i, count: () => top.length, get: j => Promise.resolve(top[j].card) });
  box.append(
    h('div.deck-kpis',
      h('div', h('b', next ? shortDate(next) : '—'), next ? ` next release, ${countdown(next, d.as_of)}` : ' next release'),
      h('div', h('b', int(t.upcoming_cards)), ' of your cards reprinted'),
      h('div', h('b', money(t.upcoming_value_usd, { whole: true })), ' at stake'),
      h('div', { title: IMPACT_TIP.wide }, h('b', money(t.upcoming_wide_value_usd, { whole: true })), ' in wide reprints')),
    h('ol.radar-top', top.map((r, i) => h('li',
      h('button.name-btn', { type: 'button', onclick: () => openCard(r.card.scryfall_id, nav(i)) }, r.card.name),
      impactBadge(r.impact),
      h('span.muted.small', r.days_until != null ? (r.days_until > 0 ? `in ${r.days_until}d` : 'out now') : ''),
      h('span.rt-value', money(r.held_value_usd))))),
    h('a.btn.small', { href: '#/radar' }, 'Full radar →'));
}
