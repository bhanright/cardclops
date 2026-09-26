// One deck: header (rename, format, status, priority, notes, replace/copy/delete), the list with
// allocations and pins, statistics, value history, suggestions from the collection and a sample hand.
import { h, clear, int, money, spinner, errorBox, manaCost, finishLabel, copyText, toast } from './util.js';
import { api } from './api.js';
import { attachHoverPreview, hideHoverPreview, cardFace, thumb } from './cards.js';
import { openCard } from './detail.js';
import { timeSeriesPanel } from './charts.js';
import { statsPanels } from './deckstats.js';
import { manafixControls } from './manafix.js';
import { copyPolicyControls, goldfishSection, historySection } from './deckextras.js';
import { DECK_FORMATS, deckFormatLabel, colorPips, invalidateDecks } from './decks.js';

const SECTIONS = ['commander', 'companion', 'main', 'sideboard', 'maybeboard'];
const SECTION_LABEL = { commander: 'Commander', companion: 'Companion', main: 'Main deck', sideboard: 'Sideboard', maybeboard: 'Maybeboard' };
const EXPORT_HEADER = { commander: 'Commander', companion: 'Companion', main: 'Deck', sideboard: 'Sideboard', maybeboard: 'Maybeboard' };
const CATEGORIES = ['Creature', 'Planeswalker', 'Battle', 'Instant', 'Sorcery', 'Artifact', 'Enchantment', 'Land', 'Other'];
const CATEGORY_PLURAL = { Creature: 'Creatures', Planeswalker: 'Planeswalkers', Battle: 'Battles', Instant: 'Instants', Sorcery: 'Sorceries',
  Artifact: 'Artifacts', Enchantment: 'Enchantments', Land: 'Lands', Other: 'Other' };

let host = null;
let deckId = null;
let data = null;           // {deck, lines, stats}
let token = 0;
let openLines = new Set(); // line_ids whose copy panel is open
let view = 'list';
let refocus = null;          // data-focus key to restore after a re-render
let notices = null;         // {title, warnings} from the last replace/sync, shown once

export function hideDeckPage() { token++; deckId = null; hideHoverPreview(); }

export function showDeckPage(container, id) {
  host = container;
  if (String(id) !== String(deckId)) { data = null; openLines = new Set(); }
  deckId = id;
  load();
}

async function load({ quiet = false } = {}) {
  const mine = ++token;
  if (!quiet || !data) clear(host).append(h('div.panel', spinner('Laying out the deck…')));
  let fresh;
  try { fresh = await api.decks.get(deckId); } catch (error) {
    if (mine === token) clear(host).append(h('a.back-link', { href: '#/decks' }, '← All decks'), errorBox(error.message));
    return;
  }
  if (mine !== token) return;
  data = fresh;
  const y = scrollY;
  render();
  if (quiet) scrollTo({ top: y });
  document.title = `${data.deck.name} · Decks · Collection Gallery`;
}

// ---------- helpers ----------
function orderedLines() {
  const out = [];
  for (const section of SECTIONS) {
    const lines = data.lines.filter(l => l.section === section);
    for (const cat of CATEGORIES) out.push(...lines.filter(l => (CATEGORIES.includes(l.category) ? l.category : 'Other') === cat)
      .sort((a, b) => (a.card?.cmc ?? 0) - (b.card?.cmc ?? 0) || a.name.localeCompare(b.name)));
  }
  return out;
}

/** Open the card detail for a line, with ←/→ moving through the deck's cards. */
function openLine(line) {
  const cards = orderedLines().filter(l => l.card).map(l => l.card);
  const index = Math.max(0, cards.findIndex(c => c === line.card));
  if (line.card) openCard(line.card.scryfall_id, { index, count: () => cards.length, get: i => Promise.resolve(cards[i]) });
}
function openByName(name) {
  const line = data.lines.find(l => l.name === name || l.card?.name === name || l.card?.name?.split(' // ')[0] === name);
  if (line) openLine(line);
}

async function patch(fields, message) {
  // Re-rendering replaces the header; put keyboard focus back on the control that was used.
  refocus = document.activeElement?.dataset?.focus || null;
  try {
    await api.decks.update(deckId, fields);
    invalidateDecks();
    if (message) toast(message);
    await load({ quiet: true });
  } catch (error) { toast('Could not save: ' + error.message); }
}

// ---------- render ----------
function render() {
  const { deck, stats } = data;
  hideHoverPreview();
  clear(host).append(
    h('a.back-link', { href: '#/decks' }, '← All decks'),
    header(deck),
    commanderPicker(deck) || '',
    h('nav.page-nav', { 'aria-label': 'Deck sections' },
      [['dk-list', 'List'], ['dk-stats', 'Stats'], ['dk-value', 'Value'], ['dk-sugg', 'Suggestions'], ['dk-hand', 'Sample hand'], ['dk-goldfish', 'Goldfish'], ['dk-history', 'History']]
        .map(([id, label]) => h('a.page-chip', { href: `#${id}`, onclick: e => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); } }, label))),
    listPanel(),
    statsArea(stats),
    valuePanel(),
    suggestionsPanel(),
    handPanel(),
    goldfishSection(data.deck),
    historySection(data.deck, { onRestored: reloadAfterChange }));
  if (refocus) { host.querySelector(`[data-focus="${refocus}"]`)?.focus({ preventScroll: true }); refocus = null; }
}

function statsArea(stats) {
  const fix = manafixControls(data.deck, { onApplied: () => { invalidateDecks(); return load({ quiet: true }); } });
  // A second, smaller way in from the Color balance panel, where the problem shows.
  const colorAction = h('button.btn.small', { type: 'button', onclick: () => fix.button.click() }, '⚙ Fix my mana base');
  return h('section#dk-stats.stats-area',
    h('div.area-head', h('h2.area-title', 'Statistics'), fix.button),
    fix.panel,
    h('div.stats-grid', statsPanels(stats, { onOpen: openByName, colorAction })));
}

function reloadAfterChange() { invalidateDecks(); return load({ quiet: true }); }

function header(deck) {
  const policy = copyPolicyControls(deck, { onChanged: reloadAfterChange });
  const total = (deck.owned || 0) + (deck.missing || 0);
  const pctOwned = total ? Math.round((deck.owned / total) * 100) : 100;

  // name with inline rename
  const nameBox = h('div.dh-name');
  const showName = () => clear(nameBox).append(h('h1', deck.name),
    h('button.icon-btn.small', { type: 'button', 'aria-label': 'Rename deck', title: 'Rename', dataset: { focus: 'rename' }, onclick: editName }, '✎'));
  const editName = () => {
    const input = h('input.text-input.name-input', { type: 'text', value: deck.name, 'aria-label': 'Deck name' });
    const save = () => { const v = input.value.trim(); if (v && v !== deck.name) { patch({ name: v }, 'Renamed'); refocus = 'rename'; } else { showName(); nameBox.querySelector('button')?.focus(); } };
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } if (e.key === 'Escape') { e.stopPropagation(); showName(); } });
    clear(nameBox).append(input, h('button.btn.small.go', { type: 'button', onclick: save }, 'Save'), h('button.btn.small.ghost', { type: 'button', onclick: showName }, 'Cancel'));
    input.focus(); input.select();
  };
  showName();

  const format = h('select.select', { 'aria-label': 'Format', dataset: { focus: 'format' }, onchange: e => patch({ format: e.target.value || null }, 'Format changed') },
    (DECK_FORMATS.includes(deck.format) || !deck.format ? DECK_FORMATS : [deck.format, ...DECK_FORMATS]).map(f => h('option', { value: f, selected: f === deck.format }, deckFormatLabel(f))));
  const status = h('div.seg', { role: 'group', 'aria-label': 'Status' },
    [['active', 'Active'], ['inactive', 'Inactive']].map(([v, l]) => h('button.seg-btn', { type: 'button', class: deck.status === v ? 'on' : null, 'aria-pressed': String(deck.status === v), dataset: { focus: 'status-' + v },
      title: v === 'active' ? 'Built: its copies are reserved' : 'An idea or retired list: reserves nothing',
      onclick: () => { if (deck.status !== v) patch({ status: v }, v === 'active' ? 'Deck is active: copies reserved' : 'Deck is inactive: copies released'); } }, l)));
  const priority = h('input.num-input', { type: 'number', step: 1, value: deck.priority ?? 0, 'aria-label': 'Priority', dataset: { focus: 'priority' } });
  priority.addEventListener('change', () => patch({ priority: parseInt(priority.value, 10) || 0 }, 'Priority saved'));

  const notes = h('textarea.notes', { 'aria-label': 'Notes', placeholder: 'Notes: upgrades to make, sleeves, where the box lives…' });
  notes.value = deck.notes || '';
  const noteState = h('span.muted.small');
  notes.addEventListener('change', async () => {
    noteState.textContent = 'Saving…';
    try { await api.decks.update(deckId, { notes: notes.value }); deck.notes = notes.value; noteState.textContent = '✓ Saved'; }
    catch (error) { noteState.textContent = '✗ ' + error.message; }
  });

  // replace list
  const replaceBox = h('div.replace-box', { hidden: true });
  const replaceText = h('textarea.deck-text', { 'aria-label': 'New decklist', spellcheck: 'false' });
  replaceBox.append(h('p.small.muted', 'Paste the new list. Pins survive for cards still in the list.'), replaceText,
    h('div.form-row',
      h('button.btn.go', { type: 'button', onclick: async e => {
        e.target.disabled = true;
        try {
          const r = await api.decks.replaceList(deckId, replaceText.value);
          invalidateDecks(); toast('List replaced');
          notices = r.warnings?.length ? { title: 'List replaced', warnings: r.warnings } : null;
          await load();
        }
        catch (error) { toast('Could not replace: ' + error.message); e.target.disabled = false; }
      } }, 'Save new list'),
      h('button.btn.ghost', { type: 'button', onclick: () => { replaceBox.hidden = true; } }, 'Cancel')));

  // delete with confirm
  const delBox = h('span.del-box');
  const showDelete = () => clear(delBox).append(h('button.btn.small.danger', { type: 'button', onclick: confirmDelete }, '✗ Delete'));
  const confirmDelete = () => {
    const yes = h('button.btn.small.danger', { type: 'button', onclick: async () => {
      try { await api.decks.remove(deckId); invalidateDecks(); toast(`Deleted “${deck.name}”`); location.hash = '#/decks'; }
      catch (error) { toast('Could not delete: ' + error.message); }
    } }, 'Yes, delete it');
    clear(delBox).append(h('span.confirm-text', `Delete “${deck.name}”?`), yes, h('button.btn.small.ghost', { type: 'button', onclick: showDelete }, 'Keep'));
    yes.focus();
  };
  showDelete();

  const missingDetail = deck.missing ? [
    deck.missing_not_owned ? `${int(deck.missing_not_owned)} not owned` : null,
    deck.missing_used_elsewhere ? `${int(deck.missing_used_elsewhere)} used by other decks` : null].filter(Boolean).join(' · ') : '';

  return h('section.panel.deck-head' + (deck.cover ? '.has-cover' : ''),
    deck.cover ? h('div.dh-banner', h('img', { src: deck.cover, alt: '', decoding: 'async' })) : null,
    h('div.dh-body',
      nameBox,
      h('div.dh-controls',
        h('label.inline-label', 'Format ', format),
        status,
        h('label.inline-label', { title: 'When active decks compete for the same copies, lower priority numbers get them first' }, 'Priority ', priority),
        colorPips(deck.color_identity),
        h('span', { class: 'legal-pill ' + (deck.legal ? 'ok' : 'bad') }, deck.legal ? '✓ Legal' : '✗ Not legal'),
        policy.control),
      policy.panel,
      h('div.bignums.dh-nums',
        h('div.bignum.c-cyan', h('div.bn-value', int(deck.card_count)), h('div.bn-label', 'Cards')),
        h('div.bignum.c-lime', h('div.bn-value', `${int(deck.owned)}`), h('div.bn-label', `Owned · ${pctOwned}%`)),
        h('div', { class: 'bignum ' + (deck.missing ? 'c-pink' : 'c-mute') }, h('div.bn-value', int(deck.missing)), h('div.bn-label', 'Missing'), missingDetail ? h('div.bn-sub', missingDetail) : null),
        h('div.bignum.c-orange', h('div.bn-value', money(deck.cost_to_complete_usd)), h('div.bn-label', 'To complete')),
        h('div.bignum.c-yellow', h('div.bn-value', money(deck.value_usd, { whole: deck.value_usd >= 1000 })), h('div.bn-label', 'Value of copies used'))),
      h('div.progress.big', { role: 'progressbar', 'aria-valuenow': pctOwned, 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-label': 'Copies owned' },
        h('span.progress-fill', { class: pctOwned >= 100 ? 'full' : null, style: { width: pctOwned + '%' } })),
      h('div.form-row.dh-actions',
        deck.source === 'archidekt' ? h('button.btn.small.go', { type: 'button', title: 'Re-read this deck from Archidekt; your pins are kept', onclick: async e => {
          e.target.disabled = true; e.target.textContent = 'Syncing…';
          try {
            const r = await api.decks.sync(deckId);
            invalidateDecks(); toast('Synced from Archidekt');
            notices = r.warnings?.length ? { title: 'Synced from Archidekt', warnings: r.warnings } : null;
            await load({ quiet: true });
          } catch (error) { toast('Sync failed: ' + error.message); e.target.disabled = false; e.target.textContent = '⟳ Sync from Archidekt'; }
        } }, '⟳ Sync from Archidekt') : null,
        h('button.btn.small', { type: 'button', onclick: () => { replaceText.value = exportText(false); replaceBox.hidden = !replaceBox.hidden; if (!replaceBox.hidden) replaceText.focus(); } }, '⟳ Replace list'),
        h('button.btn.small', { type: 'button', onclick: async () => toast((await copyText(exportText(false))) ? 'List copied' : 'Copy failed') }, '⧉ Copy list'),
        h('button.btn.small.ghost', { type: 'button', title: 'Moxfield-style lines naming the copies this deck uses: 1 Name (SET) 123 *F*',
          onclick: async () => toast((await copyText(exportText(true))) ? 'List with printings copied' : 'Copy failed') }, '⧉ Copy with printings'),
        delBox),
      replaceBox,
      h('details.notes-box', { open: !!deck.notes }, h('summary', 'Notes ', noteState), notes),
      deck.source_url ? h('p.small.muted', 'Imported from ', h('a', { href: deck.source_url, target: '_blank', rel: 'noopener' }, deck.source_url), ' ↗') : null,
      noticeBox()));
}

function noticeBox() {
  if (!notices) return null;
  const { title, warnings } = notices;
  notices = null;
  return h('details.warn-list.notice', { open: true },
    h('summary', `⚠ ${title}: ${warnings.length} line${warnings.length === 1 ? '' : 's'} not understood`),
    h('ul', warnings.map(w => h('li', w.line ? h('code', w.line) : null, w.line ? ' — ' : '', w.message))));
}

/** Commander-style decks with nobody in the command zone get a picker (two allowed, for partners). */
function commanderPicker(deck) {
  const needsOne = /commander|brawl|oathbreaker|predh|duel/.test(deck.format || '') && !data.lines.some(l => l.section === 'commander');
  if (!needsOne) return null;
  const can = l => l.oracle_id && l.card && /Legendary/.test(l.card.type_line || '') && /Creature|Planeswalker|Background/.test(l.card.type_line || '');
  const candidates = data.lines.filter(l => l.section !== 'maybeboard' && can(l));
  const chosen = new Set();
  const save = h('button.btn.go', { type: 'button', disabled: true, onclick: () => patch({ commanders: [...chosen] }, 'Commander set') }, 'Set commander');
  const buttons = candidates.map(l => {
    const b = h('button.pick', { type: 'button', 'aria-pressed': 'false', onclick: () => {
      if (chosen.has(l.oracle_id)) chosen.delete(l.oracle_id);
      else { if (chosen.size >= 2) return toast('Two commanders at most (partners)'); chosen.add(l.oracle_id); }
      for (const x of picker.querySelectorAll('.pick')) x.setAttribute('aria-pressed', String(chosen.has(x.dataset.oracle)));
      save.disabled = !chosen.size;
      save.textContent = chosen.size === 2 ? 'Set both as commanders' : 'Set commander';
    }, dataset: { oracle: l.oracle_id } },
    h('span.pick-art', h('img', { src: l.card.image.replace(/\/normal$/, '/art_crop'), alt: '', loading: 'lazy' })),
    h('span.pick-name', l.name), manaCost(l.card.mana_cost));
    return b;
  });
  const picker = h('section.panel.cmd-picker',
    h('h2', '★ Choose a commander'),
    h('p', `This ${deckFormatLabel(deck.format)} deck has nobody in the command zone, so color identity and legality can't be judged yet. `,
      'Pick one card below (or two partners).'),
    candidates.length ? h('div.pick-grid', buttons) : h('p.muted', 'No legendary creatures in the list. Replace the list with a commander section, or change the format.'),
    candidates.length ? h('div.form-row', save) : null);
  return picker;
}

/** Plain "1 Name" text, or with printings: one line per allocated copy pool, "1 Name (SET) 123 *F*". */
function exportText(withPrintings) {
  const blocks = [];
  for (const section of SECTIONS) {
    const lines = data.lines.filter(l => l.section === section);
    if (!lines.length) continue;
    const out = [EXPORT_HEADER[section]];
    for (const l of lines) {
      if (!withPrintings) { out.push(`${l.quantity} ${l.name}`); continue; }
      let left = l.quantity;
      for (const a of l.allocations || []) {
        out.push(`${a.quantity} ${l.name} (${(a.set_code || '').toUpperCase()}) ${a.collector_number}${finishMarker(a.finish)}`);
        left -= a.quantity;
      }
      if (left > 0) {
        const r = l.requested;
        out.push(r?.set_code ? `${left} ${l.name} (${r.set_code.toUpperCase()}) ${r.collector_number || ''}${finishMarker(r.finish)}`.replace(/ +$/, '')
          : l.card?.set_code ? `${left} ${l.name} (${l.card.set_code.toUpperCase()}) ${l.card.collector_number}` : `${left} ${l.name}`);
      }
    }
    blocks.push(out.join('\n'));
  }
  return blocks.join('\n\n');
}
const finishMarker = f => (f === 'foil' ? ' *F*' : f === 'etched' ? ' *E*' : '');

// ---------- list ----------
function listPanel() {
  const body = h('div.dk-list-body');
  const seg = h('div.seg', { role: 'group', 'aria-label': 'List layout' },
    [['list', 'List'], ['visual', 'Visual']].map(([v, l]) => h('button.seg-btn', { type: 'button', class: view === v ? 'on' : null, 'aria-pressed': String(view === v),
      onclick: () => { view = v; for (const b of seg.children) { b.classList.toggle('on', b.textContent === l ? true : false); b.setAttribute('aria-pressed', String(b.classList.contains('on'))); } drawList(body); } }, l)));
  drawList(body);
  return h('section#dk-list.panel.dk-list', h('div.panel-head', h('h2', 'The list'), seg), body);
}

function drawList(body) {
  clear(body);
  hideHoverPreview();
  for (const section of SECTIONS) {
    const lines = data.lines.filter(l => l.section === section);
    if (!lines.length) continue;
    const count = lines.reduce((s, l) => s + l.quantity, 0);
    const groups = CATEGORIES.map(cat => [cat, lines.filter(l => (CATEGORIES.includes(l.category) ? l.category : 'Other') === cat)
      .sort((a, b) => (a.card?.cmc ?? 0) - (b.card?.cmc ?? 0) || a.name.localeCompare(b.name))]).filter(([, ls]) => ls.length);
    const sec = h('section', { class: `dk-section sec-${section}` },
      h('h3.sec-title', SECTION_LABEL[section], h('span.muted', ` · ${int(count)}`), section === 'maybeboard' ? h('span.muted.small', ' — ideas; uses no copies') : null));
    if (view === 'visual') {
      sec.append(h('div.stack-cats', groups.map(([cat, ls]) => h('div.stack-cat',
        h('h4', section === 'commander' ? '' : CATEGORY_PLURAL[cat], h('span.muted', ` ${ls.reduce((s, l) => s + l.quantity, 0)}`)),
        h('div.card-stack', ls.map(visualCard))))));
    } else {
      for (const [cat, ls] of groups) {
        if (section !== 'commander' && section !== 'companion') sec.append(h('h4.cat-title', CATEGORY_PLURAL[cat], h('span.muted', ` (${ls.reduce((s, l) => s + l.quantity, 0)})`)));
        sec.append(h('div.dlines', ls.map(lineRow)));
      }
    }
    body.append(sec);
  }
}

function statusOf(line) {
  if (!line.oracle_id) return { cls: 'unknown', text: '? Unknown card', title: 'No card by this name was found' };
  if (line.section === 'maybeboard') return line.owned ? { cls: 'owned', text: '✓ You own it' } : { cls: 'maybe', text: '○ Not owned' };
  if (!line.missing) return { cls: 'owned', text: '✓ Owned' };
  const where = (line.used_elsewhere || []).map(d => d.name).join(', ');
  const partial = line.owned > 0;
  if (line.missing_reason === 'used_elsewhere') {
    return { cls: partial ? 'partial conflict' : 'conflict', text: `⇄ ${partial ? `${line.owned}/${line.quantity} · ` : ''}in ${where || 'another deck'}`,
      title: `Missing ${line.missing}: held by ${where || 'other active decks'}` };
  }
  return { cls: partial ? 'partial' : 'missing', text: partial ? `◐ ${line.owned} of ${line.quantity}` : '✗ Missing', title: `Missing ${line.missing}: not owned` };
}

function copyChip(a, { price = true } = {}) {
  return h('span', { class: 'copy-chip' + (a.pinned ? ' pinned' : ''), title: `${a.quantity} × ${(a.set_code || '').toUpperCase()} #${a.collector_number}, ${finishLabel(a.finish)}${a.pinned ? ', pinned' : ''}` },
    a.pinned ? h('span.pin-mark', { 'aria-label': 'pinned' }, '⚑ ') : null,
    `${a.quantity > 1 ? a.quantity + '× ' : ''}${(a.set_code || '').toUpperCase()} #${a.collector_number}`,
    a.finish !== 'normal' ? h('span.foil-mark', ' ✦') : null,
    price && a.price_usd != null ? h('span.cc-price', money(a.price_usd)) : null);
}

function lineRow(line) {
  const st = statusOf(line);
  const allocs = line.allocations || [];
  const value = allocs.reduce((s, a) => s + (a.price_usd || 0) * a.quantity, 0);
  const hasPanel = line.oracle_id && line.section !== 'maybeboard' && ((line.alternatives || []).length || allocs.length);
  const open = openLines.has(line.line_id);
  const panelId = `copies-${line.line_id}`;
  const row = h('div', { class: `dline st-${st.cls.split(' ')[0]}${st.cls.includes('conflict') ? ' has-conflict' : ''}` },
    h('span.dl-q', int(line.quantity)),
    h('button.dl-card', { type: 'button', disabled: !line.card, onclick: () => openLine(line) },
      h('span.dl-name', line.name), line.card?.mana_cost ? manaCost(line.card.mana_cost) : null),
    h('span.dl-status', { title: st.title || '' }, st.text),
    h('span.dl-copies', allocs.map(a => copyChip(a, { price: false }))),
    h('span.dl-value', value ? money(value) : line.card?.price_usd != null ? h('span.muted', money(line.card.price_usd)) : '—'),
    hasPanel ? h('button.dl-more', { type: 'button', 'aria-expanded': String(open), 'aria-controls': panelId, dataset: { focus: `more-${line.line_id}` }, title: 'Choose which copies this line uses',
      onclick: () => {
        if (openLines.has(line.line_id)) openLines.delete(line.line_id); else openLines.add(line.line_id);
        const target = row.parentElement?.classList.contains('dline-wrap') ? row.parentElement : row;
        const fresh = lineRow(line);
        target.replaceWith(fresh);
        fresh.querySelector('.dl-more')?.focus();
      } },
    open ? 'Copies ▴' : 'Copies ▾') : h('span'));
  if (line.card) attachHoverPreview(row.querySelector('.dl-card'), line.card);
  if (!open || !hasPanel) return row;
  return h('div.dline-wrap', row, copiesPanel(line, panelId));
}

function copiesPanel(line, id) {
  const allocs = line.allocations || [];
  const alts = line.alternatives || [];
  const pin = async (pool, quantity, button) => {
    button.disabled = true;
    try {
      await api.decks.pin(deckId, line.line_id, pool, quantity);
      toast(quantity ? 'Pinned' : 'Pin removed'); invalidateDecks();
      refocus = `more-${line.line_id}`;
      await load({ quiet: true });
    }
    catch (error) { toast('Could not pin: ' + error.message); button.disabled = false; }
  };
  const isCommanderFormat = /commander|brawl|oathbreaker|predh|duel/.test(data.deck.format || '');
  const canLead = isCommanderFormat && line.section === 'main' && /Legendary/.test(line.card?.type_line || '') && /Creature|Planeswalker/.test(line.card?.type_line || '');
  return h('div.copies-panel', { id },
    h('div.cp-col',
      h('h4', 'Using now'),
      allocs.length ? h('ul.cp-list', allocs.map(a => h('li', copyChip(a),
        a.pinned ? h('button.btn.small.ghost', { type: 'button', onclick: e => pin(a.pool, 0, e.target) }, 'Unpin')
          : h('button.btn.small.ghost', { type: 'button', title: 'Keep this copy even if allocation changes', onclick: e => pin(a.pool, a.quantity, e.target) }, '⚑ Pin')))) : h('p.muted.small', 'No copies allocated.')),
    h('div.cp-col',
      h('h4', 'Other copies you could use'),
      alts.length ? h('ul.cp-list', alts.map(a => h('li',
        h('span.copy-chip', `${(a.set_code || '').toUpperCase()} #${a.collector_number}`, a.finish !== 'normal' ? h('span.foil-mark', ' ✦ ' + finishLabel(a.finish)) : null,
          a.price_usd != null ? h('span.cc-price', money(a.price_usd)) : null),
        h('span.muted.small', ` ${int(a.free)} free`),
        h('button.btn.small', { type: 'button', disabled: !a.free, onclick: e => pin(a.pool, Math.min(a.free, line.quantity), e.target) },
          `⚑ Pin ${Math.min(a.free, line.quantity) || ''}`.trim())))) : h('p.muted.small', 'No other copies.')),
    canLead ? h('div.cp-col', h('button.btn.small', { type: 'button', onclick: () => {
      const current = data.lines.filter(l => l.section === 'commander' && l.oracle_id).map(l => l.oracle_id);
      patch({ commanders: [...current, line.oracle_id] }, `${line.name} is now a commander`);
    } }, '★ Make commander')) : null);
}

function visualCard(line) {
  const st = statusOf(line);
  const card = line.card;
  const copies = [];
  for (let i = 0; i < Math.min(line.quantity, 4); i++) copies.push(i);
  return h('button', { type: 'button', class: `vcard st-${st.cls.split(' ')[0]}`, title: `${line.quantity} × ${line.name} — ${st.text}`,
    'aria-label': `${line.quantity} ${line.name}, ${st.text}`, disabled: !card, onclick: () => openLine(line) },
  card ? cardFace({ ...card, finish: (line.allocations?.[0]?.finish) || 'normal' }, { size: 'normal', flip: false }).node : h('div.card-face.unknown-face', line.name),
  line.quantity > 1 ? h('span.qty.small', '×' + line.quantity) : null,
  line.missing && line.section !== 'maybeboard' ? h('span.miss-band', { title: st.title || st.text }, st.cls.includes('conflict') ? `⇄ ${line.missing}` : `✗ ${line.missing}`) : null);
}

// ---------- value history ----------
function valuePanel() {
  const box = h('div', spinner('Loading value history…'));
  const note = h('p.small.muted');
  api.decks.valueHistory(deckId).then(v => {
    timeSeriesPanel(clear(box), { series: [{ label: 'Deck value', points: v.points || [] }], height: 200,
      format: x => money(x, { whole: x >= 1000 }), emptyNote: 'Value history starts today; it grows each refresh.' });
    note.textContent = v.source_label ? `The copies this deck uses now, at each day’s prices (${v.source_label}).` : 'The copies this deck uses now, at each day’s prices.';
  }, error => clear(box).append(errorBox(error.message)));
  return h('section#dk-value.panel', h('h2', 'Value history'), note, box);
}

// ---------- suggestions ----------
function suggestionsPanel() {
  const box = h('div', spinner('Looking through your binders…'));
  api.decks.suggestions(deckId).then(s => {
    const cards = s.cards || [];
    clear(box);
    if (!cards.length) { box.append(h('div.chart-empty', 'No spare cards in your collection fit this deck’s colors and format.')); return; }
    const nav = i => ({ index: i, count: () => cards.length, get: j => Promise.resolve(cards[j]) });
    // Scores are only comparable within one list, so show them relative to the best match.
    const best = Math.max(...cards.map(c => c.score || 0)) || 1;
    box.append(h('div.sugg-row', cards.map((c, i) => h('div.sugg',
      thumb(c, { onOpen: () => openCard(c.scryfall_id, nav(i)), caption: [
        h('span.match', { title: `Similarity score ${(c.score || 0).toFixed(2)}`, 'aria-label': `Match ${Math.round(((c.score || 0) / best) * 100)}% of the best` },
          h('span.match-fill', { style: { width: `${((c.score || 0) / best) * 100}%` } })),
        `${int(c.spare)} spare`] }),
      c.because?.length ? h('div.because', 'like ', c.because.slice(0, 3).join(', ')) : null))));
  }, error => clear(box).append(errorBox(error.message)));
  return h('section#dk-sugg.panel', h('div.panel-head', h('h2', 'Suggestions from your collection'),
    h('span.muted.small', 'Spare cards in the deck’s colors and format, most like what it already plays')), box);
}

// ---------- sample hand ----------
function handPanel() {
  const library = [];
  for (const l of data.lines) if (l.section === 'main' && l.card) for (let i = 0; i < l.quantity; i++) library.push(l);
  const table = h('div.hand-cards', { 'aria-live': 'polite' });
  const info = h('p.small.muted');
  let deck = [], hand = [], mulligans = 0;
  const shuffle = () => {
    deck = [...library];
    for (let i = deck.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [deck[i], deck[j]] = [deck[j], deck[i]]; }
  };
  const draw = () => {
    clear(table);
    hand.forEach(l => table.append(h('button.hand-card', { type: 'button', 'aria-label': l.name, onclick: () => openLine(l) },
      cardFace({ ...l.card, finish: 'normal' }, { size: 'normal', flip: true }).node)));
    const lands = hand.filter(l => l.category === 'Land').length;
    info.textContent = `${hand.length} cards · ${lands} land${lands === 1 ? '' : 's'}${mulligans ? ` · mulligan ${mulligans}: put ${mulligans} on the bottom (London)` : ''} · ${deck.length} left in library`;
  };
  const newHand = () => { mulligans = 0; shuffle(); hand = deck.splice(0, 7); draw(); };
  const buttons = h('div.form-row',
    h('button.btn.go', { type: 'button', onclick: newHand }, '⟳ New hand'),
    h('button.btn', { type: 'button', onclick: () => { mulligans++; shuffle(); hand = deck.splice(0, 7); draw(); } }, 'Mulligan'),
    h('button.btn.ghost', { type: 'button', onclick: () => { if (deck.length) { hand.push(deck.shift()); draw(); } } }, '＋ Draw'));
  const section = h('section#dk-hand.panel', h('div.panel-head', h('h2', 'Sample hand'), buttons), info, table);
  if (library.length >= 7) newHand(); else { info.textContent = 'Needs at least 7 cards in the main deck.'; buttons.hidden = true; }
  return section;
}

