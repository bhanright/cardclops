// Binders (#/binders, under Tools): the binders, boxes and lists you file copies into, with their
// counts and value; opening one shows its cards in the gallery (binder:"Name"). Filing happens from
// a card's detail view or from a gallery search ("Put these in a binder"). gallery/binders.py.
import { h, clear, int, money, spinner, errorBox, toast } from './util.js';
import { api } from './api.js';
import { dialog } from './setup.js';

const KIND_LABEL = { binder: 'Binder', box: 'Box', list: 'List' };
const KIND_ICON = { binder: '📒', box: '📦', list: '📋' };
export const binderQuery = name => `binder:"${name.replace(/"/g, '')}"`;

/** Tell other views (the gallery's binder: searches, card details) that binders changed. */
export function binderChanged() { window.dispatchEvent(new CustomEvent('cardclops:binders-changed')); }

export async function showBinders() {
  const host = document.getElementById('view-binders');
  clear(host).append(spinner('Opening the binders…'));
  let data;
  try { data = await api.binders.list(); } catch (error) { clear(host).append(errorBox(error.message)); return; }
  const name = h('input.text-input', { type: 'text', placeholder: 'New binder name, e.g. Trade binder', 'aria-label': 'Binder name', maxlength: 80 });
  const kind = h('select.select', { 'aria-label': 'Kind' }, Object.entries(KIND_LABEL).map(([v, l]) => h('option', { value: v }, l)));
  const create = async () => {
    if (!name.value.trim()) return name.focus();
    try { await api.binders.create({ name: name.value.trim(), kind: kind.value }); binderChanged(); toast(`Made ${name.value.trim()}`); showBinders(); }
    catch (error) { toast(error.message); }
  };
  name.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); create(); } });
  const rows = data.binders.map(b => binderRow(b));
  clear(host).append(h('section.panel.binders-page',
    h('div.panel-head', h('h2', 'Binders'), h('span.muted.small', 'Where your copies live. Filing survives re-importing your collection.')),
    h('div.form-row.binder-new', name, kind, h('button.btn.go', { type: 'button', onclick: create }, '＋ Make it')),
    h('div.binder-rows',
      ...rows,
      h('a.binder-row.unsorted', { href: '#/gallery?q=' + encodeURIComponent('binder:none') },
        h('span.br-icon', { 'aria-hidden': 'true' }, '🗃️'),
        h('span.br-name', h('b', 'Unsorted'), h('span.muted.small', ' copies in no binder')),
        h('span.br-num', `${int(data.unsorted.copies)} copies`), h('span.br-num', money(data.unsorted.value_usd, { whole: true })))),
    h('p.small.muted', 'To file cards: open a card and use “Put in a binder”, or search the gallery (say ',
      h('code', 't:dragon'), ') and use “Put these in a binder”. Search any binder with ', h('code', 'binder:"Name"'),
      ', and unsorted copies with ', h('code', 'binder:none'), '.')));
}

function binderRow(b) {
  const open = '#/gallery?q=' + encodeURIComponent(binderQuery(b.name));
  const rename = async () => {
    const input = h('input.text-input', { type: 'text', value: b.name, 'aria-label': 'Binder name', maxlength: 80 });
    const kind = h('select.select', { 'aria-label': 'Kind' }, Object.entries(KIND_LABEL).map(([v, l]) => h('option', { value: v, selected: v === b.kind }, l)));
    const save = h('button.btn.go', { type: 'button', onclick: async () => {
      try { await api.binders.update(b.binder_id, { name: input.value.trim(), kind: kind.value }); close(); binderChanged(); showBinders(); }
      catch (error) { toast(error.message); }
    } }, 'Save');
    const close = dialog(`Edit ${b.name}`, h('div.add-to-deck', h('div.form-row', input, kind), h('div.form-row', save)));
    input.focus(); input.select();
  };
  const remove = () => {
    const yes = h('button.btn.danger', { type: 'button', onclick: async () => {
      try { await api.binders.remove(b.binder_id); close(); binderChanged(); toast(`Deleted ${b.name}; its cards are unsorted now`); showBinders(); }
      catch (error) { toast(error.message); }
    } }, `Delete ${b.name}`);
    const close = dialog(`Delete ${b.name}?`, h('div', h('p', `Its ${int(b.copies)} copies stay in your collection and become unsorted.`), h('div.form-row', yes)));
  };
  return h('div.binder-row',
    h('span.br-icon', { 'aria-hidden': 'true', title: KIND_LABEL[b.kind] }, KIND_ICON[b.kind] || '📒'),
    h('a.br-name', { href: open, title: 'Show its cards in the gallery' }, h('b', b.name), h('span.muted.small', ` ${KIND_LABEL[b.kind] || ''}`)),
    h('span.br-num', `${int(b.copies)} copies`, b.short ? h('span.br-short', { title: 'The collection now has fewer copies than this binder holds' }, ` · ${int(b.short)} missing`) : null),
    h('span.br-num', money(b.value_usd, { whole: true })),
    h('span.br-actions',
      h('a.btn.small', { href: open }, 'Open'),
      h('a.btn.small.ghost', { href: api.binders.exportUrl(b.binder_id), download: '', title: 'A CSV of this binder (ManaBox and most apps import it)' }, '⇩ CSV'),
      h('button.btn.small.ghost', { type: 'button', onclick: rename }, 'Edit'),
      h('button.btn.small.danger', { type: 'button', onclick: remove }, '✗')));
}

/**
 * "Put in a binder" / "Take out of a binder": for one card's copies (items given) or for everything a
 * gallery search finds (q given). Resolves after a change so the caller can refresh.
 */
export async function fileDialog({ title, items = null, q = null, choices = null, onDone } = {}) {
  const body = h('div.add-to-deck', spinner('Finding your binders…'));
  const close = dialog(title, body);
  let data;
  try { data = await api.binders.list(); } catch (error) { clear(body).append(errorBox(error.message)); return; }
  const NEW = '__new__';
  const binder = h('select.select', { 'aria-label': 'Binder' },
    data.binders.map(b => h('option', { value: b.binder_id }, `${KIND_ICON[b.kind] || ''} ${b.name}`)),
    h('option', { value: NEW }, '＋ New binder…'));
  const newName = h('input.text-input', { type: 'text', placeholder: 'New binder name', 'aria-label': 'New binder name', hidden: data.binders.length > 0 });
  if (!data.binders.length) binder.value = NEW;
  binder.addEventListener('change', () => { newName.hidden = binder.value !== NEW; if (!newName.hidden) newName.focus(); });
  const mode = h('select.select', { 'aria-label': 'Put in or take out' },
    h('option', { value: 'put' }, 'Put in'), h('option', { value: 'take' }, 'Take out of'));
  // One card: which printing and finish, and how many.
  const pool = choices ? h('select.select', { 'aria-label': 'Printing' }, choices.map((c, i) => h('option', { value: i }, c.label))) : null;
  const quantity = choices ? h('input.num-input', { type: 'number', min: 1, step: 1, value: Math.max(1, choices[0]?.free || 1), 'aria-label': 'Copies' }) : null;
  pool?.addEventListener('change', () => { quantity.value = Math.max(1, choices[+pool.value].free || 1); });
  const go = h('button.btn.go', { type: 'button', onclick: async () => {
    go.disabled = true;
    try {
      let binderId = binder.value;
      if (binderId === NEW) {
        if (!newName.value.trim()) { toast('Name the new binder'); go.disabled = false; return; }
        binderId = (await api.binders.create({ name: newName.value.trim(), kind: 'binder' })).binder_id;
      }
      const request = choices ? { items: [{ scryfall_id: choices[+pool.value].scryfall_id, finish: choices[+pool.value].finish, quantity: Math.max(1, +quantity.value || 1) }] }
        : items ? { items } : { q };
      const r = mode.value === 'put' ? await api.binders.put(binderId, request) : await api.binders.take(binderId, request);
      binderChanged();
      close();
      toast(r.moved ? `${mode.value === 'put' ? 'Put' : 'Took'} ${int(r.moved)} cop${r.moved === 1 ? 'y' : 'ies'} ${mode.value === 'put' ? 'in' : 'out of'} ${r.name}`
        : (mode.value === 'put' ? 'Nothing to file: those copies are already in binders' : 'None of those copies are in that binder'));
      onDone?.();
    } catch (error) { toast(error.message); go.disabled = false; }
  } }, 'Go');
  clear(body).append(
    h('div.form-row', mode, binder), newName,
    pool ? h('div.form-row', h('label.inline-label', 'Printing ', pool), h('label.inline-label', 'Copies ', quantity)) : null,
    q != null ? h('p.small.muted', 'Putting in files every copy these results have that is in no binder yet. Taking out moves them back to unsorted.') : null,
    h('div.form-row', go));
}
