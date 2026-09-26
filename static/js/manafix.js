// Mana-base fixer for one deck: land swaps from spare lands that raise the chance of casting spells on curve.
// GET /api/decks/<id>/manafix, POST …/manafix/apply (docs/TOOLS.md).
import { h, clear, int, money, spinner, errorBox, symbol, COLOR_NAMES, toast } from './util.js';
import { api, resize } from './api.js';
import { cardFace } from './cards.js';

const pct = p => (p == null ? '—' : `${Math.round(p * 100)}%`);
const pts = d => `${d >= 0 ? '▲ +' : '▼ −'}${Math.abs(d * 100).toFixed(1)} pts`;

/**
 * Returns {button, panel}. The button opens the panel (placed by the caller near the color panels)
 * and asks the server for swaps; onApplied() reloads the deck after the swaps are saved.
 */
export function manafixControls(deck, { onApplied }) {
  const panel = h('section.panel.manafix', { hidden: true, 'aria-live': 'polite', id: 'manafix' });
  const button = h('button.btn.go', { type: 'button', 'aria-controls': 'manafix', 'aria-expanded': 'false', onclick: () => {
    if (!panel.hidden) { panel.hidden = true; button.setAttribute('aria-expanded', 'false'); return; }
    panel.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    compute(deck, panel, onApplied);
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } }, '⚙ Fix my mana base');
  return { button, panel };
}

async function compute(deck, panel, onApplied) {
  clear(panel).append(h('h2', 'Fix my mana base'), spinner('Trying every swap between this deck’s lands and your spare lands…'));
  let fix;
  try { fix = await api.decks.manafix(deck.deck_id); } catch (error) {
    clear(panel).append(h('h2', 'Fix my mana base'), errorBox(error.message));
    return;
  }
  render(deck, panel, fix, onApplied);
}

function render(deck, panel, fix, onApplied) {
  const before = fix.before || {}, after = fix.after || {};
  const swaps = fix.swaps || [];
  const chosen = new Set(swaps.map((_, i) => i));
  const close = h('button.icon-btn.small', { type: 'button', 'aria-label': 'Close the mana-base fixer', onclick: () => {
    panel.hidden = true;
    document.querySelector('[aria-controls="manafix"]')?.setAttribute('aria-expanded', 'false');
  } }, '×');
  clear(panel).append(h('div.panel-head', h('h2', 'Fix my mana base'), close));

  if (deck.source === 'archidekt') {
    panel.append(h('div.warn-box', '⚠ This deck syncs from Archidekt. The next “Sync from Archidekt” will overwrite these swaps unless you make them on Archidekt too.'));
  }

  const gain = (after.score ?? 0) - (before.score ?? 0);
  panel.append(h('div.mf-score',
    h('div.mf-num', h('span.mf-label', 'Now'), h('b', pct(before.score))),
    h('span.mf-arrow', { 'aria-hidden': 'true' }, '→'),
    h('div.mf-num.after', h('span.mf-label', swaps.length ? 'After all swaps' : 'Best found'), h('b', pct(after.score))),
    h('div.mf-explain', h('b', 'spells castable on curve'), ' — the average chance of having each spell’s colors by the turn matching its mana value.',
      swaps.length ? h('span', { class: 'chg ' + (gain > 0 ? 'up' : 'flat') }, pts(gain)) : null)));

  // per color before/after for 1/2/3 pips
  const colors = 'WUBRGC'.split('').filter(c => before.by_color?.[c] || after.by_color?.[c]);
  if (colors.length) {
    panel.append(h('div.table-scroll', h('table.table.odds-table.mf-table',
      h('thead', h('tr', h('th', 'Color'), h('th.num', 'Sources'), h('th.num', '1 pip, T1'), h('th.num', '2 pips, T2'), h('th.num', '3 pips, T3'))),
      h('tbody', colors.map(c => h('tr',
        h('td', symbol(c), ' ', COLOR_NAMES[c]),
        h('td.num', beforeAfter(before.sources?.[c], after.sources?.[c], int)),
        ['1', '2', '3'].map(k => h('td.num', beforeAfter(before.by_color?.[c]?.[k], after.by_color?.[c]?.[k], pct)))))))));
  }
  if (before.tapped_lands != null) {
    panel.append(h('p.small.muted', `Lands entering tapped: ${int(before.tapped_lands)} → ${int(after.tapped_lands ?? before.tapped_lands)}.`,
      fix.considered ? ` Considered ${int(fix.considered.removable)} replaceable lands in the deck and ${int(fix.considered.spare_lands)} spare lands.` : ''));
  }

  if (!swaps.length) {
    panel.append(h('div.chart-empty', fix.message || 'No swap from your spare lands makes this deck easier to cast.'));
    return;
  }

  const apply = h('button.btn.go', { type: 'button' });
  const syncApply = () => {
    apply.textContent = `Apply ${chosen.size} selected swap${chosen.size === 1 ? '' : 's'}`;
    apply.disabled = !chosen.size;
  };
  panel.append(h('h3', 'Swaps'),
    h('p.small.muted', 'Each step is the single swap that helped most, so the scores assume the swaps above it too. Untick any you’d rather not make.'),
    h('ol.mf-swaps', swaps.map((s, i) => h('li.mf-swap',
      h('label.mf-check', h('input', { type: 'checkbox', checked: true, 'aria-label': `Swap ${s.remove.name} for ${s.add.name}`,
        onchange: e => { if (e.target.checked) chosen.add(i); else chosen.delete(i); syncApply(); } })),
      miniCard(s.remove, 'out'),
      h('span.mf-to', { 'aria-hidden': 'true' }, '→'),
      miniCard(s.add, 'in'),
      h('div.mf-detail',
        h('div', h('b', s.remove.name), ' → ', h('b', s.add.name)),
        s.note ? h('div.small', s.note) : null,
        h('div.small.muted', [s.add.price_usd != null ? money(s.add.price_usd) : null, s.add.spare != null ? `spare: ${int(s.add.spare)}` : null,
          whyText(s.remove.why)].filter(Boolean).join(' · ')),
        s.score_after != null ? h('div.small', 'castable on curve after this: ', h('b', pct(s.score_after))) : null)))),
    h('div.form-row', apply));
  syncApply();
  apply.addEventListener('click', async () => {
    const picked = swaps.filter((_, i) => chosen.has(i)).map(s => ({ remove_line_id: s.remove.line_id, add_pool: s.add.pool }));
    apply.disabled = true;
    apply.textContent = 'Applying…';
    try {
      const result = await api.decks.manafixApply(deck.deck_id, picked);
      toast(`Applied ${picked.length} swap${picked.length === 1 ? '' : 's'}`);
      if (result.warning) toast(result.warning);
      await onApplied(result.warning);
    } catch (error) {
      toast('Could not apply: ' + error.message);
      syncApply();
    }
  });
}

function whyText(why) {
  if (!why) return null;
  if (why === 'basic') return 'replacing a basic land';
  if (/tapped/.test(why)) return 'replacing a land that enters tapped';
  return `replacing a land (${why.replace(/_/g, ' ')})`;
}

function beforeAfter(a, b, fmt) {
  if (a == null && b == null) return '—';
  if (b == null || a === b) return fmt(a);
  const up = b > a;
  return h('span', fmt(a), ' → ', h('b', { class: up ? 'odds good' : 'odds bad' }, (up ? '▲ ' : '▼ ') + fmt(b)));
}

function miniCard(side, kind) {
  const face = cardFace({ name: side.name, image: resize(side.image, 'small'), finish: side.finish || 'normal' }, { size: 'small', flip: false }).node;
  return h('span', { class: `mf-card ${kind}`, title: `${kind === 'out' ? 'Take out' : 'Put in'}: ${side.name}` }, face);
}
