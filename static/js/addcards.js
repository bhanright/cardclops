// Adding cards by hand (docs/API.md, "Adding cards by hand"): the shared "Add to my collection" dialog,
// the "Cards added by hand" list, and the small hand-added marker used on tiles and rows.
import { h, clear, int, money, spinner, errorBox, toast, finishLabel, debounce } from './util.js';
import { api, resize } from './api.js';
import { lazyImg } from './cards.js';
import { dialog } from './setup.js';

const FINISH_OF = { nonfoil: 'normal', foil: 'foil', etched: 'etched' };
const CONDITIONS = [['near_mint', 'Near mint'], ['excellent', 'Excellent'], ['good', 'Good'], ['light_played', 'Lightly played'], ['played', 'Played'], ['poor', 'Poor']];
const LANGUAGES = [['en', 'English'], ['ja', 'Japanese'], ['de', 'German'], ['fr', 'French'], ['it', 'Italian'], ['es', 'Spanish'], ['pt', 'Portuguese'],
  ['ru', 'Russian'], ['ko', 'Korean'], ['zhs', 'Chinese (simplified)'], ['zht', 'Chinese (traditional)'], ['ph', 'Phyrexian']];
const KEEP_NOTE = 'These stay when you re-import from ManaBox. When your next import includes the same printing, it replaces the hand-added copies.';

/** Small marker for holdings added in Cardclops rather than imported. */
export function handMark(source, { text = false } = {}) {
  if (source !== 'manual') return null;
  return h('span.hand-mark', { title: 'Added by hand in Cardclops', 'aria-label': 'added by hand' }, text ? '✎ by hand' : '✎');
}

/** Tell the app the collection changed, once the server has reloaded it (copies reaches `copies`). */
let changedWhileOpen = false;     // a dialog is open: views refresh again when it closes
async function announceReload(copies) {
  changedWhileOpen = true;
  for (let i = 0; i < 15; i++) {
    await new Promise(r => setTimeout(r, 1000));
    try { const s = await api.summary(); if (copies == null || s.copies === copies) break; } catch { /* keep waiting */ }
  }
  window.dispatchEvent(new CustomEvent('cardclops:collection-changed'));
}

// ---------- the add form ----------
/**
 * The add-card form. Prefill with {oracleId, name, scryfallId, quantity}. onAdded(result) after a successful add.
 * Returns a node; openAddDialog wraps it in a dialog.
 */
export function addCardForm({ oracleId = null, name = '', scryfallId = null, quantity = 1, onAdded } = {}) {
  const box = h('div.add-form');
  const search = h('input.text-input', { type: 'search', value: name, placeholder: 'Card name, e.g. Sol Ring', 'aria-label': 'Card name' });
  const names = h('div.lookup-names');
  const picker = h('div.add-picker', { 'aria-live': 'polite' });
  let card = null;
  let chosen = null;

  const find = async params => {
    clear(picker).append(spinner('Looking up printings…'));
    clear(names);
    let cards;
    try { cards = (await api.lookup(params)).cards || []; } catch (error) { clear(picker).append(errorBox(error.message)); return; }
    if (!cards.length) { clear(picker).append(h('p.muted', 'No card by that name.')); return; }
    if (cards.length > 1) {
      names.append(...cards.slice(0, 12).map((c, i) => h('button.mini-chip', { type: 'button', 'aria-pressed': String(i === 0), onclick: async e => {
        for (const b of names.children) b.setAttribute('aria-pressed', String(b === e.currentTarget));
        if (!c.printings?.length) {
          try { c.printings = (await api.lookup({ oracle_id: c.oracle_id })).cards?.[0]?.printings || []; } catch (error) { clear(picker).append(errorBox(error.message)); return; }
        }
        showCard(c, null);
      } }, c.name)));
    }
    showCard(cards[0], scryfallId);
  };
  const runSearch = () => { const q = search.value.trim(); if (q) find({ q }); };
  search.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); runSearch(); } });
  search.addEventListener('input', debounce(() => { if (search.value.trim().length >= 3) runSearch(); }, 500));

  function showCard(c, preselect) {
    card = c;
    const printings = (c.printings || []).slice().sort((a, b) => (b.released_at || '').localeCompare(a.released_at || ''));
    if (!printings.length) { clear(picker).append(h('p.muted', 'No printings found.')); return; }
    chosen = printings.find(p => p.scryfall_id === preselect) || printings[0];
    const grid = h('div.print-grid', { role: 'radiogroup', 'aria-label': 'Printing' });
    const form = h('div');
    const drawGrid = () => {
      clear(grid).append(...printings.map(p => h('button.print-pick', { type: 'button', role: 'radio', 'aria-checked': String(p === chosen),
        title: `${p.set_name} (${(p.set_code || '').toUpperCase()}) #${p.collector_number}${p.released_at ? ' · ' + p.released_at.slice(0, 4) : ''}`,
        onclick: () => { chosen = p; drawGrid(); drawForm(); } },
      lazyImg(resize(p.image, 'small'), '', 'card-img'),
      h('span.pp-cap', h('b', (p.set_code || '').toUpperCase()), ` #${p.collector_number}`))));
    };
    const drawForm = () => {
      const finishes = (chosen.finishes || ['nonfoil']).map(f => FINISH_OF[f] || 'normal');
      const finish = h('select.select', { 'aria-label': 'Finish' }, finishes.map(f => h('option', { value: f }, finishLabel(f))));
      const qty = h('input.num-input', { type: 'number', min: 1, step: 1, value: quantity, 'aria-label': 'Quantity' });
      const cond = h('select.select', { 'aria-label': 'Condition' }, CONDITIONS.map(([v, l]) => h('option', { value: v, selected: v === 'near_mint' }, l)));
      const lang = h('select.select', { 'aria-label': 'Language' }, LANGUAGES.map(([v, l]) => h('option', { value: v, selected: v === 'en' }, l)));
      const paid = h('input.num-input', { type: 'number', min: 0, step: 0.01, placeholder: 'optional', 'aria-label': 'Price paid per copy in dollars' });
      const priceNow = h('span.muted.small');
      const showPrice = () => {
        const key = { normal: 'usd', foil: 'usd_foil', etched: 'usd_etched' }[finish.value];
        const v = chosen.prices?.[key];
        priceNow.textContent = v != null ? `now ${money(+v)} each` : 'no price yet';
      };
      finish.addEventListener('change', showPrice);
      showPrice();
      const status = h('div.add-status', { 'aria-live': 'polite' });
      const submit = h('button.btn.go', { type: 'button', onclick: async () => {
        const n = parseInt(qty.value, 10);
        if (!(n > 0)) { toast('Enter how many copies'); qty.focus(); return; }
        submit.disabled = true;
        clear(status).append(spinner('Adding…'));
        try {
          const r = await api.collection.add({ scryfall_id: chosen.scryfall_id, finish: finish.value, quantity: n, condition: cond.value, language: lang.value,
            purchase_price: paid.value === '' ? null : +paid.value });
          const what = `${n} × ${c.name} (${(chosen.set_code || '').toUpperCase()} #${chosen.collector_number}, ${finishLabel(finish.value).toLowerCase()})`;
          const note = h('div.setup-ok', `✓ Added ${what} — the collection is reloading…`);
          clear(status).append(note);
          submit.disabled = false;
          announceReload(r.copies).then(() => { note.textContent = `✓ Added ${what}. You now have ${int(r.copies)} copies in all.`; });
          onAdded?.(r);
        } catch (error) { clear(status).append(errorBox(error.message)); submit.disabled = false; }
      } }, '＋ Add to my collection');
      clear(form).append(
        h('div.add-fields',
          h('label.inline-label', 'Finish ', finish), priceNow,
          h('label.inline-label', 'Copies ', qty),
          h('label.inline-label', 'Condition ', cond),
          h('label.inline-label', 'Language ', lang),
          h('label.inline-label', 'Paid $ ', paid)),
        h('div.form-row', submit), status);
    };
    drawGrid();
    drawForm();
    clear(picker).append(h('p', h('b', c.name), h('span.muted.small', ` · ${printings.length} printing${printings.length === 1 ? '' : 's'} — pick yours`)), grid, form);
    grid.querySelector('[aria-checked="true"]')?.scrollIntoView({ block: 'nearest' });
  }

  box.append(h('div.form-row', search, h('button.btn', { type: 'button', onclick: runSearch }, 'Find')), names, picker);
  if (oracleId) find({ oracle_id: oracleId });
  else if (name) find({ q: name });
  setTimeout(() => (oracleId ? null : search.focus()), 0);
  return box;
}

function refreshOnClose() {
  if (changedWhileOpen) window.dispatchEvent(new CustomEvent('cardclops:collection-changed'));
  changedWhileOpen = false;
}

export function openAddDialog(prefill = {}) {
  changedWhileOpen = false;
  return dialog(prefill.name ? `Add ${prefill.name} to my collection` : 'Add to my collection', addCardForm(prefill), { onClose: refreshOnClose });
}

// ---------- "Cards added by hand" (Tools menu) ----------
export function openHandAdded() {
  const list = h('div.manual-list');
  const reloadList = () => loadManual(list);
  changedWhileOpen = false;
  dialog('Add cards by hand', h('div.hand-dialog',
    addCardForm({ onAdded: () => setTimeout(reloadList, 500) }),
    h('section.manual-section', h('h3', 'Cards added by hand'), h('p.small.muted', KEEP_NOTE), list)), { onClose: refreshOnClose });
  reloadList();
}

async function loadManual(list) {
  clear(list).append(spinner('Loading…'));
  let rows;
  try { rows = (await api.collection.manual()).rows || []; } catch (error) { clear(list).append(errorBox(error.message)); return; }
  clear(list);
  if (!rows.length) { list.append(h('div.chart-empty', 'Nothing added by hand yet.')); return; }
  list.append(h('ul.manual-rows', rows.map(r => manualRow(r, () => loadManual(list)))));
}

function manualRow(row, reload) {
  const li = h('li.manual-row');
  const draw = () => {
    const qty = h('input.num-input', { type: 'number', min: 1, step: 1, value: row.quantity, 'aria-label': `Copies of ${row.name}` });
    const finish = h('select.select', { 'aria-label': 'Finish' }, ['normal', 'foil', 'etched'].map(f => h('option', { value: f, selected: f === row.finish }, finishLabel(f))));
    const cond = h('select.select', { 'aria-label': 'Condition' }, CONDITIONS.map(([v, l]) => h('option', { value: v, selected: v === row.condition }, l)));
    const save = h('button.btn.small.go', { type: 'button', hidden: true, onclick: async () => {
      save.disabled = true;
      try {
        const updated = await api.collection.updateManual(row.row_id, { quantity: +qty.value, finish: finish.value, condition: cond.value });
        Object.assign(row, updated); toast('Saved — the collection is reloading…'); draw(); announceReload(null);
      } catch (error) { toast('Could not save: ' + error.message); save.disabled = false; }
    } }, 'Save');
    for (const el of [qty, finish, cond]) el.addEventListener('input', () => { save.hidden = false; });
    for (const el of [finish, cond]) el.addEventListener('change', () => { save.hidden = false; });
    const del = h('button.btn.small.danger', { type: 'button', 'aria-label': `Remove ${row.name}`, onclick: () => {
      const yes = h('button.btn.small.danger', { type: 'button', onclick: async () => {
        try { await api.collection.removeManual(row.row_id); toast(`Removed ${row.name}`); li.remove(); announceReload(null); }
        catch (error) { toast('Could not remove: ' + error.message); }
      } }, 'Yes, remove');
      clear(actions).append(h('span.confirm-text.small', 'Remove?'), yes, h('button.btn.small.ghost', { type: 'button', onclick: draw }, 'Keep'));
      yes.focus();
    } }, '✗');
    const actions = h('div.mr-actions', save, del);
    clear(li).append(
      h('span.mr-img', lazyImg(resize(row.image, 'small'), '', 'rr-thumb')),
      h('div.mr-main',
        h('div', h('b', row.name), h('span.muted.small', ` ${(row.set_code || '').toUpperCase()} #${row.collector_number}${row.language && row.language !== 'en' ? ' · ' + row.language.toUpperCase() : ''}`)),
        h('div.mr-fields', h('label.inline-label', '× ', qty), finish, cond,
          h('span.muted.small', `${row.price_usd != null ? money(row.price_usd) + ' each' : ''}${row.added_at ? ' · added ' + String(row.added_at).slice(0, 10) : ''}`))),
      actions);
  };
  draw();
  return li;
}

/** Import results can list hand-added copies that the imported file now covers. */
export function reconciledList(items) {
  if (!items?.length) return null;
  return h('details.unmatched', { open: items.length <= 8 },
    h('summary', `${items.length} hand-added card${items.length === 1 ? '' : 's'} replaced by your import`),
    h('ul', items.map(x => h('li', h('b', x.name), ` (${finishLabel(x.finish || 'normal').toLowerCase()}): ${int(x.removed)} hand-added ${x.removed === 1 ? 'copy now comes' : 'copies now come'} from the import`))));
}

