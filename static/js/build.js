// "What can I build?": commanders you own ranked by how much of a deck your spare cards fill (#/build),
// and a proposed 100-card draft for one of them (#/build/<oracle_id>?partner=<oracle_id>). docs/TOOLS.md.
import { h, $, clear, int, money, spinner, errorBox, manaCost, symbol, copyText, toast, debounce, store } from './util.js';
import { api, resize } from './api.js';
import { lazyImg, attachHoverPreview, hideHoverPreview } from './cards.js';
import { openCard } from './detail.js';
import { statsPanels } from './deckstats.js';
import { colorPips, invalidateDecks } from './decks.js';

const PARTS = ['lands', 'ramp', 'draw', 'removal', 'wipes', 'synergy'];
const PART_LABEL = { lands: 'Lands', ramp: 'Ramp', draw: 'Draw', removal: 'Removal', wipes: 'Wipes', synergy: 'Synergy' };
const PART_COLOR = { lands: 'var(--yellow)', ramp: 'var(--lime)', draw: 'var(--cyan)', removal: 'var(--pink)', wipes: 'var(--orange)', synergy: 'var(--violet)' };
const ROLES = ['commander', 'lands', 'ramp', 'draw', 'removal', 'wipes', 'synergy', 'filler'];
const ROLE_LABEL = { commander: 'Commander', lands: 'Lands', ramp: 'Ramp', draw: 'Card draw', removal: 'Removal', wipes: 'Board wipes', synergy: 'Synergy', filler: 'Filler' };
const PREFS_KEY = 'gallery.build';
const prefs = { colors: '', q: '', ...store.get(PREFS_KEY, {}) };

let built = false;
let home, page, grid, chips, search, status;
let cache = new Map();          // "colors|q" → response
let lastList = null;            // commanders from the most recent list, for partner choices
let controller = null;
let draftToken = 0;

export function showBuild(commanderId, partnerId) {
  const root = $('#view-build');
  if (!built) {
    built = true;
    home = h('div.build-home');
    page = h('div.build-draft', { hidden: true });
    root.append(home, page);
    buildHome();
  }
  if (commanderId) {
    home.hidden = true;
    page.hidden = false;
    showDraft(commanderId, partnerId);
  } else {
    draftToken++;
    hideHoverPreview();
    page.hidden = true;
    home.hidden = false;
    loadList();
  }
}

// ---------- commander list ----------
function buildHome() {
  chips = h('div.color-chips', { role: 'group', 'aria-label': 'Limit to color identities within these colors' });
  search = h('input.text-input', { type: 'search', placeholder: 'Commander name…', 'aria-label': 'Search commanders by name', value: prefs.q });
  search.addEventListener('input', debounce(() => { prefs.q = search.value.trim(); save(); loadList(); }, 350));
  status = h('div.build-status', { 'aria-live': 'polite' });
  grid = h('div.cmd-grid');
  home.append(
    h('section.panel.build-head',
      h('div.panel-head', h('h2', 'What can I build?'), h('div.panel-tools', chips, search)),
      h('p.muted', 'Every commander you own, ranked by how much of a 99-card deck your ', h('b', 'spare'),
        ' cards could fill — copies not reserved by an active deck — and then by how well those cards fit it. Pick one for a proposed list.')),
    status, grid);
  drawChips();
}
function save() { store.set(PREFS_KEY, prefs); }

function drawChips() {
  clear(chips);
  for (const c of 'WUBRG') {
    const on = prefs.colors.includes(c);
    chips.append(h('button', { type: 'button', class: 'color-chip' + (on ? ' on' : ''), 'aria-pressed': String(on), title: on ? `Remove ${c}` : `Add ${c}`,
      onclick: () => { prefs.colors = on ? prefs.colors.replace(c, '') : [...'WUBRG'].filter(x => x === c || prefs.colors.includes(x)).join(''); save(); drawChips(); loadList(); } },
    symbol(c)));
  }
  if (prefs.colors) chips.append(h('button.link-btn', { type: 'button', onclick: () => { prefs.colors = ''; save(); drawChips(); loadList(); } }, 'any colors'));
}

async function loadList() {
  const key = `${prefs.colors}|${prefs.q}`;
  if (cache.has(key)) { renderList(cache.get(key)); return; }
  controller?.abort();
  controller = new AbortController();
  const count = h('span', 'your');
  clear(status).append(h('div.panel', h('div.spinner-wrap', { role: 'status' }, h('span.spinner', { 'aria-hidden': 'true' }),
    h('span', 'Checking ', count, ' commanders against your spare cards… the first look can take a few seconds.'))));
  clear(grid);
  // How many commanders are about to be checked, for the spinner (cheap search, best effort).
  api.search({ q: 't:legendary (t:creature or o:"can be your commander") spare>=1', limit: 1, unique: 'cards' })
    .then(r => { if (r.totals?.unique_cards) count.textContent = int(r.totals.unique_cards); }).catch(() => {});
  let response;
  try {
    response = await api.build.commanders({ limit: 60, colors: prefs.colors || null, q: prefs.q || null }, controller.signal);
  } catch (error) {
    if (error.name === 'AbortError') return;
    clear(status).append(errorBox(error.message));
    return;
  }
  cache.set(key, response);
  renderList(response);
}

function renderList(response) {
  const list = response.commanders || [];
  lastList = list.length ? list : lastList;
  clear(status).append(h('p.muted.small', `${int(list.length)} commander${list.length === 1 ? '' : 's'}${response.computed_in_ms >= 100 ? ` · worked out in ${(response.computed_in_ms / 1000).toFixed(1)} s` : ''}.`));
  clear(grid);
  if (!list.length) { grid.append(h('div.chart-empty', 'No commanders you own match. Try fewer colors or clear the name search.')); return; }
  // Coverage saturates when little is reserved by decks, so fit (synergy) is the headline; scale it to the best in view.
  const bestFit = Math.max(0.0001, ...list.map(x => x.synergy_score || 0));
  grid.append(...list.map((x, i) => commanderTile(x, i, bestFit)));
}

export function coverageRing(coverage, size = 76) {
  const r = 30, c = 2 * Math.PI * r;
  const pctValue = Math.round((coverage || 0) * 100);
  const cls = pctValue >= 90 ? 'good' : pctValue >= 70 ? 'ok' : 'bad';
  return h('span', { class: 'ring ' + cls, role: 'img', 'aria-label': `Coverage ${pctValue}%`, style: { width: size + 'px', height: size + 'px' },
    html: `<svg viewBox="0 0 76 76" aria-hidden="true"><circle cx="38" cy="38" r="${r}" class="ring-bg"/><circle cx="38" cy="38" r="${r}" class="ring-fill" stroke-dasharray="${(c * (coverage || 0)).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 38 38)"/></svg><b>${pctValue}%</b>` });
}

function partBars(parts) {
  return h('div.part-bars', PARTS.map(p => {
    const part = parts?.[p] || { have: 0, target: 0 };
    const full = part.have >= part.target;
    return h('div', { class: 'part' + (full ? ' full' : ''), title: `${PART_LABEL[p]}: ${part.have} of ${part.target}` },
      h('span.part-label', PART_LABEL[p]),
      h('span.part-track', h('span.part-fill', { style: { width: `${part.target ? Math.min(100, (part.have / part.target) * 100) : 0}%`, background: PART_COLOR[p] } })),
      h('span.part-num', `${part.have}/${part.target}`));
  }));
}

export function fitMeter(score, best) {
  const share = best ? Math.min(1, (score || 0) / best) : 0;
  const label = share >= 0.85 ? 'Great fit' : share >= 0.6 ? 'Good fit' : share >= 0.35 ? 'Some fit' : 'Loose fit';
  return h('div', { class: 'fit-meter', title: `Synergy score ${(score || 0).toFixed(3)}: how closely your spare cards match this commander (scaled to the best shown)` },
    h('span.fit-label', label),
    h('span.fit-track', h('span.fit-fill', { style: { width: `${share * 100}%` } })),
    h('b.fit-num', (score || 0).toFixed(2)));
}

function commanderTile(x, index, bestFit) {
  const c = x.card;
  return h('a.cmd-tile', { href: `#/build/${c.oracle_id}`, 'aria-label': `Rank ${index + 1}: ${c.name}, fit ${(x.synergy_score || 0).toFixed(2)}, ${Math.round(x.coverage * 100)}% of a deck from spare cards` },
    h('div.cmd-art', lazyImg(resize(c.image, 'art_crop'), '', 'dt-art'), h('span.rank-badge', `#${index + 1}`), coverageRing(x.coverage, 62)),
    h('div.cmd-body',
      h('div.dt-name', c.name),
      fitMeter(x.synergy_score, bestFit),
      h('div.dt-meta', colorPips(x.color_identity), h('span.muted', `${int(x.pool_size)} playable spares`), x.edhrec_rank ? h('span.muted', `EDHREC #${int(x.edhrec_rank)}`) : null),
      partBars(x.parts),
      x.synergy?.length ? h('div.synergy', h('b', 'Fits: '), x.synergy.join(' · ')) : h('div.synergy.muted', 'No close synergy cards spare.')));
}

// ---------- draft ----------
async function showDraft(commanderId, partnerId) {
  const mine = ++draftToken;
  clear(page).append(h('a.back-link', { href: '#/build' }, '← All commanders'), h('div.panel', spinner('Drafting 99 cards from your spare copies…')));
  let draft;
  try { draft = await api.build.draft(commanderId, partnerId || null); } catch (error) {
    if (mine === draftToken) clear(page).append(h('a.back-link', { href: '#/build' }, '← All commanders'), errorBox(error.message));
    return;
  }
  if (mine !== draftToken) return;
  if (draft.error) { clear(page).append(h('a.back-link', { href: '#/build' }, '← All commanders'), errorBox(draft.error)); return; }
  renderDraft(draft, commanderId, partnerId);
  document.title = `${draft.commander?.name || 'Draft'} · Build · Collection Gallery`;
  partnerChoice(draft.commander, commanderId, partnerId, mine);
}

function renderDraft(draft, commanderId, partnerId) {
  const cmd = draft.commander;
  const lines = draft.lines || [];
  const fromList = (lastList || []).find(x => x.card.oracle_id === commanderId);
  const total = lines.reduce((s, l) => s + l.quantity, 0);
  const value = lines.reduce((s, l) => s + (l.card?.price_usd || 0) * l.quantity, 0);
  const saveBtn = h('button.btn.go', { type: 'button', onclick: async () => {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      const names = lines.filter(l => l.role === 'commander').map(l => l.card.name.split(' // ')[0]);
      const r = await api.decks.import([{ name: `${names.join(' & ') || cmd.name} (built from spares)`, text: draft.text, format: 'commander', status: 'inactive', source: 'builder', source_url: null }]);
      const deck = r.imported?.[0];
      invalidateDecks();
      toast('Saved as an inactive deck');
      if (deck) location.hash = `#/decks/${deck.deck_id}`;
    } catch (error) { toast('Could not save: ' + error.message); saveBtn.disabled = false; saveBtn.textContent = '＋ Save as deck'; }
  } }, '＋ Save as deck');
  const partnerBox = h('div.partner-box');
  const shortfalls = Object.entries(draft.shortfalls || {}).filter(([, n]) => n > 0);

  const nav = cards => i => ({ index: i, count: () => cards.length, get: j => Promise.resolve(cards[j]) });
  const allCards = ROLES.flatMap(r => lines.filter(l => l.role === r)).map(l => l.card).filter(Boolean);
  const open = card => openCard(card.scryfall_id, nav(allCards)(Math.max(0, allCards.indexOf(card))));
  const openByName = name => { const c = allCards.find(x => x.name === name || x.name.split(' // ')[0] === name); if (c) open(c); };

  clear(page).append(
    h('a.back-link', { href: '#/build' }, '← All commanders'),
    h('section.panel.deck-head.has-cover',
      h('div.dh-banner', h('img', { src: resize(cmd.image, 'art_crop'), alt: '', decoding: 'async' })),
      h('div.dh-body',
        h('div.dh-name', h('h1', cmd.name), fromList ? coverageRing(fromList.coverage, 64) : null),
        h('div.dh-controls', colorPips(cmd.color_identity), h('span.muted', `${int(total)} cards · ${money(value, { whole: value >= 1000 })} of spare copies`),
          h('span.muted.small', 'Built only from spare copies. Saving makes an inactive deck, so it reserves nothing until you activate it.')),
        fromList ? partBars(fromList.parts) : null,
        partnerBox,
        shortfalls.length ? h('div.shortfalls', h('b', 'Short of target: '), shortfalls.map(([p, n]) => h('span.short-chip', `${PART_LABEL[p] || p} −${n}`))) : null,
        draft.notes?.length ? h('ul.draft-notes', draft.notes.map(n => h('li', n))) : null,
        h('div.form-row.dh-actions', saveBtn,
          h('button.btn', { type: 'button', onclick: async () => toast((await copyText(draft.text)) ? 'List copied' : 'Copy failed') }, '⧉ Copy list')))),
    h('section.panel.draft-list',
      h('h2', 'Proposed list'),
      h('div.role-grid', ROLES.map(role => {
        const ls = lines.filter(l => l.role === role);
        if (!ls.length) return null;
        const n = ls.reduce((s, l) => s + l.quantity, 0);
        return h('section', { class: `role role-${role}` },
          h('h3', ROLE_LABEL[role], h('span.muted', ` ${int(n)}`)),
          h('ul.role-lines', ls.map(l => {
            const row = h('li', h('span.dl-q', int(l.quantity)),
              h('button.dl-card', { type: 'button', onclick: () => open(l.card) }, h('span.dl-name', l.card.name), manaCost(l.card.mana_cost)),
              l.reason ? h('span.reason', l.reason) : null);
            attachHoverPreview(row.querySelector('.dl-card'), l.card);
            return row;
          })));
      }))),
    h('section.stats-area', h('h2.area-title', 'Statistics'), h('div.stats-grid', statsPanels(draft.stats, { onOpen: openByName }))));
  page.partnerBox = partnerBox;
}

/** Partner / Background picker, shown only when the commander's rules text allows a second commander. */
async function partnerChoice(cmd, commanderId, partnerId, mine) {
  let text = '';
  try {
    const detail = await api.card(cmd.scryfall_id);
    text = [detail.card?.oracle_text, ...(detail.card?.faces || []).map(f => f.oracle_text)].filter(Boolean).join('\n');
  } catch { return; }
  if (mine !== draftToken) return;
  const background = /Choose a Background/i.test(text);
  const partner = /\bPartner\b|Friends forever|Doctor's companion/i.test(text);
  if (!background && !partner) return;
  let options;
  try {
    options = background
      ? (await api.search({ q: 't:background spare>=1', unique: 'cards', limit: 200, sort: 'name' })).results.map(c => ({ oracle_id: c.oracle_id, name: c.name }))
      : (lastList || (await api.build.commanders({ limit: 500 })).commanders).map(x => ({ oracle_id: x.card.oracle_id, name: x.card.name }));
  } catch { return; }
  if (mine !== draftToken) return;
  options = options.filter(o => o.oracle_id !== commanderId);
  const select = h('select.select', { 'aria-label': background ? 'Background' : 'Partner', onchange: e => {
    location.hash = `#/build/${commanderId}` + (e.target.value ? `?partner=${e.target.value}` : '');
  } }, h('option', { value: '' }, background ? 'No background' : 'No partner'),
  options.map(o => h('option', { value: o.oracle_id, selected: o.oracle_id === partnerId }, o.name)));
  page.partnerBox?.append(h('label.inline-label', background ? 'Background ' : 'Partner ', select),
    h('span.muted.small', background ? ' Backgrounds you have spare.' : ' Pick a second commander that also has Partner (or its named partner).'));
}
