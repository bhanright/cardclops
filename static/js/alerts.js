// Price alerts (#/alerts): the alert feed, the watchlist with targets, and alert settings; plus the
// header bell and the "Add to watchlist" form used by the card detail modal. docs/TOOLS2.md.
import { h, $, clear, int, money, changeChip, spinner, errorBox, toast, finishLabel } from './util.js';
import { api, resize } from './api.js';
import { lazyImg, attachHoverPreview } from './cards.js';
import { openCard } from './detail.js';

const KIND = {
  target: ['◎', 'Target hit', 'A watched printing crossed your target price'],
  move: ['⇅', 'Big move', 'A card you hold moved a lot in price'],
  reprint: ['⟳', 'Reprint', 'A wide reprint of a card you hold was previewed'],
  legality: ['⚖', 'Legality', 'A ban, unban or rotation touched a card you hold'],
};
const FINISH_OF = { nonfoil: 'normal', foil: 'foil', etched: 'etched' };
const PRICE_KEY = { normal: 'usd', foil: 'usd_foil', etched: 'usd_etched' };

let built = false;
let alertsBox, watchBox, settingsBox, addBox;

// ---------- header bell ----------
let bellTimer = null;
export function initBell() {
  refreshBell();
  clearInterval(bellTimer);
  bellTimer = setInterval(refreshBell, 5 * 60 * 1000);
}
export async function refreshBell(known) {
  const bell = $('#alertBell');
  if (!bell) return;
  let unseen = known;
  if (unseen == null) {
    try { unseen = (await api.summary()).alerts?.unseen ?? 0; } catch { return; }
  }
  const badge = bell.querySelector('.bell-count');
  badge.textContent = unseen > 99 ? '99+' : String(unseen);
  badge.hidden = !unseen;
  bell.classList.toggle('ringing', unseen > 0);
  bell.setAttribute('aria-label', unseen ? `Price alerts: ${unseen} new` : 'Price alerts');
  bell.title = unseen ? `${unseen} new alert${unseen === 1 ? '' : 's'}` : 'Price alerts';
}

// ---------- view ----------
export function showAlerts() {
  const root = $('#view-alerts');
  if (!built) {
    built = true;
    alertsBox = h('div');
    watchBox = h('div');
    addBox = h('div.watch-add');
    settingsBox = h('div');
    root.append(
      h('section.panel.alerts-panel', h('div.panel-head', h('h2', 'Price alerts'), h('div.panel-tools',
        h('button.btn.small.ghost', { type: 'button', onclick: markAllSeen }, '✓ Mark all seen'),
        h('button.btn.small', { type: 'button', onclick: checkNow }, '⟳ Check now'))), alertsBox),
      h('section.panel.watch-panel', h('div.panel-head', h('h2', 'Watchlist'), h('span.muted.small', 'Any printing, owned or not. The daily refresh checks targets.')), addBox, watchBox),
      h('section.panel.settings-panel', h('h2', 'Alert settings'), settingsBox));
    buildAddForm();
  }
  loadAlerts();
  loadWatchlist();
  loadSettings();
}

async function loadAlerts() {
  clear(alertsBox).append(spinner('Loading alerts…'));
  let data;
  try { data = await api.alerts.list({ limit: 200 }); } catch (error) { clear(alertsBox).append(errorBox(error.message)); return; }
  refreshBell(data.unseen);
  const alerts = data.alerts || [];
  clear(alertsBox);
  if (!alerts.length) {
    alertsBox.append(h('div.chart-empty', 'No alerts yet. They appear here when a watched card hits its target, a card you hold moves a lot, gets a wide reprint, or is banned or unbanned.'));
    return;
  }
  const days = [...new Set(alerts.map(a => a.day))];
  alertsBox.append(...days.map(day => h('section.alert-day',
    h('h3', dayLabel(day)),
    h('ul.alert-list', alerts.filter(a => a.day === day).map(alertRow)))));
}

function dayLabel(day) {
  const d = new Date(day + 'T12:00:00');
  return isNaN(d) ? day : d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

function alertRow(a) {
  const [icon, label, tip] = KIND[a.kind] || ['•', a.kind, ''];
  return h('li', { class: `alert-row k-${a.kind}` + (a.seen ? '' : ' unseen') },
    h('span.kind-icon', { title: tip, 'aria-label': label }, icon),
    a.image ? h('button.alert-thumb', { type: 'button', 'aria-label': `Open ${a.name}`, onclick: () => a.scryfall_id && openCard(a.scryfall_id) }, lazyImg(resize(a.image, 'art_crop'), '', 'mv-img')) : h('span'),
    h('div.alert-text', h('div', h('span.kind-label', label), ' ', h('b', a.name), a.seen ? null : h('span.new-dot', ' ● new')), h('div.small', a.message)),
    a.price_usd != null ? h('span.alert-price', money(a.price_usd)) : h('span'));
}

async function markAllSeen() {
  try { const r = await api.alerts.seen({ all: true }); refreshBell(r.unseen ?? 0); toast('All alerts marked seen'); loadAlerts(); }
  catch (error) { toast('Could not mark seen: ' + error.message); }
}
async function checkNow(event) {
  const b = event.target;
  b.disabled = true; b.textContent = 'Checking…';
  try { const r = await api.alerts.check(); toast(r.new ? `${r.new} new alert${r.new === 1 ? '' : 's'}` : 'No new alerts'); loadAlerts(); loadWatchlist(); }
  catch (error) { toast('Check failed: ' + error.message); }
  finally { b.disabled = false; b.textContent = '⟳ Check now'; }
}

// ---------- watchlist ----------
async function loadWatchlist() {
  clear(watchBox).append(spinner('Loading watchlist…'));
  let items;
  try { items = (await api.watchlist.list()).items || []; } catch (error) { clear(watchBox).append(errorBox(error.message)); return; }
  clear(watchBox);
  if (!items.length) { watchBox.append(h('div.chart-empty', 'Nothing watched yet. Look a card up above, or use “Add to watchlist” in any card’s details.')); return; }
  watchBox.append(h('ul.watch-list', items.map(watchRow)));
}

function distanceText(item) {
  if (item.distance_pct == null || item.price_usd == null) return 'no price yet';
  const d = item.distance_pct;
  if (item.triggered) return 'target reached';
  return `${Math.abs(d).toFixed(1)}% ${d > 0 ? 'above' : 'below'} target`;
}

function watchRow(item) {
  const row = h('li', { class: 'watch-row' + (item.triggered ? ' hit' : '') });
  const draw = () => {
    clear(row).append(
      h('button.watch-img', { type: 'button', 'aria-label': `Open ${item.name}`, onclick: () => openCard(item.scryfall_id) }, lazyImg(resize(item.image, 'small'), '', 'rr-thumb')),
      h('div.watch-main',
        h('div', h('b', item.name), h('span.muted.small', ` ${(item.set_code || '').toUpperCase()} #${item.collector_number} · ${finishLabel(item.finish)}${item.owned ? ` · you own ${item.owned}` : ''}`)),
        h('div.watch-target', h('span', { class: 'target-pill ' + item.direction }, `${item.direction === 'below' ? '▼ at or below' : '▲ at or above'} ${money(item.target_usd)}`),
          item.triggered ? h('span.status-pill.on', '◎ Triggered') : h('span.muted.small', distanceText(item))),
        item.note ? h('div.small.muted', '“', item.note, '”') : null),
      h('div.watch-price', h('b', money(item.price_usd)),
        h('div.watch-changes', changeChip(item.change?.d1, { small: true, label: '1d' }), changeChip(item.change?.d7, { small: true, label: '7d' }), changeChip(item.change?.d30, { small: true, label: '30d' }))),
      h('div.watch-actions',
        h('button.btn.small', { type: 'button', onclick: edit }, '✎ Edit'),
        h('button.btn.small.danger', { type: 'button', onclick: confirmDelete }, '✗')));
    attachHoverPreview(row.querySelector('.watch-img'), { image: item.image });
  };
  const confirmDelete = () => {
    const actions = row.querySelector('.watch-actions');
    const yes = h('button.btn.small.danger', { type: 'button', onclick: async () => {
      try { await api.watchlist.remove(item.watch_id); toast(`Stopped watching ${item.name}`); row.remove(); }
      catch (error) { toast('Could not delete: ' + error.message); }
    } }, 'Stop watching');
    clear(actions).append(yes, h('button.btn.small.ghost', { type: 'button', onclick: draw }, 'Keep'));
    yes.focus();
  };
  const edit = () => {
    const form = targetFields({ target: item.target_usd, direction: item.direction, note: item.note, finish: item.finish, finishes: ['normal', 'foil', 'etched'] });
    clear(row).append(h('form.watch-edit', { onsubmit: async e => {
      e.preventDefault();
      try {
        const updated = await api.watchlist.update(item.watch_id, form.values());
        Object.assign(item, updated); draw(); toast('Saved');
      } catch (error) { toast('Could not save: ' + error.message); }
    } }, h('b', item.name), form.node, h('div.form-row', h('button.btn.small.go', { type: 'submit' }, 'Save'), h('button.btn.small.ghost', { type: 'button', onclick: draw }, 'Cancel'))));
    form.focus();
  };
  draw();
  return row;
}

/** Target price, direction, finish and note inputs. Returns {node, values(), focus()}. */
function targetFields({ target, direction = 'below', note = '', finish = 'normal', finishes = ['normal'], price } = {}) {
  let dir = direction;
  const targetInput = h('input.num-input', { type: 'number', min: 0, step: 0.01, required: true, value: target ?? (price != null ? (price * 0.85).toFixed(2) : ''), 'aria-label': 'Target price in dollars' });
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Alert when the price is' });
  const drawSeg = () => clear(seg).append(...[['below', '▼ At or below'], ['above', '▲ At or above']].map(([v, l]) =>
    h('button.seg-btn', { type: 'button', class: dir === v ? 'on' : null, 'aria-pressed': String(dir === v), onclick: () => { dir = v; drawSeg(); } }, l)));
  drawSeg();
  const finishSelect = h('select.select', { 'aria-label': 'Finish' }, finishes.map(f => h('option', { value: f, selected: f === finish }, finishLabel(f))));
  const noteInput = h('input.text-input', { type: 'text', value: note || '', placeholder: 'Note (optional)', 'aria-label': 'Note' });
  return {
    node: h('div.target-fields', seg, h('label.inline-label', '$', targetInput), finishes.length > 1 ? finishSelect : null, noteInput),
    values: () => ({ target_usd: +targetInput.value, direction: dir, note: noteInput.value.trim(), finish: finishSelect.value }),
    focus: () => targetInput.focus(),
    finishSelect,
  };
}

// ---------- add to watchlist (lookup) ----------
function buildAddForm() {
  const q = h('input.text-input', { type: 'search', placeholder: 'Card name, e.g. The One Ring', 'aria-label': 'Find a card to watch' });
  const results = h('div.lookup-results', { 'aria-live': 'polite' });
  const find = async () => {
    const name = q.value.trim();
    if (!name) return q.focus();
    clear(results).append(spinner('Asking Scryfall…'));
    let cards;
    try { cards = (await api.lookup({ q: name })).cards || []; } catch (error) { clear(results).append(errorBox(error.message)); return; }
    clear(results);
    if (!cards.length) { results.append(h('p.muted', `No card called “${name}”.`)); return; }
    const picker = h('div.lookup-picker');
    if (cards.length > 1) {
      results.append(h('div.lookup-names', cards.slice(0, 12).map((c, i) => h('button.mini-chip', { type: 'button', 'aria-pressed': String(i === 0), onclick: async e => {
        for (const b of results.querySelectorAll('.lookup-names button')) b.setAttribute('aria-pressed', String(b === e.currentTarget));
        if (!c.printings?.length) {
          clear(picker).append(spinner('Loading printings…'));
          try { c.printings = (await api.lookup({ oracle_id: c.oracle_id })).cards?.[0]?.printings || []; } catch (error) { clear(picker).append(errorBox(error.message)); return; }
        }
        showPrintings(picker, c, () => { clear(results); q.value = ''; loadWatchlist(); });
      } }, c.name))));
    }
    results.append(picker);
    showPrintings(picker, cards[0], () => { clear(results); q.value = ''; loadWatchlist(); });
  };
  q.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); find(); } });
  addBox.append(h('div.form-row', q, h('button.btn', { type: 'button', onclick: find }, 'Find')), results);
}

function showPrintings(box, card, onAdded) {
  const printings = (card.printings || []).slice().sort((a, b) => (b.released_at || '').localeCompare(a.released_at || ''));
  if (!printings.length) { clear(box).append(h('p.muted', 'No printings found.')); return; }
  const preview = h('img.lookup-img', { alt: '' });
  const printing = h('select.select', { 'aria-label': 'Printing' }, printings.map((p, i) => h('option', { value: i },
    `${p.set_name} (${(p.set_code || '').toUpperCase()}) #${p.collector_number}${p.released_at ? ' · ' + p.released_at.slice(0, 4) : ''}`)));
  const fieldsBox = h('div');
  let fields;
  const current = () => printings[+printing.value];
  const drawFields = () => {
    const p = current();
    preview.src = p.image;
    const finishes = (p.finishes || ['nonfoil']).map(f => FINISH_OF[f] || 'normal');
    fields = targetFields({ finishes, finish: finishes[0], price: p.prices?.[PRICE_KEY[finishes[0]]] });
    const priceNote = h('span.muted.small');
    const showPrice = () => { const v = p.prices?.[PRICE_KEY[fields.finishSelect.value]]; priceNote.textContent = v != null ? `now ${money(+v)}` : 'no price yet'; };
    fields.finishSelect.addEventListener('change', showPrice);
    showPrice();
    clear(fieldsBox).append(fields.node, priceNote);
  };
  printing.addEventListener('change', drawFields);
  const add = h('button.btn.go', { type: 'button', onclick: async () => {
    const v = fields.values();
    if (!(v.target_usd > 0)) { toast('Enter a target price'); fields.focus(); return; }
    add.disabled = true;
    try { await api.watchlist.add({ scryfall_id: current().scryfall_id, ...v }); toast(`Watching ${card.name}`); onAdded(); }
    catch (error) { toast('Could not add: ' + error.message); add.disabled = false; }
  } }, '＋ Watch');
  clear(box).append(h('div.lookup-card', preview, h('div.lookup-form', h('b', card.name), h('label.inline-label', 'Printing ', printing), fieldsBox, h('div.form-row', add))));
  drawFields();
}

/** Inline form for the card detail modal, prefilled with that printing and its finishes. */
export function watchButton(card, holdings = []) {
  const box = h('div.watch-inline');
  const button = h('button.btn.small', { type: 'button', onclick: () => {
    const p = card.prices || {};
    const finishes = ['normal', 'foil', 'etched'].filter(f => p[PRICE_KEY[f]] != null);
    const owned = holdings.find(x => x.scryfall_id === card.scryfall_id || !x.scryfall_id);
    const finish = owned?.finish && finishes.includes(owned.finish) ? owned.finish : finishes[0] || 'normal';
    const fields = targetFields({ finishes: finishes.length ? finishes : ['normal'], finish, price: p[PRICE_KEY[finish]] != null ? +p[PRICE_KEY[finish]] : null });
    clear(box).append(h('form.watch-edit', { onsubmit: async e => {
      e.preventDefault();
      const v = fields.values();
      if (!(v.target_usd > 0)) { toast('Enter a target price'); return; }
      try { await api.watchlist.add({ scryfall_id: card.scryfall_id, ...v }); toast(`Watching ${card.name}`); clear(box).append(h('span.status-pill.on', '◎ On your watchlist'), ' ', h('a', { href: '#/alerts' }, 'See watchlist')); }
      catch (error) { toast('Could not add: ' + error.message); }
    } }, fields.node, h('div.form-row', h('button.btn.small.go', { type: 'submit' }, '＋ Watch this printing'), h('button.btn.small.ghost', { type: 'button', onclick: () => clear(box).append(button) }, 'Cancel'))));
    fields.focus();
  } }, '◎ Add to watchlist');
  box.append(button);
  return box;
}

// ---------- settings ----------
async function loadSettings() {
  clear(settingsBox).append(spinner('Loading settings…'));
  let s;
  try { s = await api.alerts.settings(); } catch (error) { clear(settingsBox).append(errorBox(error.message)); return; }
  const pctIn = h('input.num-input', { type: 'number', min: 1, step: 1, value: s.held_move_pct, 'aria-label': 'Move percentage' });
  const windowSel = h('select.select', { 'aria-label': 'Over how many days' }, [1, 7, 30].map(d => h('option', { value: d, selected: d === s.held_move_window }, d === 1 ? '1 day' : `${d} days`)));
  const minIn = h('input.num-input', { type: 'number', min: 0, step: 1, value: s.held_min_value_usd, 'aria-label': 'Minimum value held' });
  const check = (key, label) => { const c = h('input', { type: 'checkbox', checked: !!s[key] }); c.dataset.key = key; return h('label.inline-label.check', c, ' ', label); };
  const checks = [check('reprint_alerts', 'Wide reprints of cards I hold'), check('legality_alerts', 'Bans, unbans and rotations'), s.notifications_available ? check('windows_notifications', 'Windows notifications') : null].filter(Boolean);
  const state = h('span.muted.small');
  settingsBox.replaceChildren(h('form.settings-form', { onsubmit: async e => {
    e.preventDefault();
    const body = { held_move_pct: +pctIn.value, held_move_window: +windowSel.value, held_min_value_usd: +minIn.value };
    for (const l of checks) { const c = l.querySelector('input'); body[c.dataset.key] = c.checked; }
    state.textContent = 'Saving…';
    try { await api.alerts.saveSettings(body); state.textContent = '✓ Saved'; } catch (error) { state.textContent = '✗ ' + error.message; }
  } },
  h('p', 'Tell me when a card I hold, worth at least ', h('label.inline-label', '$', minIn), ' in total, moves ', h('label.inline-label', pctIn, '%'), ' or more over ', windowSel, '.'),
  h('div.form-row', checks),
  h('div.form-row', h('button.btn.go', { type: 'submit' }, 'Save settings'), state)));
}
