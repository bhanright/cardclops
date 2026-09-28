// Deck page extras from docs/TOOLS2.md: copy policy (default / budget / bling) with a preview,
// the goldfish simulator, and the version history with restore.
import { h, clear, int, money, signedMoney, signedClass, spinner, errorBox, symbol, finishLabel, toast } from './util.js';
import { api } from './api.js';
import { turnChart, columnChart, stackBar } from './charts.js';

export const POLICIES = [
  ['default', 'Default', 'The printing the list names, if you own it; otherwise your most valuable free copy (basics: the cheapest).'],
  ['budget', 'Budget', 'The cheapest free copy, so valuable printings stay in the binder.'],
  ['bling', 'Bling', 'The fanciest free copy: foil or etched first, then borderless, full art, showcase…, then price.'],
];
const POLICY_LABEL = Object.fromEntries(POLICIES.map(([k, l]) => [k, l]));
export const policyMark = p => (p === 'budget' ? '$ Budget' : p === 'bling' ? '✦ Bling' : null);

// ---------- copy policy ----------
/** A segmented control for the header plus a preview panel; onChanged() reloads the deck. */
export function copyPolicyControls(deck, { onChanged }) {
  const current = deck.copy_policy || 'default';
  const panel = h('div.policy-panel', { hidden: true, id: 'policyPreview', 'aria-live': 'polite' });
  let preview = null;
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Which copies this deck uses' },
    POLICIES.map(([key, label, tip]) => h('button.seg-btn', { type: 'button', class: key === current ? 'on' : null, 'aria-pressed': String(key === current),
      title: tip, 'aria-controls': 'policyPreview', onclick: () => open(key) }, label)));
  async function open(focus) {
    panel.hidden = false;
    if (!preview) {
      clear(panel).append(spinner('Working out which copies each option would use…'));
      try { preview = await api.decks.copyPolicies(deck.deck_id); } catch (error) { clear(panel).append(errorBox(error.message)); return; }
    }
    render(focus);
  }
  function render(focus) {
    const opts = preview.options || {};
    clear(panel).append(
      h('div.panel-head', h('h3', 'Which copies should this deck use?'),
        h('button.icon-btn.small', { type: 'button', 'aria-label': 'Close copy options', onclick: () => { panel.hidden = true; } }, '×')),
      h('p.small.muted', 'Pinned copies always stay. Other decks keep their copies; this only chooses among your free ones.'),
      h('div.policy-grid', POLICIES.map(([key, label, tip]) => {
        const o = opts[key] || { changes: [] };
        const isCurrent = key === (preview.current || current);
        const changes = o.changes || [];
        return h('section', { class: 'policy-card' + (isCurrent ? ' current' : '') + (key === focus ? ' focus' : '') },
          h('div.pc-head', h('h4', label), isCurrent ? h('span.status-pill.on', '● In use') : null),
          h('p.small.muted', tip),
          h('div.pc-value', h('b', money(o.value_usd)),
            isCurrent ? h('span.muted.small', ' now') : h('span', { class: 'gain ' + signedClass(o.value_change_usd) }, ' ', signedMoney(o.value_change_usd))),
          changes.length ? h('details.pc-changes', { open: key === focus && changes.length <= 12 },
            h('summary', `${int(changes.length)} card${changes.length === 1 ? '' : 's'} would change`),
            h('ul', changes.map(c => h('li',
              h('b', c.name), h('span.pc-swap', copyText(c.from), ' → ', copyText(c.to)))))) : h('p.small.muted', isCurrent ? 'This is what the deck uses now.' : 'No card would change.'),
          isCurrent ? null : h('button.btn.small.go', { type: 'button', onclick: async e => {
            e.target.disabled = true;
            try { await api.decks.update(deck.deck_id, { copy_policy: key }); toast(`Deck now uses ${label.toLowerCase()} copies`); await onChanged(); }
            catch (error) { toast('Could not switch: ' + error.message); e.target.disabled = false; }
          } }, `Use ${label.toLowerCase()} copies`));
      })));
  }
  return { control: h('span.inline-label', 'Copies ', seg), panel };
}
const copyText = x => (x ? `${(x.set_code || '').toUpperCase()} #${x.collector_number}${x.finish && x.finish !== 'normal' ? ' ' + finishLabel(x.finish).toLowerCase() : ''} ${money(x.price_usd)}` : 'none');

// ---------- goldfish ----------
const pct = p => (p == null ? '—' : `${Math.round(p * 100)}%`);

export function goldfishSection(deck) {
  const opts = { games: 2000, turns: 10, play: 'first', seed: 1 };
  const out = h('div.gf-out');
  const games = h('select.select', { 'aria-label': 'Games to simulate' }, [500, 1000, 2000, 5000].map(n => h('option', { value: n, selected: n === opts.games }, `${int(n)} games`)));
  const turns = h('select.select', { 'aria-label': 'Turns per game' }, [6, 8, 10, 12].map(n => h('option', { value: n, selected: n === opts.turns }, `${n} turns`)));
  const play = h('div.seg', { role: 'group', 'aria-label': 'On the play or the draw' });
  const drawPlay = () => clear(play).append(...[['first', 'On the play'], ['second', 'On the draw']].map(([v, l]) =>
    h('button.seg-btn', { type: 'button', class: opts.play === v ? 'on' : null, 'aria-pressed': String(opts.play === v), onclick: () => { opts.play = v; drawPlay(); } }, l)));
  drawPlay();
  const seed = h('input.num-input', { type: 'number', value: opts.seed, min: 1, 'aria-label': 'Random seed' });
  const run = h('button.btn.go', { type: 'button', onclick: () => go() }, '≈ Goldfish it');
  const reseed = h('button.btn.small.ghost', { type: 'button', title: 'Same settings, different shuffles', onclick: () => { seed.value = Math.floor(Math.random() * 99999) + 1; go(); } }, '⟳ Reseed');
  async function go() {
    opts.games = +games.value; opts.turns = +turns.value; opts.seed = +seed.value || 1;
    run.disabled = true;
    clear(out).append(spinner(`Playing ${int(opts.games)} solitaire games…`));
    try { renderGoldfish(out, await api.decks.goldfish(deck.deck_id, opts)); }
    catch (error) { clear(out).append(errorBox(error.message)); }
    finally { run.disabled = false; }
  }
  clear(out).append(h('p.muted', 'Plays the deck alone thousands of times: mulligans, land drops, mana, when the commander lands and which spells get cast. A model, not a rules engine — see the assumptions.'));
  return h('section#dk-goldfish.panel.goldfish',
    h('div.panel-head', h('h2', 'Goldfish'), h('div.panel-tools', games, turns, play, h('label.inline-label', 'seed ', seed), reseed, run)),
    out);
}

function renderGoldfish(out, g) {
  const m = g.mulligans || {};
  const big = (label, value, color, tip) => h('div', { class: `bignum c-${color}`, title: tip || '' }, h('div.bn-value', value), h('div.bn-label', label));
  const cards = g.cards || [];
  let showAll = false;
  const table = h('div');
  const drawTable = () => {
    const rows = showAll ? cards : cards.slice(0, 20);
    clear(table).append(h('div.table-scroll', h('table.table.gf-cards',
      h('thead', h('tr', h('th', 'Card'), h('th.num', 'MV'), h('th', 'Cast in the game'), h('th.num', 'Median turn'))),
      h('tbody', rows.map(c => h('tr',
        h('td', c.name), h('td.num', int(c.mana_value)),
        h('td', h('span.cast-bar', h('span.cast-fill', { style: { width: pct(c.cast_rate) } })), ' ', h('b', pct(c.cast_rate))),
        h('td.num', c.median_turn != null ? 'T' + c.median_turn : '—')))))),
    cards.length > 20 ? h('button.btn.small.ghost', { type: 'button', onclick: () => { showAll = !showAll; drawTable(); } }, showAll ? 'Show top 20' : `Show all ${int(cards.length)}`) : null);
  };
  drawTable();
  const chart = h('div');
  const cmdChart = g.commander ? columnChart(Object.entries(g.commander.cast_by_turn || {}).map(([t, p]) => ({ label: 'T' + t, value: Math.round(p * 1000) / 10, color: 'var(--pink)', tip: `${pct(p)} have cast ${g.commander.name} by turn ${t}` })),
    { height: 130, format: v => `${v}%` }) : null;
  const sizes = Object.entries(m.by_size || {}).sort((a, b) => b[0] - a[0]);
  clear(out).append(
    h('p.small.muted', `${int(g.games)} games, ${g.turns} turns, ${g.on_the_play ? 'on the play' : 'on the draw'}, seed ${g.seed}${g.elapsed_ms ? ` · ${(g.elapsed_ms / 1000).toFixed(1)} s` : ''}.`),
    h('div.bignums.gf-nums',
      big('Mulligan rate', pct(m.rate), 'cyan', m.average_hand_size ? `Average kept hand: ${m.average_hand_size.toFixed(2)} cards` : ''),
      big(g.commander ? 'Commander, median' : 'Commander', g.commander?.median_turn != null ? `T${g.commander.median_turn}` : '—', 'pink', g.commander ? g.commander.name : 'No commander'),
      big('Mana screw', pct(g.screw_rate), 'orange', '2 or fewer lands in play at the end of turn 4'),
      big('Flood', pct(g.flood_rate), 'violet', '7+ lands in play and at most 1 card in hand at the end of turn 7'),
      big('Color screw', pct(g.color_screw_rate), 'yellow', 'Turn 4: a spell in hand has enough mana but not its colors'),
      big('Efficiency', pct(g.efficiency), 'lime', 'Mana spent ÷ mana available over all turns')),
    sizes.length ? h('div.gf-mull', h('h4', 'Kept hand size'), stackBar(sizes.map(([k, v], i) => ({ key: k, short: k, label: `${k} cards`, value: Math.round(v * 1000) / 10,
      color: ['var(--lime)', 'var(--yellow)', 'var(--orange)', 'var(--pink)'][i] || 'var(--pink)' })), { format: v => `${v}%` })) : null,
    h('div.gf-charts',
      h('div', h('h4', 'By turn (averages)'), chart),
      cmdChart ? h('div', h('h4', `${g.commander.name}: cast by turn`), cmdChart) : null),
    h('h4', 'Which spells get cast'), table,
    g.sample_game?.length ? h('details.gf-sample', h('summary', 'One sample game'),
      h('ol.gf-log', g.sample_game.map(t => h('li',
        h('b', `T${t.turn}`), h('span.muted', ` hand ${t.hand_size} · mana ${t.mana}, spent ${t.spent}`),
        h('div', t.land ? ['Land: ', h('b', t.land)] : h('span.muted', 'No land drop'), t.cast?.length ? [' · Cast: ', t.cast.join(', ')] : null))))) : null,
    g.assumptions?.length ? h('details.gf-assume', h('summary', 'Assumptions'), h('ul', g.assumptions.map(a => h('li', a)))) : null);
  turnChart(chart, { series: [
    { label: 'Lands in play', color: 'var(--yellow)', dash: '7 5', values: g.lands_by_turn || {} },
    { label: 'Mana available', color: 'var(--cyan)', values: g.mana_by_turn || {} },
    { label: 'Mana spent', color: 'var(--lime)', values: g.spent_by_turn || {} }] });
}

// ---------- history ----------
const REASON = { create: 'Created', import: 'Imported', replace: 'List replaced', sync: 'Synced from Archidekt', manafix: 'Mana-base fixer', commander: 'Commander changed', restore: 'Restored', edit: 'Edited' };

export function historySection(deck, { onRestored }) {
  const box = h('div', spinner('Loading history…'));
  api.decks.versions(deck.deck_id).then(({ versions = [] }) => {
    clear(box);
    if (!versions.length) { box.append(h('div.chart-empty', 'No versions recorded yet. Every list change from now on is kept here.')); return; }
    box.append(h('ol.versions', versions.map((v, i) => versionItem(deck, v, i === 0, onRestored))));
  }, error => clear(box).append(errorBox(error.message)));
  return h('section#dk-history.panel.history', h('h2', 'History'), h('p.small.muted', 'Every change to the list, with what it did to the numbers.'), box);
}

function when(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' });
}
const signed = (n, digits = 0) => `${n > 0 ? '+' : n < 0 ? '−' : '±'}${Math.abs(n).toFixed(digits)}`;

function diffChips(diff) {
  if (!diff) return [];
  const chips = [];
  const chip = (text, good, title) => h('span', { class: 'diff-chip ' + (good == null ? 'flat' : good ? 'up' : 'down'), title: title || '' }, text);
  if (diff.castability) chips.push(chip(`Castable on curve ${diff.castability > 0 ? '▲' : '▼'} ${signed(diff.castability * 100, 1)} pts`, diff.castability > 0));
  if (diff.average_mv) chips.push(chip(`Avg MV ${signed(diff.average_mv, 2)}`, diff.average_mv < 0, 'Lower average mana value is usually smoother'));
  if (diff.lands) chips.push(chip(`Lands ${signed(diff.lands)}`, null));
  for (const [c, n] of Object.entries(diff.sources || {})) if (n) chips.push(h('span', { class: 'diff-chip ' + (n > 0 ? 'up' : 'down') }, symbol(c), ` sources ${signed(n)}`));
  for (const [f, n] of Object.entries(diff.functions || {})) if (n) chips.push(chip(`${f.replace(/_/g, ' ')} ${signed(n)}`, n > 0));
  const curve = Object.entries(diff.curve || {}).filter(([, n]) => n);
  if (curve.length) chips.push(chip(`Curve: ${curve.map(([k, n]) => `${signed(n)} at ${k}`).join(', ')}`, null));
  if (diff.price_usd) chips.push(chip(`Value ${signedMoney(diff.price_usd)}`, null));
  if (diff.legal != null) chips.push(chip(diff.legal ? '✓ Became legal' : '✗ Became illegal', !!diff.legal));
  return chips;
}

function versionItem(deck, v, isLatest, onRestored) {
  const added = v.added || [], removed = v.removed || [];
  const confirmBox = h('span.del-box');
  const showRestore = () => clear(confirmBox).append(isLatest ? h('span.status-pill.on', '● Current') :
    h('button.btn.small', { type: 'button', onclick: () => {
      const yes = h('button.btn.small.danger', { type: 'button', onclick: async () => {
        yes.disabled = true;
        try { await api.decks.restoreVersion(deck.deck_id, v.version_id); toast('Version restored'); await onRestored(); }
        catch (error) { toast('Could not restore: ' + error.message); yes.disabled = false; }
      } }, 'Yes, restore');
      clear(confirmBox).append(h('span.confirm-text', 'Replace the current list with this one?'), yes, h('button.btn.small.ghost', { type: 'button', onclick: showRestore }, 'Keep current'));
      yes.focus();
    } }, '⟲ Restore this version'));
  showRestore();
  const s = v.stats || {};
  return h('li.version',
    h('div.v-head', h('span', { class: 'reason-badge r-' + v.reason }, REASON[v.reason] || v.reason), h('b', when(v.created_at)),
      h('span.muted.small', `${int(v.card_count)} cards${s.castability != null ? ` · ${Math.round(s.castability * 100)}% castable on curve` : ''}${s.price_usd != null ? ` · ${money(s.price_usd)}` : ''}`), confirmBox),
    added.length || removed.length ? h('div.v-cards',
      added.map(c => h('span.card-chip.add', `+${c.quantity} ${c.name}`, c.section !== 'main' ? h('span.muted', ` (${c.section})`) : null)),
      removed.map(c => h('span.card-chip.del', `−${c.quantity} ${c.name}`, c.section !== 'main' ? h('span.muted', ` (${c.section})`) : null)))
      : h('p.small.muted', v.diff ? 'Same cards; allocation or commander changed.' : 'First recorded version.'),
    v.diff ? h('div.v-diff', diffChips(v.diff)) : null);
}

export { POLICY_LABEL };
