// Deck check: paste a list, see what you own, what's missing and what it costs to finish.
import { h, $, clear, int, money, spinner, errorBox, copyText, toast, store } from './util.js';
import { api } from './api.js';
import { thumb } from './cards.js';
import { openCard } from './detail.js';

const DRAFT_KEY = 'gallery.deckDraft';
const STATUS = {
  owned: ['✓', 'Owned'], partial: ['◐', 'Partial'], missing: ['✗', 'Missing'], unknown: ['?', 'Not recognized'],
};
let built = false;
let lastResult = null;

export function showDeckCheck() {
  if (built) return;
  built = true;
  const root = $('#view-deck');
  const area = h('textarea#deckText.deck-text', {
    spellcheck: 'false', 'aria-label': 'Decklist',
    placeholder: '4 Lightning Bolt\n4 Ragavan, Nimble Pilferer (MH2) 138\n1 Sol Ring *F*\n\nSideboard\n2 Pyroblast',
  });
  area.value = store.get(DRAFT_KEY, '');
  area.addEventListener('input', () => store.set(DRAFT_KEY, area.value));
  const out = h('div.deck-out');
  const check = h('button.btn.go', { type: 'submit' }, 'Check deck');
  const form = h('form.panel.deck-form', { onsubmit: async event => {
    event.preventDefault();
    if (!area.value.trim()) { area.focus(); return; }
    check.disabled = true;
    clear(out).append(spinner('Checking your binders…'));
    try {
      lastResult = await api.deckcheck(area.value);
      render(out, lastResult);
    } catch (error) {
      clear(out).append(errorBox(error.message));
    } finally { check.disabled = false; }
  } },
  h('div.panel-head', h('h2', 'Deck check'), h('span.muted.small', 'MTGO, Arena or Moxfield export. Set codes and *F* markers are ignored.')),
  area,
  h('div.form-row', check, h('button.btn.ghost', { type: 'button', onclick: () => { area.value = ''; store.set(DRAFT_KEY, ''); clear(out); area.focus(); } }, 'Clear')));
  root.append(form, out);
}

function render(out, result) {
  clear(out);
  const lines = result.lines || [];
  const t = result.totals || {};
  if (!lines.length) { out.append(h('div.chart-empty', 'No card lines found. Lines look like “4 Lightning Bolt”.')); return; }
  const missingLines = lines.filter(l => l.missing > 0);
  const pct = t.wanted ? Math.round((t.owned / t.wanted) * 100) : 0;
  out.append(h('div.panel.deck-summary',
    h('div.bignums',
      h('div.bignum.c-cyan', h('div.bn-value', int(t.wanted)), h('div.bn-label', 'Cards wanted')),
      h('div.bignum.c-lime', h('div.bn-value', int(t.owned)), h('div.bn-label', `Owned (${pct}%)`)),
      h('div.bignum.c-pink', h('div.bn-value', int(t.missing)), h('div.bn-label', 'Missing')),
      h('div.bignum.c-yellow', h('div.bn-value', money(t.cost_to_complete_usd)), h('div.bn-label', 'Cost to complete'),
        t.unpriced_missing ? h('div.bn-sub', `+ ${int(t.unpriced_missing)} without a price`) : null)),
    h('div.progress.big', { role: 'progressbar', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-label': 'Deck owned' },
      h('span.progress-fill', { class: pct >= 100 ? 'full' : null, style: { width: pct + '%' } })),
    h('div.form-row',
      h('button.btn', { type: 'button', disabled: !missingLines.length, onclick: async () => {
        const text = missingLines.map(l => `${l.missing} ${l.name}`).join('\n');
        toast((await copyText(text)) ? `Copied ${missingLines.length} missing lines` : 'Copy failed — select and copy manually');
      } }, '⧉ Copy missing list'),
      missingLines.length ? h('span.muted.small', `${missingLines.length} line${missingLines.length === 1 ? '' : 's'} to buy`) : h('span.chg.up', '▲ You own the whole deck!'))));

  const sections = [];
  for (const line of lines) {
    let section = sections.find(s => s.name === line.section);
    if (!section) sections.push(section = { name: line.section, lines: [] });
    section.lines.push(line);
  }
  for (const section of sections) {
    const wanted = section.lines.reduce((s, l) => s + l.wanted, 0);
    const owned = section.lines.reduce((s, l) => s + l.owned, 0);
    const order = { missing: 0, unknown: 1, partial: 2, owned: 3 };
    out.append(h('section.panel.deck-section',
      h('div.panel-head', h('h2', section.name || 'Main'), h('span.muted', `${int(owned)} / ${int(wanted)} owned`)),
      h('div.deck-lines', [...section.lines].sort((a, b) => order[a.status] - order[b.status] || a.name.localeCompare(b.name)).map(line => {
        const [icon, label] = STATUS[line.status] || ['?', line.status];
        const printings = line.printings || [];
        return h('div', { class: `deck-line st-${line.status}` },
          h('span.st-pill', { title: label }, icon, h('span.st-text', ' ' + label)),
          h('span.dl-qty', h('b', int(line.owned)), `/${int(line.wanted)}`),
          h('span.dl-name', h('b', line.name),
            line.missing ? h('span.muted.small', ` · need ${line.missing}${line.cheapest_usd != null ? ` × ${money(line.cheapest_usd)} = ${money(line.cheapest_usd * line.missing)}` : ''}`) : null),
          printings.length ? h('div.dl-prints', printings.map((c, i) => thumb(c, { onOpen: () => openCard(c.scryfall_id, { index: i, count: () => printings.length, get: j => Promise.resolve(printings[j]) }) })))
            : line.status === 'unknown' ? h('span.muted.small.dl-note', 'No card by that name — check the spelling.') : null);
      }))));
  }
}
