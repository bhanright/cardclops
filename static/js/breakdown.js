// Color / curve / type / rarity breakdowns shared by the answer strip and the dashboard.
import { h, int, COLOR_NAMES, RARITY_ORDER, titleCase } from './util.js';
import { barList, stackBar, columnChart } from './charts.js';

export const COLOR_VARS = { W: 'var(--mana-W)', U: 'var(--mana-U)', B: 'var(--mana-B)', R: 'var(--mana-R)', G: 'var(--mana-G)', C: 'var(--mana-C)', M: 'var(--mana-M)' };
export const RARITY_VARS = { common: 'var(--r-common)', uncommon: 'var(--r-uncommon)', rare: 'var(--r-rare)', mythic: 'var(--r-mythic)', special: 'var(--r-special)', bonus: 'var(--r-special)' };
const TYPE_COLORS = ['var(--pink)', 'var(--lime)', 'var(--cyan)', 'var(--orange)', 'var(--yellow)', 'var(--violet)', 'var(--mint)', 'var(--peach)'];

/** onPick(kind, key) lets a click add a filter (e.g. c:r, t:creature, r:rare, mv=3). */
export function breakdownPanels(breakdown, { onPick, large = false } = {}) {
  if (!breakdown) return [];
  const colors = breakdown.colors || {};
  const colorItems = 'WUBRGCM'.split('').map(key => ({ key, short: key, label: COLOR_NAMES[key], value: colors[key] || 0, color: COLOR_VARS[key] }));
  const curve = breakdown.curve || {};
  const curveItems = Object.keys(curve).map(key => ({ label: key, value: curve[key] || 0, color: 'var(--cyan)', tip: `mana value ${key}` }));
  const types = Object.entries(breakdown.types || {}).sort((a, b) => b[1] - a[1]);
  const rarity = breakdown.rarity || {};
  const rarityItems = Object.keys(rarity).sort((a, b) => RARITY_ORDER.indexOf(a) - RARITY_ORDER.indexOf(b))
    .map(key => ({ key, short: key[0].toUpperCase(), label: titleCase(key), value: rarity[key] || 0, color: RARITY_VARS[key] || 'var(--pink)' }));

  const pick = (kind, key) => (onPick ? () => onPick(kind, key) : null);
  const panels = [
    h('div.bd', h('h4', 'Colors'), stackBar(colorItems),
      onPick ? h('div.mini-filters', colorItems.filter(i => i.value && i.key !== 'M').map(i =>
        h('button.mini-chip', { type: 'button', onclick: pick('color', i.key), title: `Add c:${i.key.toLowerCase()}` }, h('span.swatch', { style: { background: i.color } }), i.key)))
        : null),
    h('div.bd', h('h4', 'Mana curve ', h('span.muted.small', '(nonland copies)')), columnChart(curveItems, { height: large ? 150 : 84 })),
    h('div.bd', h('h4', 'Types'), barList(types.slice(0, large ? 10 : 6).map(([type, value], i) => ({
      label: type, value, color: TYPE_COLORS[i % TYPE_COLORS.length], onclick: pick('type', type), title: onPick ? `Add t:${type.toLowerCase()}` : null,
    })))),
    h('div.bd', h('h4', 'Rarity'), stackBar(rarityItems)),
  ];
  return panels;
}

export { int };
