// Card detail modal: big image, rules text, price chart, holdings, tags, legality, other printings, similar.
import { h, $, clear, money, signedMoney, signedClass, manaCost, rulesText, finishLabel, formatLabel, sortFormats, spinner, errorBox, int, titleCase } from './util.js';
import { watchButton } from './alerts.js';
import { api, resize } from './api.js';
import { cardFace, thumb, rarityGem } from './cards.js';
import { timeSeriesPanel } from './charts.js';

let hooks = { search: () => {} };
let modal, dialog, body, counter, prevBtn, nextBtn;
let nav = null;
let token = 0;
let lastFocus = null;

export function setDetailHooks(next) { hooks = { ...hooks, ...next }; }

function ensureModal() {
  if (modal) return;
  modal = $('#modal');
  prevBtn = h('button.icon-btn', { type: 'button', 'aria-label': 'Previous result (←)', title: 'Previous (←)', onclick: () => step(-1) }, '←');
  nextBtn = h('button.icon-btn', { type: 'button', 'aria-label': 'Next result (→)', title: 'Next (→)', onclick: () => step(1) }, '→');
  counter = h('span.nav-count');
  body = h('div.modal-body');
  dialog = h('div.modal-card', { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Card details', tabindex: '-1' },
    h('div.modal-top', h('div.modal-nav', prevBtn, counter, nextBtn),
      h('button.icon-btn.close', { type: 'button', 'aria-label': 'Close (Esc)', title: 'Close (Esc)', onclick: closeCard }, '×')),
    body);
  modal.append(h('div.modal-backdrop', { onclick: closeCard }), dialog);
  document.addEventListener('keydown', event => {
    if (modal.hidden) return;
    const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName);
    if (event.key === 'Escape') { event.preventDefault(); closeCard(); }
    else if (!typing && event.key === 'ArrowLeft') { event.preventDefault(); step(-1); }
    else if (!typing && event.key === 'ArrowRight') { event.preventDefault(); step(1); }
    else if (event.key === 'Tab') trapFocus(event);
  });
}

function trapFocus(event) {
  const focusable = [...dialog.querySelectorAll('button, a[href], select, input, [tabindex="0"]')].filter(n => !n.disabled && n.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0], last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}

/** nav: {index, count(), get(i) → Promise<CardSummary>, onClose(i)} for ←/→ through a result list. */
export function openCard(id, navigation = null) {
  ensureModal();
  if (modal.hidden) lastFocus = document.activeElement;
  if (navigation) nav = { ...navigation };
  else if (modal.hidden) nav = null;
  modal.hidden = false;
  document.body.classList.add('modal-open');
  load(id);
  dialog.focus({ preventScroll: true });
}

export function closeCard() {
  if (!modal || modal.hidden) return;
  modal.hidden = true;
  document.body.classList.remove('modal-open');
  token++;
  nav?.onClose?.(nav.index);
  nav = null;
  lastFocus?.focus?.({ preventScroll: true });
}

async function step(delta) {
  if (!nav) return;
  const next = nav.index + delta;
  if (next < 0 || next >= nav.count()) return;
  nav.index = next;
  syncNav();
  const card = await nav.get(next);
  if (card && nav && nav.index === next) load(card.scryfall_id);
}

function syncNav() {
  const has = !!nav;
  prevBtn.hidden = nextBtn.hidden = counter.hidden = !has;
  if (!has) return;
  const count = nav.count();
  counter.textContent = `${int(nav.index + 1)} / ${int(count)}`;
  prevBtn.disabled = nav.index <= 0;
  nextBtn.disabled = nav.index >= count - 1;
}

async function load(id) {
  const mine = ++token;
  syncNav();
  clear(body).append(h('div.modal-loading', spinner('Loading card…')));
  let data;
  try { data = await api.card(id); } catch (error) {
    if (mine !== token) return;
    // Lists of cards you may not own (set checklists) can show a plain image when the card has no local detail.
    const item = nav?.lightbox ? await nav.get(nav.index) : null;
    if (item && item.scryfall_id === id && item.image) lightbox(item);
    else clear(body).append(errorBox(error.message));
    return;
  }
  if (mine !== token) return;
  render(data);
  body.scrollTop = 0;
  dialog.scrollTop = 0;
}

function lightbox(item) {
  const { node: face } = cardFace({ ...item, finish: 'normal', image: resize(item.image, 'large'), image_back: item.image_back ? resize(item.image_back, 'large') : null }, { size: 'large', eager: true });
  const rawPrice = item.prices?.usd ?? item.prices?.usd_foil;
  const price = rawPrice == null ? null : Number(rawPrice);
  clear(body).append(h('div.lightbox', h('div.d-image', face),
    h('div.lightbox-text', h('h2.d-name', item.name), h('p.muted', `#${item.collector_number}${item.type_line ? ' · ' + item.type_line : ''}`),
      price != null ? h('p', 'Price: ', h('b', money(price))) : null,
      h('p.small.muted', 'You don’t own this card, and its full details aren’t in the local card data yet — this is just its picture.'))));
}

const LEGAL_LABEL = { legal: '✓ Legal', not_legal: '– Not legal', banned: '✗ Banned', restricted: '◐ Restricted' };

function render({ card, tags = [], holdings = [], other_printings = [], similar = [], decks = [] }) {
  const faces = card.faces && card.faces.length ? card.faces : [card];
  const finishes = new Set(holdings.filter(x => x.scryfall_id === card.scryfall_id).map(x => x.finish));
  const headFinish = finishes.size === 1 ? [...finishes][0] : 'normal';
  const { node: face } = cardFace({ ...card, finish: headFinish, image: resize(card.image, 'large'), image_back: card.image_back ? resize(card.image_back, 'large') : null }, { size: 'large', eager: true });

  const p = card.prices || {};
  const priceBadges = [['usd', 'Non-foil'], ['usd_foil', 'Foil'], ['usd_etched', 'Etched'], ['eur', '€'], ['tix', 'MTGO tix']]
    .filter(([key]) => p[key] != null)
    .map(([key, label]) => h('span.price-badge', h('span.muted', label), ' ', key === 'eur' ? `€${(+p[key]).toFixed(2)}` : key === 'tix' ? `${(+p[key]).toFixed(2)}` : money(+p[key])));

  const links = [];
  if (card.scryfall_uri) links.push(h('a.btn.small', { href: card.scryfall_uri, target: '_blank', rel: 'noopener' }, 'Scryfall ↗'));
  for (const [key, url] of Object.entries(card.purchase_uris || {})) {
    links.push(h('a.btn.small.ghost', { href: url, target: '_blank', rel: 'noopener' }, titleCase(key) + ' ↗'));
  }

  const left = h('div.d-left', h('div.d-image', face), h('div.price-badges', priceBadges), h('div.d-links', links), watchButton(card, holdings));

  const facesBlock = faces.map((f, i) => h('div.face-block',
    faces.length > 1 ? h('div.face-name', h('span', f.name), ' ', manaCost(f.mana_cost)) : null,
    h('div.type-line', f.type_line || (i ? '' : card.type_line)),
    rulesText(f.oracle_text ?? (faces.length === 1 ? card.oracle_text : '')),
    f.power != null || f.toughness != null ? h('div.pt', `${f.power ?? card.power}/${f.toughness ?? card.toughness}`) : null,
    f.loyalty != null ? h('div.pt', 'Loyalty ' + f.loyalty) : null,
    f.flavor_text ? h('div.flavor', f.flavor_text) : null));

  const flags = [];
  if (card.reserved) flags.push(h('span.flag.orange', 'Reserved list'));
  if (card.game_changer) flags.push(h('span.flag.pink', 'Game changer'));
  if (card.edhrec_rank) flags.push(h('span.flag', 'EDHREC #' + int(card.edhrec_rank)));

  const chartBox = h('div.chart-box', spinner('Loading price history…'));
  loadPrices(card.scryfall_id, chartBox);

  // Holdings cover every printing of the card; say which row is which when there are several.
  const manyPrintings = new Set(holdings.map(x => x.scryfall_id)).size > 1;
  const holdingTotals = holdings.reduce((a, x) => ({ qty: a.qty + x.quantity, gain: x.gain_usd == null ? a.gain : (a.gain ?? 0) + x.gain_usd }), { qty: 0, gain: null });
  const holdingsTable = holdings.length ? h('div.table-scroll', h('table.table',
    h('thead', h('tr', [...(manyPrintings ? ['Printing'] : []), 'Finish', 'Cond.', 'Lang', 'Qty', 'Paid', 'Now', 'Gain', 'Added'].map(t => h('th', t)))),
    h('tbody', holdings.map(x => h('tr', { class: x.scryfall_id === card.scryfall_id ? 'this-printing' : '' },
      manyPrintings ? h('td', { title: x.scryfall_id === card.scryfall_id ? 'This printing' : '' },
        `${(x.set_code || '').toUpperCase()} #${x.collector_number || '?'}${x.scryfall_id === card.scryfall_id ? ' ●' : ''}`) : null,
      h('td', finishLabel(x.finish)), h('td', x.condition ? titleCase(x.condition) : '—'), h('td', (x.language || '').toUpperCase() || '—'),
      h('td.num', int(x.quantity)), h('td.num', money(x.purchase_price)), h('td.num', money(x.price_usd)),
      h('td.num', { class: 'gain ' + signedClass(x.gain_usd) }, x.gain_usd == null ? '—' : signedMoney(x.gain_usd)),
      h('td', (x.added_at || '').slice(0, 10) || '—')))),
    holdings.length > 1 ? h('tfoot', h('tr', h('td', { colspan: manyPrintings ? 4 : 3 }, 'Total'), h('td.num', int(holdingTotals.qty)), h('td'), h('td'),
      h('td.num', { class: 'gain ' + signedClass(holdingTotals.gain) }, holdingTotals.gain == null ? '—' : signedMoney(holdingTotals.gain)), h('td'))) : null))
    : h('p.muted', 'You don’t hold this card.');

  const tagChips = tags.length ? h('div.tag-chips', tags.map(t => h('button.tchip', { type: 'button', title: (t.description || '') + `\nSearch otag:${t.slug}`,
    onclick: () => { closeCard(); hooks.search(`otag:${t.slug}`); } }, t.slug))) : h('p.muted', 'No Tagger function tags for this card.');

  const legal = card.legalities || {};
  const legalGrid = h('div.legal-grid', sortFormats(Object.keys(legal)).map(f =>
    h('div', { class: `legal ${legal[f]}` }, h('span.lf', formatLabel(f)), h('span.ls', LEGAL_LABEL[legal[f]] || titleCase(legal[f])))));

  const right = h('div.d-right',
    h('h2.d-name', h('span', card.name), ' ', manaCost(card.mana_cost)),
    h('div.d-set', rarityGem(card.rarity), ' ', h('a.set-link', { href: `#/sets/${encodeURIComponent((card.set_code || '').toLowerCase())}`, title: 'Open this set’s checklist' }, h('b', card.set_name)), ` (${(card.set_code || '').toUpperCase()}) #${card.collector_number}`,
      card.artist ? h('span.muted', ' · ✎ ' + card.artist) : null, card.released_at ? h('span.muted', ' · ' + card.released_at) : null),
    flags.length ? h('div.flags', flags) : null,
    h('div.faces', facesBlock),
    section('Price history', chartBox),
    section(`Your copies${holdingTotals.qty ? ' · ' + int(holdingTotals.qty) : ''}`, holdingsTable),
    decks.length ? section(`In your decks · ${decks.length}`, deckUses(decks)) : null,
    section('Function tags', tagChips),
    section('Legality', legalGrid));

  const open = c => openCard(c.scryfall_id);
  clear(body).append(...[
    h('div.detail-grid', left, right),
    other_printings.length ? section(`Other printings you own · ${other_printings.length}`, h('div.thumb-row', other_printings.map(c => thumb(c, { onOpen: () => open(c) })))) : null,
    similar.length ? section('Similar cards in your collection', h('div.thumb-row', similar.map(c => thumb(c, { onOpen: () => open(c), caption: c.name })))) : null].filter(Boolean));
}

const SECTION_LABEL = { commander: 'Commander', companion: 'Companion', main: 'Main', sideboard: 'Sideboard', maybeboard: 'Maybeboard' };

/** Which decks list this card, and which of your copies each one uses. Links go to the deck page. */
function deckUses(decks) {
  return h('ul.deck-uses', decks.map(d => h('li', { class: d.status === 'active' ? 'active' : 'inactive' },
    h('a.deck-link', { href: `#/decks/${d.deck_id}` }, d.name),
    h('span', { class: 'status-pill ' + (d.status === 'active' ? 'on' : 'off') }, d.status === 'active' ? '● Active' : '○ Inactive'),
    h('span.muted', ` ${SECTION_LABEL[d.section] || d.section} · ×${int(d.quantity)}`),
    d.copies?.length ? h('span.copy-list', d.copies.map(c => h('span.copy-chip',
      `${(c.set_code || '').toUpperCase()} #${c.collector_number}${c.finish && c.finish !== 'normal' ? ' ✦' : ''}${c.quantity > 1 ? ' ×' + c.quantity : ''}`)))
      : h('span.muted.small', d.status === 'active' ? ' no copy allocated' : ' reserves nothing'))));
}

function section(title, content) {
  return h('section.d-section', h('h3', title), content);
}

async function loadPrices(id, box) {
  const mine = token;
  let data;
  try { data = await api.prices(id); } catch (error) {
    if (mine === token) clear(box).append(errorBox(error.message));
    return;
  }
  if (mine !== token) return;
  const formats = {
    EUR: [v => `€${v.toFixed(2)}`, v => `€${v < 10 ? v.toFixed(2) : Math.round(v)}`],
    TIX: [v => `${v.toFixed(2)} tix`, v => v.toFixed(v < 10 ? 2 : 0)],
  };
  const series = (data.series || []).map(s => ({
    ...s,
    label: `${finishLabel(s.finish)} · ${s.label || s.source}${s.currency && s.currency !== 'USD' ? ' (' + s.currency + ')' : ''}`,
    format: formats[s.currency]?.[0], axisFormat: formats[s.currency]?.[1],
  }));
  timeSeriesPanel(clear(box), { series, defaultIndex: data.default || 0, height: 200,
    emptyNote: 'History starts today; it grows each refresh.' });
}
