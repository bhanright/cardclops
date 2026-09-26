// Deck statistics panels, drawn from the `stats` object of GET /api/decks/<id> (see docs/DECKS.md).
import { h, int, money, symbol, manaCost, formatLabel, sortFormats, titleCase, COLOR_NAMES } from './util.js';
import { barList, stackBar } from './charts.js';
import { COLOR_VARS, RARITY_VARS } from './breakdown.js';

const TYPE_COLORS = {
  Creature: 'var(--lime)', Planeswalker: 'var(--orange)', Battle: 'var(--peach)', Instant: 'var(--cyan)',
  Sorcery: 'var(--pink)', Artifact: 'var(--mana-C)', Enchantment: 'var(--violet)', Land: 'var(--yellow)', Kindred: 'var(--mint)', Other: 'var(--faint)',
};
const FUNCTION_LABELS = {
  ramp: 'Ramp', card_draw: 'Card draw', removal: 'Removal', board_wipes: 'Board wipes', counterspells: 'Counterspells',
  tutors: 'Tutors', protection: 'Protection', recursion: 'Recursion',
};
const BRACKET_LABELS = { game_changers: 'Game changers', extra_turns: 'Extra turns', mass_land_denial: 'Mass land denial', tutors: 'Tutors' };
const pct = p => (p == null ? '—' : `${Math.round(p * 100)}%`);
const odds = p => (p == null ? 'flat' : p >= 0.9 ? 'good' : p >= 0.75 ? 'ok' : 'bad');
const oddsMark = p => (p == null ? '' : p >= 0.9 ? '▲ ' : p >= 0.75 ? '● ' : '▼ ');

function panel(title, cls, ...content) {
  return h('section', { class: `panel stat-panel ${cls}` }, h('h3', title), ...content);
}

/** onOpen(name) opens the card detail for a card named in the deck. */
export function statsPanels(stats, { onOpen = () => {}, colorAction = null } = {}) {
  if (!stats) return [h('div.chart-empty', 'No statistics for this deck yet.')];
  const nameButton = name => h('button.name-btn', { type: 'button', onclick: () => onOpen(name) }, name);
  return [
    curvePanel(stats),
    colorPanel(stats, colorAction),
    castabilityPanel(stats, nameButton),
    openingHandPanel(stats),
    landKindsPanel(stats),
    functionsPanel(stats, nameButton),
    bracketPanel(stats, nameButton),
    legalityPanel(stats, nameButton),
    typesPanel(stats),
    pricePanel(stats, nameButton),
  ].filter(Boolean);
}

// ---------- curve ----------
function curvePanel(stats) {
  const curve = stats.curve || {};
  const byType = stats.curve_by_type || {};
  const buckets = Object.keys(curve);
  const types = Object.keys(byType).filter(t => Object.values(byType[t] || {}).some(Boolean));
  const max = Math.max(1, ...buckets.map(b => curve[b] || 0));
  const counts = stats.counts || {};
  return panel('Mana curve', 'st-curve',
    h('p.panel-sub', `Average mana value ${stats.average_mv?.nonland?.toFixed(2) ?? '—'} (nonland) · `,
      `${int(counts.lands)} lands, ${int(counts.nonlands)} nonland, ${int(counts.creatures)} creatures`,
      counts.mdfc_lands ? ` · ${int(counts.mdfc_lands)} modal double-faced card${counts.mdfc_lands === 1 ? '' : 's'} also count${counts.mdfc_lands === 1 ? 's' : ''} as land${counts.mdfc_lands === 1 ? '' : 's'}` : '',
      counts.sideboard ? ` · ${int(counts.sideboard)} in the sideboard` : ''),
    h('div.stack-cols', { role: 'img', 'aria-label': 'Mana curve: ' + buckets.map(b => `${b}: ${curve[b] || 0}`).join(', ') },
      buckets.map(b => h('div.scol',
        h('span.scol-total', curve[b] ? int(curve[b]) : ''),
        h('div.scol-bar', { style: { height: `${((curve[b] || 0) / max) * 100}%` } },
          (types.length ? types : ['Other']).map(t => {
            const n = types.length ? byType[t]?.[b] || 0 : curve[b] || 0;
            return n ? h('span.scol-seg', { style: { flexGrow: n, background: TYPE_COLORS[t] || 'var(--faint)' }, title: `${t}: ${n} at mana value ${b}` }) : null;
          })),
        h('span.scol-label', b)))),
    types.length > 1 ? h('div.stack-legend', types.map(t => h('span.legend-item', h('span.swatch', { style: { background: TYPE_COLORS[t] || 'var(--faint)' } }), t))) : null);
}

// ---------- color balance ----------
function colorPanel(stats, action) {
  const pips = stats.pips || {}, share = stats.pip_share || {}, sources = stats.sources || {}, nonland = stats.nonland_sources || {}, sourceShare = stats.source_share || {};
  const colors = 'WUBRG'.split('').filter(c => (pips[c] || 0) > 0 || (sources[c] || 0) > 0);
  if (!colors.length) return panel('Color balance', 'st-colors', h('div.chart-empty', 'Colorless deck: no colored pips to balance.'));
  const rows = colors.map(c => {
    const want = share[c] || 0, give = sourceShare[c] || 0;
    const short = want - give > 0.05;
    return h('div', { class: 'cb-row' + (short ? ' short' : '') },
      h('div.cb-head', symbol(c), h('b', COLOR_NAMES[c]),
        h('span.cb-sentence', `costs want ${pct(want)}, sources give ${pct(give)}`),
        short ? h('span.cb-warn', { title: 'This color has a smaller share of your sources than of your costs' }, '▼ short') : null),
      h('div.cb-bars',
        h('span.cb-label', 'Costs'),
        h('span.cb-track', h('span.cb-fill', { style: { width: pct(want), background: COLOR_VARS[c] } })),
        h('span.cb-num', `${pct(want)} · ${fmtPips(pips[c])} pips`),
        h('span.cb-label', 'Sources'),
        h('span.cb-track', h('span.cb-fill.hatched', { style: { width: pct(give), '--fill': COLOR_VARS[c] } })),
        h('span.cb-num', `${pct(give)} · ${int(sources[c] || 0)} lands${nonland[c] ? ` + ${int(nonland[c])} other` : ''}`)));
  });
  return panel('Color balance', 'st-colors wide',
    h('p.panel-sub', 'Share of colored mana symbols in your costs, next to the share of mana sources that make each color.',
      sources.any ? ` ${int(sources.any)} lands make any color.` : '', pips.C ? ` ${fmtPips(pips.C)} {C} pips; ${int(sources.C || 0)} colorless sources.` : ''),
    h('div.cb-rows', rows),
    action ? h('div.form-row', action) : null);
}
const fmtPips = n => (n == null ? '0' : Number.isInteger(n) ? int(n) : n.toFixed(1));

// ---------- castability ----------
function castabilityPanel(stats, nameButton) {
  const cast = stats.castability || {};
  const byColor = cast.by_color || {};
  const colors = 'WUBRGC'.split('').filter(c => byColor[c]);
  const hardest = cast.hardest || [];
  return panel('Castability', 'st-cast',
    h('p.panel-sub', 'Chance of having enough sources of a color on curve, on the play (hypergeometric).'),
    colors.length ? h('div.table-scroll', h('table.table.odds-table',
      h('thead', h('tr', h('th', 'Color'), h('th.num', '1 pip, T1'), h('th.num', '2 pips, T2'), h('th.num', '3 pips, T3'))),
      h('tbody', colors.map(c => h('tr', h('td', symbol(c), ' ', COLOR_NAMES[c]),
        ['1', '2', '3'].map(k => h('td.num', h('span', { class: 'odds ' + odds(byColor[c][k]) }, oddsMark(byColor[c][k]) + pct(byColor[c][k])))))))))
      : h('p.muted', 'No colored costs.'),
    hardest.length ? h('div', h('h4', 'Hardest to cast on curve'),
      h('ol.hardest', hardest.map(x => h('li',
        nameButton(x.name), ' ', manaCost(x.mana_cost),
        h('span.muted.small', ` T${x.turn}: ${x.needed}× ${COLOR_NAMES[x.color] || x.color}`),
        h('span', { class: 'odds ' + odds(x.probability) }, oddsMark(x.probability) + pct(x.probability)))))) : null);
}

// ---------- opening hand ----------
function openingHandPanel(stats) {
  const hand = stats.opening_hand;
  if (!hand) return null;
  const dist = hand.lands_in_seven || {};
  const keys = Object.keys(dist).sort((a, b) => a - b);
  const max = Math.max(0.01, ...keys.map(k => dist[k] || 0));
  const drops = hand.expected_land_drops || {};
  return panel('Opening hand', 'st-hand',
    h('div.hand-top',
      h('div.big-odds', { class: odds(hand.two_to_four) }, h('div.bo-value', pct(hand.two_to_four)), h('div.bo-label', '2–4 lands in 7')),
      h('p.muted.small', `${int(hand.lands)} lands in ${int(hand.deck_size)} cards`,
        stats.counts?.mdfc_lands ? `, plus ${int(stats.counts.mdfc_lands)} modal double-faced card${stats.counts.mdfc_lands === 1 ? '' : 's'} with a land side` : '', '.')),
    h('div.land-dist', { role: 'img', 'aria-label': 'Lands in a 7-card hand: ' + keys.map(k => `${k}: ${pct(dist[k])}`).join(', ') },
      keys.map(k => h('div', { class: 'ld-col' + (k >= 2 && k <= 4 ? ' sweet' : '') },
        h('span.ld-pct', pct(dist[k])),
        h('div.ld-track', h('span.ld-bar', { style: { height: `${((dist[k] || 0) / max) * 100}%` } })),
        h('span.ld-label', k)))),
    h('p.small.muted', 'Lands in a 7-card hand. Green columns are the keepable 2–4.'),
    Object.keys(drops).length ? h('div', h('h4', 'Hitting land drops (on the play)'),
      h('div.drops', Object.keys(drops).sort((a, b) => a - b).map(t => h('div', { class: 'drop odds ' + odds(drops[t]) },
        h('span.drop-t', 'T' + t), h('b', pct(drops[t])))))) : null);
}

// ---------- land kinds ----------
const LAND_KIND_LABELS = { basic: 'Basic', mono: 'Mono-color', dual: 'Dual', tri: 'Tri', five_color: 'Any color', colorless: 'Colorless', fetch: 'Fetch' };
function landKindsPanel(stats) {
  const kinds = Object.entries(stats.land_kinds || {}).filter(([, n]) => n > 0);
  if (!kinds.length) return null;
  const colors = ['var(--yellow)', 'var(--cyan)', 'var(--lime)', 'var(--pink)', 'var(--orange)', 'var(--mana-C)', 'var(--violet)'];
  return panel('Lands', 'st-lands', barList(kinds.map(([k, n], i) => ({ label: LAND_KIND_LABELS[k] || titleCase(k), value: n, color: colors[i % colors.length] }))));
}

// ---------- functions & bracket ----------
function namedCounts(entries, labels, nameButton, { alert = [] } = {}) {
  return h('div.fn-list', entries.map(([key, names]) => {
    const list = names || [];
    const label = labels[key] || titleCase(key);
    if (!list.length) return h('div.fn-row.none', h('span.fn-label', label), h('span.fn-count', '0'));
    return h('details', { class: 'fn-row' + (alert.includes(key) ? ' alert' : '') },
      h('summary', h('span.fn-label', label), h('span.fn-count', int(list.length))),
      h('div.fn-names', list.map(nameButton)));
  }));
}

function functionsPanel(stats, nameButton) {
  const f = stats.functions || {};
  const keys = [...Object.keys(FUNCTION_LABELS), ...Object.keys(f).filter(k => !FUNCTION_LABELS[k])];
  return panel('What the cards do', 'st-functions', h('p.panel-sub', 'From Scryfall Tagger tags. Open a row for the card names.'),
    namedCounts(keys.filter(k => k in f || FUNCTION_LABELS[k]).map(k => [k, f[k]]), FUNCTION_LABELS, nameButton));
}

function bracketPanel(stats, nameButton) {
  const b = stats.bracket_signals;
  if (!b) return null;
  const keys = [...Object.keys(BRACKET_LABELS), ...Object.keys(b).filter(k => !BRACKET_LABELS[k])].filter(k => k in b);
  const total = keys.reduce((s, k) => s + (b[k]?.length || 0), 0);
  return panel('Bracket signals', 'st-bracket',
    h('p.panel-sub', total ? 'Cards that push a Commander deck up the power brackets.' : 'Nothing here pushes the deck up a bracket.'),
    namedCounts(keys.map(k => [k, b[k]]), BRACKET_LABELS, nameButton, { alert: keys }));
}

// ---------- legality ----------
function legalityPanel(stats, nameButton) {
  const l = stats.legality;
  if (!l) return null;
  const others = l.other_formats || {};
  return panel('Legality', 'st-legal',
    h('div', { class: 'legal-verdict ' + (l.legal ? 'ok' : 'bad') }, l.legal ? '✓ Legal' : '✗ Not legal', ' in ', l.format ? formatLabel(l.format) : 'its format'),
    l.problems?.length ? h('ul.problems', l.problems.map(p => h('li', p.name ? [nameButton(p.name), ' — '] : null, p.reason))) : null,
    l.warnings?.length ? h('ul.legal-warnings', l.warnings.map(w => h('li', '⚠ ', w.name ? [nameButton(w.name), ' — '] : null, w.reason))) : null,
    Object.keys(others).length ? h('div', h('h4', 'Other formats ', h('span.muted.small', '(cards only, not deck size)')),
      h('div.format-chips', sortFormats(Object.keys(others)).map(f =>
        h('span', { class: 'fmt-chip ' + (others[f] ? 'ok' : 'bad') }, (others[f] ? '✓ ' : '✗ ') + formatLabel(f))))) : null);
}

// ---------- types, rarity, price ----------
function typesPanel(stats) {
  const types = Object.entries(stats.types || {}).sort((a, b) => b[1] - a[1]);
  const rarity = stats.rarity || {};
  const order = ['common', 'uncommon', 'rare', 'mythic', 'special', 'bonus'];
  const rarityItems = Object.keys(rarity).sort((a, b) => order.indexOf(a) - order.indexOf(b))
    .map(k => ({ key: k, short: k[0].toUpperCase(), label: titleCase(k), value: rarity[k], color: RARITY_VARS[k] || 'var(--pink)' }));
  return panel('Types & rarity', 'st-types',
    barList(types.map(([t, n]) => ({ label: t, value: n, color: TYPE_COLORS[t] || 'var(--faint)' }))),
    rarityItems.length ? h('div', h('h4', 'Rarity'), stackBar(rarityItems)) : null);
}

function pricePanel(stats, nameButton) {
  const p = stats.price;
  if (!p) return null;
  return panel('Price', 'st-price',
    h('div.price-top', h('div', h('div.big-number', money(p.total_usd, { whole: p.total_usd >= 1000 })), h('div.muted.small', 'total')),
      h('div', h('div.mid-number', money(p.average_usd)), h('div.muted.small', 'average card'))),
    p.most_expensive?.length ? h('ol.top5', p.most_expensive.map(x => h('li', nameButton(x.name), h('span.t5-price', money(x.price_usd))))) : null);
}
