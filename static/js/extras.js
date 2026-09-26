// Trade binder: copies beyond what you'd keep for constructed (4) or singleton (1).
import { h, $, clear, int, money, spinner, errorBox, copyText, toast, store, finishLabel } from './util.js';
import { api } from './api.js';
import { lazyImg, rarityGem, attachHoverPreview } from './cards.js';
import { openCard } from './detail.js';

const state = { keep: 4, min_price: 0.5, ...store.get('gallery.extras', {}) };
let built = false;
let out, controls, stale = true;

export function showExtras() {
  const root = $('#view-extras');
  if (!built) {
    built = true;
    controls = h('div.panel-tools');
    out = h('div.extras-out');
    root.append(h('section.panel.extras-head',
      h('div.panel-head', h('h2', 'Trade binder'), controls),
      h('p.muted', 'Cards you hold more copies of than you’d ever play, most valuable extras first. ',
        h('b', 'Only spare copies count:'), ' copies your active decks use are never offered for trade.')), out);
    buildControls();
  }
  if (stale) load();
}

function buildControls() {
  clear(controls);
  const seg = h('div.seg', { role: 'group', 'aria-label': 'Copies to keep' },
    [[4, 'Keep 4 · constructed'], [1, 'Keep 1 · singleton']].map(([keep, label]) => h('button.seg-btn', {
      type: 'button', class: state.keep === keep ? 'on' : null, 'aria-pressed': String(state.keep === keep),
      onclick: () => { state.keep = keep; save(); buildControls(); load(); } }, label)));
  const min = h('input.num-input', { type: 'number', min: 0, step: 0.25, value: state.min_price, 'aria-label': 'Minimum price per copy in dollars' });
  min.addEventListener('change', () => { state.min_price = Math.max(0, +min.value || 0); save(); load(); });
  controls.append(seg, h('label.inline-label', 'min $', min));
}
function save() { store.set('gallery.extras', state); }

async function load() {
  stale = false;
  clear(out).append(spinner('Counting spares…'));
  let data;
  try { data = await api.extras({ keep: state.keep, min_price: state.min_price }); } catch (error) {
    clear(out).append(errorBox(error.message)); stale = true; return;
  }
  const cards = data.cards || [];
  clear(out).append(h('div.panel.extras-summary',
    h('div.bignums',
      h('div.bignum.c-lime', h('div.bn-value', int(data.total_extra_copies)), h('div.bn-label', 'Extra copies')),
      h('div.bignum.c-yellow', h('div.bn-value', money(data.total_extra_value_usd, { whole: data.total_extra_value_usd >= 1000 })), h('div.bn-label', 'Extra value')),
      h('div.bignum.c-cyan', h('div.bn-value', int(cards.length)), h('div.bn-label', 'Different cards'))),
    h('div.form-row', h('button.btn', { type: 'button', disabled: !cards.length, onclick: async () => {
      const text = cards.map(c => `${c.extra} ${c.name}`).join('\n');
      toast((await copyText(text)) ? `Copied ${cards.length} lines` : 'Copy failed');
    } }, '⧉ Copy trade list'), h('span.muted.small', `Keeping ${data.keep} of each; cards priced from ${money(state.min_price)}.`))));
  if (!cards.length) { out.append(h('div.chart-empty', 'No spares at this price — try lowering the minimum.')); return; }
  const nav = i => ({ index: i, count: () => cards.length, get: j => Promise.resolve(cards[j]) });
  out.append(h('div.panel.extras-table', h('div.xrow.xhead', ['', 'Card', 'Held', 'Extra', 'Price', 'Extra value'].map(t => h('span', t))),
    cards.map((c, i) => {
      const row = h('button.xrow', { type: 'button', onclick: () => openCard(c.scryfall_id, nav(i)) },
        h('span.x-thumb', lazyImg(c.image.replace(/\/normal$/, '/art_crop'), '', 'mv-img')),
        h('span.x-name', rarityGem(c.rarity), ' ', h('b', c.name), h('span.muted.small', ` ${(c.set_code || '').toUpperCase()}${c.finish && c.finish !== 'normal' ? ' · ' + finishLabel(c.finish) : ''}`)),
        h('span.x-num', int(c.quantity)),
        h('span.x-num.x-extra', '+' + int(c.extra)),
        h('span.x-num', money(c.price_usd)),
        h('span.x-num.x-value', money(c.extra_value_usd)));
      attachHoverPreview(row, c);
      return row;
    })));
}

export function markExtrasStale() { stale = true; }
