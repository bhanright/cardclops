// Decks tab: the deck grid with totals, filters and sorting, plus the import panel (paste or files).
import { h, $, clear, int, money, spinner, errorBox, store, symbol, formatLabel, toast, debounce } from './util.js';
import { api } from './api.js';
import { lazyImg } from './cards.js';
import { showDeckPage, hideDeckPage } from './deckpage.js';
import { browserEdition } from './edition.js';
import { dialog } from './setup.js';

export const DECK_FORMATS = ['commander', 'standard', 'pioneer', 'modern', 'legacy', 'vintage', 'pauper', 'paupercommander',
  'oathbreaker', 'brawl', 'standardbrawl', 'historic', 'timeless', 'alchemy', 'explorer', 'penny', 'premodern',
  'oldschool', 'redux', 'predh', 'duel', 'gladiator', 'casual'];
export const deckFormatLabel = f => (f === 'casual' ? 'Casual' : f ? formatLabel(f) : 'No format');
// Formats built around a commander (gallery/deckstats.py COMMANDER_FORMAT_SIZES).
export const COMMANDER_FORMATS = ['commander', 'paupercommander', 'oathbreaker', 'brawl', 'standardbrawl', 'predh', 'duel'];

const PREFS_KEY = 'gallery.decks';
const prefs = { filter: 'all', sort: 'name', dir: 'asc', format: '', folder: 'all', q: '', ...store.get(PREFS_KEY, {}) };
let built = false;
let home, page, grid, totalsBox, toolbar, folderBar, importPanel, searchNote;
let lastData = null;

/** Show the deck grid (deckId null) or one deck's page. */
export function showDecks(deckId) {
  const root = $('#view-decks');
  if (!built) {
    built = true;
    home = h('div.decks-home');
    page = h('div.deck-page', { hidden: true });
    root.append(home, page);
    buildHome();
  }
  if (deckId != null) {
    home.hidden = true;
    page.hidden = false;
    showDeckPage(page, deckId);
  } else {
    hideDeckPage();
    page.hidden = true;
    home.hidden = false;
    load();
  }
}

// ---------- home ----------
function buildHome() {
  const importBtn = h('button.btn', { type: 'button', 'aria-expanded': 'false', onclick: () => toggleImport() }, '＋ Import decks');
  const newBtn = h('button.btn.go', { type: 'button', onclick: newDeckDialog }, '＋ New deck');
  // Every deck in one file, to move them to another copy of Cardclops (the phone, cardclops.com).
  const exportLink = h('a.btn.ghost', { href: api.decks.exportUrl, download: '', title: 'Save every deck in one file, to import into another copy of Cardclops' }, '⇩ Export all decks');
  toolbar = h('div.panel-tools');
  folderBar = h('div.folder-bar', { role: 'group', 'aria-label': 'Folders' });
  totalsBox = h('div.deck-totals');
  importPanel = buildImportPanel();
  importPanel.hidden = true;
  grid = h('div.deck-grid', { 'aria-live': 'polite' });
  home.append(
    h('section.panel.decks-head',
      h('div.panel-head', h('h2', 'Decks'), h('div.panel-tools', toolbar, exportLink, importBtn, newBtn)),
      totalsBox,
      folderBar,
      deckSearch()),
    importPanel,
    grid);
  home.importBtn = importBtn;
  buildToolbar();
}

/** Decks → New deck: a name, a format and (for a commander format) the commander. The deck page
 *  that opens next adds the cards: search one at a time, or paste a list. */
function newDeckDialog() {
  const name = h('input.text-input', { type: 'text', placeholder: 'e.g. Atraxa Superfriends', 'aria-label': 'Deck name', maxlength: 120 });
  const format = h('select.select', { 'aria-label': 'Format' },
    DECK_FORMATS.map(f => h('option', { value: f, selected: f === 'commander' }, deckFormatLabel(f))));
  const search = h('input.text-input', { type: 'search', placeholder: 'Search a card name', 'aria-label': 'Commander', autocomplete: 'off' });
  const names = h('div.lookup-names');
  const picked = h('p.small.muted', 'No commander yet: you can add one later from the deck page.');
  let commander = null;
  const commanderRow = h('div.new-deck-commander',
    h('label.inline-label', h('b', 'Commander')), search, names, picked,
    h('p.small.muted', 'Partners and Backgrounds can join it from the deck page.'));
  const showCommander = () => { commanderRow.hidden = !COMMANDER_FORMATS.includes(format.value); };
  format.addEventListener('change', showCommander);
  showCommander();
  const choose = (card, button) => {
    commander = card;
    for (const b of names.children) b.setAttribute('aria-pressed', String(b === button));
    picked.textContent = `Commander: ${card.name}`;
    if (!name.value.trim()) name.placeholder = card.name;
  };
  search.addEventListener('input', debounce(async () => {
    const q = search.value.trim();
    clear(names);
    if (q.length < 2) return;
    let cards;
    try { cards = (await api.lookup({ q })).cards || []; } catch (error) { names.append(h('span.muted.small', error.message)); return; }
    if (q !== search.value.trim()) return;
    if (!cards.length) { names.append(h('span.muted.small', 'No card by that name.')); return; }
    names.append(...cards.map(card => {
      const b = h('button.mini-chip', { type: 'button', 'aria-pressed': 'false', onclick: () => choose(card, b) }, card.name);
      return b;
    }));
  }, 300));
  const folders = lastData?.folders || [];
  const folder = h('select.select', { 'aria-label': 'Folder' }, h('option', { value: '' }, 'No folder'),
    folders.map(f => h('option', { value: f.folder_id, selected: f.folder_id === prefs.folder }, f.name)));
  const status = h('div');
  const create = h('button.btn.go', { type: 'button', onclick: async () => {
    create.disabled = true;
    const useCommander = commander && COMMANDER_FORMATS.includes(format.value);
    try {
      const deck = await api.decks.create({ name: name.value.trim() || (useCommander ? commander.name : ''), format: format.value,
        commanders: useCommander ? [commander.oracle_id] : [], folder_id: folder.value ? +folder.value : null });
      invalidateDecks();
      close();
      location.hash = `#/decks/${deck.deck_id}`;
    } catch (error) { clear(status).append(errorBox(error.message)); create.disabled = false; }
  } }, 'Create deck');
  const close = dialog('New deck', h('div.new-deck',
    h('label.new-deck-field', h('b', 'Name'), name),
    h('label.new-deck-field', h('b', 'Format'), format),
    folders.length ? h('label.new-deck-field', h('b', 'Folder'), folder) : null,
    commanderRow,
    h('p.small.muted', 'Next, add cards on the deck page: search for them one at a time, or paste a list.'),
    status,
    h('div.form-row', create)));
  name.focus();
}

function toggleImport(force) {
  const open = force ?? importPanel.hidden;
  importPanel.hidden = !open;
  home.importBtn.setAttribute('aria-expanded', String(open));
  home.importBtn.textContent = open ? '× Close import' : '＋ Import decks';
  if (open) importPanel.querySelector('input, textarea')?.focus();
}

/** The deck search box (gallery/decksearch.py has the syntax) and its cheat sheet. */
function deckSearch() {
  const input = h('input.search-input.deck-search-input', { type: 'search', value: prefs.q, autocomplete: 'off', spellcheck: 'false',
    placeholder: 'Search decks… try  c:simic  card:"sol ring"  is:active', 'aria-label': 'Search decks' });
  const run = debounce(() => { prefs.q = input.value.trim(); store.set(PREFS_KEY, prefs); load(); }, 300);
  input.addEventListener('input', run);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); run.cancel?.(); prefs.q = input.value.trim(); store.set(PREFS_KEY, prefs); load(); } });
  const help = h('div.deck-search-help.small', { hidden: true },
    h('table', h('tbody', [
      ['atraxa', 'deck or commander name contains the words ("quotes" for a phrase)'],
      ['c:g   c:simic   c:c', 'color identity includes these colors (c:c is colorless)'],
      ['c=ug   c<=ug', 'exactly these colors; within these colors'],
      ['card:"sol ring"', 'a card in the deck (not the maybeboard) has this in its name'],
      ['cmd:tymna', 'a commander has this in its name'],
      ['f:commander   f:modern', 'format'],
      ['is:active   is:inactive   is:legal   is:illegal', 'status and legality'],
      ['is:complete   is:incomplete   is:conflict', 'all copies owned; some missing; short because another deck holds them'],
      ['cards>=100   missing>0   value>200   cost<50', 'numbers (also owned, priority)'],
      ['-card:"sol ring"', 'a leading minus excludes'],
    ].map(([q, what]) => h('tr', h('td', h('code', q)), h('td', what))))));
  const helpBtn = h('button.icon-btn', { type: 'button', 'aria-label': 'Deck search syntax', 'aria-expanded': 'false', title: 'Search syntax',
    onclick: () => { help.hidden = !help.hidden; helpBtn.setAttribute('aria-expanded', String(!help.hidden)); } }, '?');
  searchNote = h('div.deck-search-note.small', { 'aria-live': 'polite' });
  return h('div.deck-search', h('div.deck-search-row', input, helpBtn), searchNote, help);
}

// Sorts: [key, label, the direction it starts in, compare(a, b) for ascending].
const DECK_SORTS = [
  ['name', 'Name', 'asc', (a, b) => a.name.localeCompare(b.name)],
  ['value', 'Value', 'desc', (a, b) => (a.value_usd || 0) - (b.value_usd || 0)],
  ['cost', 'Cost to complete', 'desc', (a, b) => (a.cost_to_complete_usd || 0) - (b.cost_to_complete_usd || 0)],
  ['missing', 'Missing cards', 'desc', (a, b) => (a.missing || 0) - (b.missing || 0)],
  ['cards', 'Card count', 'desc', (a, b) => (a.card_count || 0) - (b.card_count || 0)],
  ['created', 'Date created', 'desc', (a, b) => (a.created_at || '').localeCompare(b.created_at || '')],
  ['updated', 'Last updated', 'desc', (a, b) => (a.updated_at || '').localeCompare(b.updated_at || '')],
  ['format', 'Format', 'asc', (a, b) => deckFormatLabel(a.format).localeCompare(deckFormatLabel(b.format))],
  ['colors', 'Colors', 'asc', (a, b) => colorRank(a) - colorRank(b)],
  ['priority', 'Priority', 'asc', (a, b) => (a.priority || 0) - (b.priority || 0)],
];
const colorRank = d => (d.color_identity || []).reduce((n, c) => n * 6 + 1 + 'WUBRG'.indexOf(c), d.color_identity?.length || 0);

function savePrefs() { store.set(PREFS_KEY, prefs); }

function buildToolbar() {
  clear(toolbar);
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Show decks' },
    [['active', 'Active'], ['inactive', 'Inactive'], ['all', 'All']].map(([value, label]) => h('button.seg-btn', {
      type: 'button', class: prefs.filter === value ? 'on' : null, 'aria-pressed': String(prefs.filter === value),
      onclick: () => { prefs.filter = value; store.set(PREFS_KEY, prefs); buildToolbar(); renderGrid(); } }, label)));
  const formats = [...new Set((lastData?.decks || []).map(d => d.format).filter(Boolean))].sort((a, b) => deckFormatLabel(a).localeCompare(deckFormatLabel(b)));
  if (prefs.format && !formats.includes(prefs.format)) formats.push(prefs.format);
  const format = h('select.select', { 'aria-label': 'Format', onchange: e => { prefs.format = e.target.value; savePrefs(); renderGrid(); } },
    h('option', { value: '' }, 'All formats'), formats.map(f => h('option', { value: f, selected: prefs.format === f }, deckFormatLabel(f))));
  const sort = h('select.select', { 'aria-label': 'Sort decks', onchange: e => {
    prefs.sort = e.target.value;
    prefs.dir = DECK_SORTS.find(s => s[0] === prefs.sort)?.[2] || 'asc';        // each sort starts the useful way round
    savePrefs(); buildToolbar(); renderGrid();
  } }, DECK_SORTS.map(([v, l]) => h('option', { value: v, selected: prefs.sort === v }, l)));
  const dir = h('button.btn.small.dir', { type: 'button', 'aria-label': `Sort direction: ${prefs.dir === 'asc' ? 'ascending' : 'descending'}`,
    onclick: () => { prefs.dir = prefs.dir === 'asc' ? 'desc' : 'asc'; savePrefs(); buildToolbar(); renderGrid(); } },
    prefs.dir === 'asc' ? '↑ Asc' : '↓ Desc');
  toolbar.append(seg, format, h('label.sort-label', h('span.sort-caption', 'Sort'), sort), dir);
}

/** Ask for a name in the app's own dialog (the browser's prompt() isn't shown in every edition's
 *  web view). Resolves to the trimmed name, or null when cancelled. */
function askName(title, action, initial = '') {
  return new Promise(resolve => {
    let answered = false;
    const input = h('input.text-input', { type: 'text', value: initial, maxlength: 80, 'aria-label': 'Folder name' });
    const done = value => { answered = true; close(); resolve(value); };
    const ok = h('button.btn.go', { type: 'button', onclick: () => input.value.trim() && done(input.value.trim()) }, action);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); ok.click(); } });
    const close = dialog(title, h('div.new-deck', input, h('div.form-row', ok,
      h('button.btn.ghost', { type: 'button', onclick: () => done(null) }, 'Cancel'))), { onClose: () => { if (!answered) resolve(null); } });
    input.focus(); input.select();
  });
}

function askSure(title, message, action) {
  return new Promise(resolve => {
    let answered = false;
    const done = value => { answered = true; close(); resolve(value); };
    const close = dialog(title, h('div.new-deck', h('p', message), h('div.form-row',
      h('button.btn.go', { type: 'button', onclick: () => done(true) }, action),
      h('button.btn.ghost', { type: 'button', onclick: () => done(false) }, 'Cancel'))), { onClose: () => { if (!answered) resolve(false); } });
  });
}

/** The folder chips: all decks, each folder, decks in none; the chosen folder can be renamed or deleted. */
function renderFolders() {
  const folders = lastData?.folders || [];
  if (prefs.folder !== 'all' && prefs.folder !== 'none' && !folders.some(f => f.folder_id === prefs.folder)) prefs.folder = 'all';
  const all = lastData?.decks || [];
  const chip = (value, label, count) => h('button.mini-chip.folder-chip', { type: 'button', 'aria-pressed': String(prefs.folder === value),
    onclick: () => { prefs.folder = value; savePrefs(); renderFolders(); renderGrid(); } }, label, h('span.muted', ` ${int(count)}`));
  const add = h('button.mini-chip.folder-add', { type: 'button', onclick: async () => {
    const name = await askName('New folder', 'Make folder');
    if (!name) return;
    try { const r = await api.decks.folders.create(name); prefs.folder = r.folder_id; savePrefs(); toast('Folder made'); load(); }
    catch (error) { toast('Could not make it: ' + error.message); }
  } }, '＋ New folder');
  clear(folderBar).append(...[
    chip('all', 'All decks', all.length),
    ...folders.map(f => chip(f.folder_id, `📁 ${f.name}`, all.filter(d => d.folder_id === f.folder_id).length)),
    folders.length ? chip('none', 'No folder', all.filter(d => !d.folder_id).length) : null,
    add].filter(Boolean));
  const current = folders.find(f => f.folder_id === prefs.folder);
  if (current) {
    folderBar.append(
      h('button.link-btn.small', { type: 'button', onclick: async () => {
        const name = await askName('Rename folder', 'Rename', current.name);
        if (!name || name === current.name) return;
        try { await api.decks.folders.rename(current.folder_id, name); load(); } catch (error) { toast('Could not rename it: ' + error.message); }
      } }, 'Rename'),
      h('button.link-btn.small', { type: 'button', onclick: async () => {
        if (!await askSure('Delete folder', `Delete the folder “${current.name}”? Its decks stay, in no folder.`, 'Delete folder')) return;
        try { await api.decks.folders.remove(current.folder_id); prefs.folder = 'all'; savePrefs(); load(); } catch (error) { toast('Could not delete it: ' + error.message); }
      } }, 'Delete folder'));
  }
}

async function load() {
  if (!lastData) clear(grid).append(spinner('Shuffling decks…'));
  const asked = prefs.q;
  try {
    const data = await api.decks.list(asked);
    if (asked !== prefs.q) return;                  // a newer search is on its way
    lastData = data;
    searchNote?.classList.remove('bad');
  } catch (error) {
    if (asked !== prefs.q) return;
    if (asked && searchNote) { clear(searchNote).append(`⚠ ${error.message}`); searchNote.classList.add('bad'); return; }
    clear(grid).append(errorBox(error.message));
    return;
  }
  renderTotals(lastData.totals || {});
  buildToolbar();
  renderFolders();
  renderGrid();
}

function renderTotals(t) {
  clear(totalsBox).append(h('div.bignums',
    h('div.bignum.c-lime', h('div.bn-value', int(t.active ?? 0)), h('div.bn-label', 'Active decks')),
    h('div.bignum.c-violet', h('div.bn-value', int(t.inactive ?? 0)), h('div.bn-label', 'Inactive')),
    h('div.bignum.c-cyan', h('div.bn-value', int(t.copies_in_decks ?? 0)), h('div.bn-label', 'Copies in decks')),
    h('div.bignum.c-yellow', h('div.bn-value', money(t.value_in_decks_usd ?? 0, { whole: (t.value_in_decks_usd ?? 0) >= 1000 })), h('div.bn-label', 'Value in decks')),
    h('div', { class: 'bignum ' + (t.conflicts ? 'c-pink' : 'c-mute'), title: 'Deck lines short of copies because another active deck holds them' },
      h('div.bn-value', (t.conflicts ? '⚠ ' : '') + int(t.conflicts ?? 0)), h('div.bn-label', 'Conflicts'))));
}

function renderGrid() {
  if (!lastData) return;
  const decks = (lastData.decks || []).filter(d => (prefs.filter === 'all' || d.status === prefs.filter)
    && (!prefs.format || d.format === prefs.format)
    && (prefs.folder === 'all' || (prefs.folder === 'none' ? !d.folder_id : d.folder_id === prefs.folder)));
  const [, , , compare] = DECK_SORTS.find(s => s[0] === prefs.sort) || DECK_SORTS[0];
  const sign = prefs.dir === 'desc' ? -1 : 1;
  decks.sort((a, b) => sign * compare(a, b) || a.name.localeCompare(b.name));
  clear(grid);
  const deckCount = (lastData.totals?.active ?? 0) + (lastData.totals?.inactive ?? 0);   // all decks, whatever the search
  if (!deckCount) {
    grid.append(h('div.empty', h('div.blob-eye', { 'aria-hidden': 'true' }),
      h('p', 'No decks yet. Paste a list or drop some deck files to get started.'),
      h('button.btn.go', { type: 'button', onclick: () => toggleImport(true) }, '＋ Import decks')));
    return;
  }
  if (searchNote) clear(searchNote).append(lastData.query ? `${int(decks.length)} ${decks.length === 1 ? 'deck matches' : 'decks match'}` : '');
  if (!decks.length) { grid.append(h('div.chart-empty', lastData.query ? 'No decks match that search.' : 'No decks match these filters.')); return; }
  grid.append(...decks.map(deckTile));
}

export function colorPips(identity) {
  const colors = (identity || []).filter(c => 'WUBRG'.includes(c));
  return h('span.pips', { 'aria-label': colors.length ? `Color identity ${colors.join('')}` : 'Colorless' },
    colors.length ? colors.map(c => symbol(c)) : symbol('C'));
}

function deckTile(d) {
  const total = (d.owned || 0) + (d.missing || 0);
  const pct = total ? Math.round((d.owned / total) * 100) : 100;
  const conflict = d.status === 'active' && d.missing_used_elsewhere > 0;   // an inactive deck reserves nothing, so it can't conflict
  return h('a', { class: `deck-tile ${d.status}`, href: `#/decks/${d.deck_id}`, 'aria-label': `${d.name}, ${deckFormatLabel(d.format)}, ${d.status}` },
    h('div.dt-cover',
      d.cover ? lazyImg(d.cover, '', 'dt-art') : h('div.dt-art.placeholder', { 'aria-hidden': 'true' }),
      h('span', { class: 'status-pill ' + (d.status === 'active' ? 'on' : 'off') }, d.status === 'active' ? '● Active' : '○ Inactive'),
      h('span', { class: 'legal-pill ' + (d.legal ? 'ok' : 'bad'), title: d.legal ? `Legal in ${deckFormatLabel(d.format)}` : `Not legal in ${deckFormatLabel(d.format)}` },
        d.legal ? '✓ Legal' : '✗ Not legal'),
      conflict ? h('span.conflict-pill', { title: `${d.missing_used_elsewhere} missing ${d.missing_used_elsewhere === 1 ? 'copy is' : 'copies are'} held by other active decks` }, `⇄ ${d.missing_used_elsewhere} used elsewhere`) : null),
    h('div.dt-body',
      h('div.dt-name', d.name),
      h('div.dt-meta', h('span.fmt', deckFormatLabel(d.format)), colorPips(d.color_identity), h('span.muted', `${int(d.card_count)} cards`),
        d.copy_policy && d.copy_policy !== 'default' ? h('span', { class: 'policy-mark ' + d.copy_policy, title: d.copy_policy === 'budget' ? 'Uses your cheapest copies' : 'Uses your fanciest copies' }, d.copy_policy === 'budget' ? '$ budget' : '✦ bling') : null),
      h('div.progress', { role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': pct, 'aria-label': 'Copies owned' },
        h('span.progress-fill', { class: pct >= 100 ? 'full' : null, style: { width: pct + '%' } })),
      h('div.dt-foot',
        h('span', d.missing ? [h('b', int(d.owned)), `/${int(total)} · `, h('span.miss', `${int(d.missing)} missing`)] : [h('b', '✓ '), 'complete']),
        h('span.dt-value', money(d.value_usd, { whole: d.value_usd >= 1000 })))));
}

// ---------- import ----------
function formatSelect(label, value = '') {
  return h('select.select', { 'aria-label': label },
    h('option', { value: '', selected: !value }, 'Auto-detect'),
    DECK_FORMATS.map(f => h('option', { value: f, selected: f === value }, deckFormatLabel(f))));
}

function statusSeg(get, set) {
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Status' });
  const draw = () => {
    clear(seg).append(...[['active', 'Active'], ['inactive', 'Inactive']].map(([v, l]) =>
      h('button.seg-btn', { type: 'button', class: get() === v ? 'on' : null, 'aria-pressed': String(get() === v), onclick: () => { set(v); draw(); } }, l)));
  };
  draw();
  return seg;
}

function buildImportPanel() {
  let status = 'active';
  const files = [];                       // {name, text, source}
  const name = h('input.text-input', { type: 'text', placeholder: 'Deck name', 'aria-label': 'Deck name' });
  const format = formatSelect('Format');
  const text = h('textarea.deck-text', { spellcheck: 'false', 'aria-label': 'Decklist',
    placeholder: 'Commander\n1 Atraxa, Praetors\' Voice\n\nDeck\n1 Sol Ring (C21) 263\n1 Arcane Signet *F*\n…' });
  const fileInput = h('input', { type: 'file', multiple: true, accept: '.txt,.dek,.dck,.csv,text/plain,text/csv', hidden: true });
  const fileList = h('ul.file-list');
  const results = h('div.import-results', { 'aria-live': 'polite' });
  const go = h('button.btn.go', { type: 'submit' }, 'Import');

  const drawFiles = () => {
    clear(fileList);
    files.forEach((f, i) => fileList.append(h('li',
      h('input.text-input', { type: 'text', value: f.name, 'aria-label': `Deck name for ${f.file}`, oninput: e => { f.name = e.target.value; } }),
      h('span.muted.small', `${f.file} · ${f.lines} lines`),
      h('button.link-btn', { type: 'button', 'aria-label': `Remove ${f.file}`, onclick: () => { files.splice(i, 1); drawFiles(); } }, 'remove'))));
  };
  const addFiles = async list => {
    for (const file of list) {
      if (!/\.(txt|dek|dck|csv)$/i.test(file.name)) { toast(`Skipped ${file.name}: not a .txt/.dek/.dck/.csv file`); continue; }
      const raw = await readFile(file);
      const parsed = normalizeDeckFile(file.name, raw);
      files.push({ file: file.name, name: parsed.name, text: parsed.text, lines: parsed.text.split('\n').filter(l => /^\s*\d/.test(l)).length });
    }
    drawFiles();
  };
  fileInput.addEventListener('change', () => { addFiles([...fileInput.files]); fileInput.value = ''; });

  const drop = h('div.dropzone', { tabindex: '0', role: 'button', 'aria-label': 'Choose deck files or drop them here',
    onclick: () => fileInput.click(),
    onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); } } },
  h('div.dz-blob', { 'aria-hidden': 'true' }, '⇩'),
  h('div', h('b', 'Drop deck files here'), ' or click to choose'),
  h('div.muted.small', '.txt · .dek (MTGO) · .dck (Forge/XMage) · .csv — several at once is fine'));
  for (const type of ['dragenter', 'dragover']) drop.addEventListener(type, e => { e.preventDefault(); drop.classList.add('over'); });
  for (const type of ['dragleave', 'drop']) drop.addEventListener(type, e => { e.preventDefault(); drop.classList.remove('over'); });
  drop.addEventListener('drop', e => addFiles([...(e.dataTransfer?.files || [])]));

  const form = h('form.panel.import-panel', { onsubmit: async event => {
    event.preventDefault();
    const decks = [];
    const fmt = format.value || null;
    if (text.value.trim()) decks.push({ name: name.value.trim() || 'Pasted deck', text: text.value, format: fmt, status, source: 'paste', source_url: null });
    for (const f of files) decks.push({ name: f.name.trim() || f.file, text: f.text, format: fmt, status, source: 'file', source_url: null });
    if (!decks.length) { clear(results).append(errorBox('Paste a list or add at least one file first.')); return; }
    go.disabled = true;
    clear(results).append(spinner(`Importing ${decks.length} deck${decks.length === 1 ? '' : 's'}…`));
    try {
      const response = await api.decks.import(decks);
      renderImportResults(results, response);
      text.value = ''; name.value = ''; files.length = 0; drawFiles();
      load();
    } catch (error) {
      clear(results).append(errorBox(error.message));
    } finally { go.disabled = false; }
  } },
  h('div.panel-head', h('h2', 'Import decks'), h('span.muted.small', 'MTGO, Arena, Moxfield and Archidekt exports. Set codes and *F* markers pick printings.')),
  h('div.import-grid',
    h('div.import-paste',
      h('h3', 'Paste a list'),
      h('div.form-row', name),
      text,
      h('p.hint', h('b', 'From Moxfield: '), 'More → Export → Copy for Moxfield, then paste here (keeps printings and foils).')),
    h('div.import-files',
      h('h3', 'Or import files'),
      drop, fileInput, fileList)),
  h('div.form-row.import-opts',
    h('label.inline-label', 'Format ', format),
    h('span.inline-label', 'Status ', statusSeg(() => status, v => { status = v; })),
    go),
  results,
  browserEdition ? h('p.hint.arch-note', h('b', 'From Archidekt: '), 'use the deck’s Export option to copy its list as text, and paste it above. (Archidekt doesn’t let other websites read its decks, so importing by link needs the Cardclops app.)')
    : archidektSection(() => status, results),
  decksFileSection(results));
  return form;
}

/** A file from "Export all decks" on another copy of Cardclops: every deck, with settings and pins. */
function decksFileSection(results) {
  let onConflict = 'replace';
  const choices = [['replace', 'Replace them'], ['keep', 'Keep both'], ['skip', 'Skip them']];
  const conflict = h('div.seg', { role: 'group', 'aria-label': 'Decks already here with the same name' });
  const drawConflict = () => clear(conflict).append(...choices.map(([v, label]) => h('button.seg-btn', {
    type: 'button', class: v === onConflict ? 'on' : null, 'aria-pressed': String(v === onConflict),
    onclick: () => { onConflict = v; drawConflict(); } }, label)));
  drawConflict();
  const input = h('input', { type: 'file', accept: '.json,application/json', hidden: true, onchange: async () => {
    const file = input.files[0];
    input.value = '';
    if (!file) return;
    clear(results).append(spinner(`Reading ${file.name}…`));
    try {
      const data = JSON.parse(await file.text());
      const r = await api.decks.importFile(data, onConflict);
      const parts = [['Added', r.imported], ['Replaced', r.replaced], ['Skipped', r.skipped]].filter(([, list]) => list.length);
      clear(results).append(h('div.panel.import-results',
        h('h3', `From ${file.name}`),
        ...parts.map(([label, list]) => h('p', h('b', `${label} ${list.length}: `), list.join(', '))),
        r.warnings?.length ? h('details.warn-list', h('summary', `⚠ ${r.warnings.length} line${r.warnings.length === 1 ? '' : 's'} not understood`),
          h('ul', r.warnings.map(w => h('li', `${w.deck}: `, h('code', w.line), ' — ', w.message)))) : null));
      invalidateDecks();
      load();
    } catch (error) {
      clear(results).append(errorBox(error instanceof SyntaxError ? 'That file isn’t a Cardclops decks file.' : error.message));
    }
  } });
  return h('section.decks-file',
    h('h3', 'From a Cardclops decks file'),
    h('p.small.muted', 'Made with ⇩ Export all decks on another copy of Cardclops (your phone, cardclops.com). Lists, printings, commanders, format, status, priority, notes and pinned copies all come across.'),
    h('div.form-row', h('span.inline-label', 'Decks already here with the same name: ', conflict),
      h('button.btn', { type: 'button', onclick: () => input.click() }, '⇧ Choose a decks file…'), input));
}

/** Archidekt import: paste deck links, or list a user's public decks and pick. One deck a second server-side. */
function archidektSection(getStatus, results) {
  const urls = h('textarea.url-text', { spellcheck: 'false', 'aria-label': 'Archidekt deck links, one per line',
    placeholder: 'https://archidekt.com/decks/123456/my_deck\nhttps://archidekt.com/decks/654321/…' });
  const user = h('input.text-input', { type: 'text', placeholder: 'Archidekt username', 'aria-label': 'Archidekt username', autocomplete: 'off' });
  const listBox = h('div.arch-list');
  const run = async (list, button) => {
    if (!list.length) return;
    button.disabled = true;
    const seconds = list.length;
    const started = Date.now();
    const elapsed = h('b', '0 s');
    clear(results).append(h('div.asking-box', spinner(), h('div', `Fetching ${list.length} deck${list.length === 1 ? '' : 's'} from Archidekt, one a second — about ${seconds} s. `, elapsed)));
    const timer = setInterval(() => { elapsed.textContent = Math.round((Date.now() - started) / 1000) + ' s'; }, 500);
    try {
      const response = await api.decks.importArchidekt(list, getStatus());
      renderImportResults(results, response);
      load();
    } catch (error) { clear(results).append(errorBox(error.message)); }
    finally { clearInterval(timer); button.disabled = false; }
  };
  const importUrls = h('button.btn', { type: 'button', onclick: e => run(urls.value.split(/[\s,]+/).filter(u => /archidekt\.com\/decks\/\d+/.test(u) || /^\d+$/.test(u)), e.target) }, 'Import links');
  const lookUp = async () => {
    const name = user.value.trim();
    if (!name) return user.focus();
    find.disabled = true;
    clear(listBox).append(spinner(`Looking up ${name}’s decks…`));
    try {
      const { decks = [] } = await api.decks.archidektUser(name);
      drawChecklist(decks, name);
    } catch (error) { clear(listBox).append(errorBox(error.message)); }
    finally { find.disabled = false; }
  };
  const find = h('button.btn', { type: 'button', onclick: lookUp }, 'Find decks');
  // Not a nested <form>: Enter in the username box looks decks up instead of submitting the import form.
  user.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); lookUp(); } });
  const userForm = h('div.form-row', user, find);
  const drawChecklist = (decks, name) => {
    clear(listBox);
    if (!decks.length) { listBox.append(h('p.muted', `No public decks for “${name}”.`)); return; }
    const chosen = new Set(decks.map(d => d.url));
    const count = h('span');
    const go = h('button.btn.go', { type: 'button', onclick: e => run(decks.filter(d => chosen.has(d.url)).map(d => d.url), e.target) });
    const sync = () => {
      count.textContent = `${chosen.size} of ${decks.length} selected`;
      go.textContent = `Import ${chosen.size} deck${chosen.size === 1 ? '' : 's'} (~${chosen.size} s)`;
      go.disabled = !chosen.size;
      all.checked = chosen.size === decks.length;
      all.indeterminate = chosen.size > 0 && chosen.size < decks.length;
    };
    const all = h('input', { type: 'checkbox', checked: true, 'aria-label': 'Select all decks', onchange: () => {
      if (all.checked) decks.forEach(d => chosen.add(d.url)); else chosen.clear();
      for (const box of listBox.querySelectorAll('.arch-deck input')) box.checked = all.checked;
      sync();
    } });
    listBox.append(
      h('label.arch-all', all, ' Select all ', count),
      h('ul.arch-decks', decks.map(d => h('li', h('label.arch-deck',
        h('input', { type: 'checkbox', checked: true, onchange: e => { if (e.target.checked) chosen.add(d.url); else chosen.delete(d.url); sync(); } }),
        d.featured ? h('img', { src: d.featured, alt: '', loading: 'lazy' }) : h('span.arch-noart'),
        h('span.arch-name', h('b', d.name), h('span.muted.small', ` ${d.format ? deckFormatLabel(d.format) + ' · ' : ''}${int(d.size)} cards${d.updated_at ? ' · updated ' + String(d.updated_at).slice(0, 10) : ''}`)))))),
      h('div.form-row', go));
    sync();
  };
  return h('details.arch-box',
    h('summary', 'Import from Archidekt'),
    h('div.import-grid',
      h('div.import-paste', h('h3', 'Deck links'), urls, h('div.form-row', importUrls)),
      h('div.import-files', h('h3', 'All decks of a user'), userForm,
        h('p.hint', 'Only public and unlisted decks can be read; private decks don’t show up.'), listBox)),
    h('p.hint', 'Exact printings, foils, commander, companion and sideboard come across. The status above applies to these too.'));
}

function renderImportResults(box, response) {
  clear(box);
  const imported = response.imported || [];
  const warnings = response.warnings || [];
  box.append(h('div.import-done',
    h('h3', `Imported ${imported.length} deck${imported.length === 1 ? '' : 's'}`),
    h('ul.import-list', imported.map(d => {
      const w = warnings.filter(x => x.deck === d.name);
      return h('li',
        h('a.deck-link', { href: `#/decks/${d.deck_id}` }, d.name),
        h('span.muted', ` · ${deckFormatLabel(d.format)} · ${int(d.card_count)} cards · `),
        d.missing ? h('span.miss', `${int(d.missing)} missing`) : h('span.gain.up', '✓ all owned'),
        w.length ? h('details.warn-list', h('summary', `⚠ ${w.length} line${w.length === 1 ? '' : 's'} not understood`),
          h('ul', w.map(x => h('li', h('code', x.line), ' — ', x.message)))) : null);
    })),
    warnings.filter(x => !imported.some(d => d.name === x.deck)).map(x => h('div.warn', '⚠ ', x.deck ? h('b', x.deck + ': ') : null, x.line ? h('code', x.line) : null, ' ', x.message))));
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

/**
 * Turn a deck file into list text the server understands. Plain text and Moxfield/Arena exports pass through;
 * MTGO .dek XML, Forge/XMage .dck and CSV exports are rewritten as "N Name (SET) number" lines with section headers.
 */
export function normalizeDeckFile(fileName, raw) {
  let name = fileName.replace(/\.[^.]+$/, '');
  const text = (raw.charCodeAt(0) === 0xFEFF ? raw.slice(1) : raw).replace(/\r\n?/g, '\n');   // drop a byte-order mark
  if (/\.dek$/i.test(fileName) || /^\s*<\?xml|<Deck\b/i.test(text)) {
    const main = [], side = [];
    for (const m of text.matchAll(/<Cards\b([^>]*)\/?>/gi)) {
      const attr = key => (m[1].match(new RegExp(`${key}="([^"]*)"`, 'i')) || [])[1];
      const qty = attr('Quantity'), card = attr('Name');
      if (!qty || !card) continue;
      (attr('Sideboard') === 'true' ? side : main).push(`${qty} ${decodeXml(card)}`);
    }
    return { name, text: ['Deck', ...main, '', 'Sideboard', ...side].join('\n') };
  }
  if (/\.dck$/i.test(fileName)) {
    const out = [];
    for (let line of text.split('\n')) {
      line = line.trim();
      if (!line || line.startsWith('#')) continue;
      const header = line.match(/^\[(\w+)\]$/);
      if (header) {
        const key = header[1].toLowerCase();
        if (key !== 'metadata') out.push('', { main: 'Deck', sideboard: 'Sideboard', commander: 'Commander', planes: 'Maybeboard' }[key] || header[1]);
        continue;
      }
      const meta = line.match(/^Name[=:](.+)$/i);                                    // Forge "Name=", XMage "NAME:"
      if (meta) { name = meta[1].trim(); continue; }
      if (/^\w+=/.test(line) || /^LAYOUT\b/i.test(line)) continue;
      let side = false;
      if (/^SB:\s*/i.test(line)) { side = true; line = line.replace(/^SB:\s*/i, ''); }
      const xmage = line.match(/^(\d+)\s+\[([A-Z0-9]+):([^\]]+)\]\s+(.+)$/i);        // 4 [M10:146] Lightning Bolt
      const forge = line.match(/^(\d+)\s+([^|]+)\|([A-Z0-9]+)(?:\|(\S+))?/i);          // 4 Lightning Bolt|M10|1
      let entry = line;
      if (xmage) entry = `${xmage[1]} ${xmage[4]} (${xmage[2]}) ${xmage[3]}`;
      else if (forge) entry = `${forge[1]} ${forge[2].trim()} (${forge[3]})`;
      if (!/^\d/.test(entry)) continue;
      if (side) { if (!out.includes('Sideboard')) out.push('', 'Sideboard'); }
      out.push(entry);
    }
    return { name, text: out.join('\n').trim() };
  }
  if (/\.csv$/i.test(fileName)) return { name, text: csvToList(text) };
  return { name, text };
}

function decodeXml(s) {
  return s.replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
}

function parseCsvLine(line) {
  const cells = [];
  let cur = '', quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') quoted = false;
      else cur += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { cells.push(cur); cur = ''; }
    else cur += c;
  }
  cells.push(cur);
  return cells.map(s => s.trim());
}

function csvToList(text) {
  const rows = text.split('\n').filter(l => l.trim()).map(parseCsvLine);
  if (!rows.length) return '';
  const head = rows[0].map(c => c.toLowerCase());
  const find = (...keys) => head.findIndex(c => keys.includes(c));
  const nameCol = find('name', 'card name', 'card', 'cardname');
  if (nameCol < 0) return text;                       // not a recognizable export; let the server try
  const qtyCol = find('quantity', 'qty', 'count', 'amount');
  const setCol = find('set code', 'set', 'edition', 'set_code');
  const numCol = find('collector number', 'collector_number', 'number', 'card number');
  const finCol = find('foil', 'finish', 'printing');
  const secCol = find('section', 'board', 'category', 'zone');
  const groups = new Map();
  for (const row of rows.slice(1)) {
    const card = row[nameCol];
    if (!card) continue;
    const qty = parseInt(row[qtyCol] ?? '1', 10) || 1;
    const set = setCol >= 0 && row[setCol] && row[setCol].length <= 6 ? ` (${row[setCol].toUpperCase()})` : '';
    const num = set && numCol >= 0 && row[numCol] ? ` ${row[numCol]}` : '';
    const fin = finCol >= 0 ? (/etched/i.test(row[finCol]) ? ' *E*' : /foil|true|yes/i.test(row[finCol]) && !/non/i.test(row[finCol]) ? ' *F*' : '') : '';
    const rawSection = secCol >= 0 ? (row[secCol] || '').toLowerCase() : '';
    const section = /command/.test(rawSection) ? 'Commander' : /side/.test(rawSection) ? 'Sideboard' : /maybe/.test(rawSection) ? 'Maybeboard' : /compan/.test(rawSection) ? 'Companion' : 'Deck';
    if (!groups.has(section)) groups.set(section, []);
    groups.get(section).push(`${qty} ${card}${set}${num}${fin}`);
  }
  const order = ['Commander', 'Companion', 'Deck', 'Sideboard', 'Maybeboard'];
  return order.filter(s => groups.has(s)).map(s => [s, ...groups.get(s)].join('\n')).join('\n\n');
}

/** Called by the deck page after a delete / rename so the grid refreshes next visit. */
/** After any deck change: this list reloads, and other views that show spare copies hear about it. */
// A deck changed somewhere else (a card's detail view): the list reloads next time it's shown.
addEventListener('cardclops:decks-changed', () => { lastData = null; });

export function invalidateDecks() {
  lastData = null;
  window.dispatchEvent(new CustomEvent('cardclops:decks-changed'));
}
