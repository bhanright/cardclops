// Small shared helpers: DOM building, formatting, mana symbols, storage, clipboard.

/** Build an element: h('div.a.b', {attrs}, children...). Strings become text nodes. */
export function h(tag, attrs, ...children) {
  const [head, ...classes] = tag.split('.');
  const [name, id] = head.split('#');
  const node = document.createElement(name || 'div');
  if (id) node.id = id;
  if (classes.length) node.className = classes.join(' ');
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className += (node.className ? ' ' : '') + value;
    else if (key === 'style' && typeof value === 'object') {
      for (const [prop, v] of Object.entries(value)) {
        if (prop.startsWith('--')) node.style.setProperty(prop, v); else node.style[prop] = v;
      }
    }
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (key === 'html') node.innerHTML = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child == null || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export function clear(node) {
  while (node.firstChild) node.firstChild.remove();
  return node;
}

export function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) if (value != null) node.setAttribute(key, value);
  for (const child of children.flat()) if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

// ---------- numbers ----------
const moneyCents = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const moneyWhole = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const integer = new Intl.NumberFormat('en-US');

export function money(value, { whole = false } = {}) {
  if (value == null || Number.isNaN(value)) return '—';
  if (whole || Math.abs(value) >= 10000) return moneyWhole.format(value);
  return moneyCents.format(value);
}

/** Signed money: "+$1.20" / "−$3.00". */
export function signedMoney(value, options) {
  if (value == null) return '—';
  const sign = value > 0.004 ? '+' : value < -0.004 ? '−' : '±';
  return sign + money(Math.abs(value), options);
}

export const int = value => (value == null ? '—' : integer.format(value));

/** Compact money for axes: $1.2k, $28k. */
export function moneyShort(value) {
  if (value == null) return '—';
  const abs = Math.abs(value);
  if (abs >= 1e6) return '$' + (value / 1e6).toFixed(1) + 'M';
  if (abs >= 1e4) return '$' + Math.round(value / 1e3) + 'k';
  if (abs >= 1e3) return '$' + (value / 1e3).toFixed(1) + 'k';
  if (abs >= 100) return '$' + Math.round(value);
  if (abs >= 1) return '$' + value.toFixed(2).replace(/\.00$/, '');
  return '$' + value.toFixed(2);
}

/** A change chip: "▲ 4.2%" (up), "▼ 3.1%" (down), "● 0.0%" (flat) — shape and sign, not color alone. */
export function changeChip(percent, { label = '', small = false } = {}) {
  if (percent == null) return h('span.chg.flat' + (small ? '.small' : ''), { title: 'No price history yet' }, label ? label + ' ' : '', '—');
  const dir = percent > 0.05 ? 'up' : percent < -0.05 ? 'down' : 'flat';
  const arrow = dir === 'up' ? '▲' : dir === 'down' ? '▼' : '●';
  const sign = dir === 'up' ? '+' : dir === 'down' ? '−' : '';
  return h(`span.chg.${dir}` + (small ? '.small' : ''), label ? label + ' ' : '', arrow + ' ' + sign + Math.abs(percent).toFixed(1) + '%');
}

export function signedClass(value) {
  if (value == null) return 'flat';
  return value > 0.004 ? 'up' : value < -0.004 ? 'down' : 'flat';
}

// ---------- mana symbols ----------
const SYMBOL_TEXT = { T: '⟳', Q: '⟲', S: '❄', E: 'E', CHAOS: '✺', PW: 'PW', TK: 'TK', A: 'A' };

/** Render a mana cost / rules text string, turning {X} tokens into styled symbols. */
export function manaNodes(text) {
  const out = [];
  if (!text) return out;
  const parts = String(text).split(/(\{[^}]+\})/g);
  for (const part of parts) {
    if (!part) continue;
    const match = part.match(/^\{([^}]+)\}$/);
    if (!match) { out.push(document.createTextNode(part)); continue; }
    out.push(symbol(match[1]));
  }
  return out;
}

export function symbol(raw) {
  const code = raw.toUpperCase();
  const pieces = code.split('/');
  const colors = pieces.filter(p => 'WUBRGC'.includes(p) && p.length === 1);
  const phyrexian = pieces.includes('P');
  const numeric = /^\d+$/.test(pieces[0]) || ['X', 'Y', 'Z', '½', '∞'].includes(pieces[0]);
  let cls = 'ms';
  let label = code;
  if (SYMBOL_TEXT[code]) { cls += ' ms-sym'; label = SYMBOL_TEXT[code]; }
  else if (colors.length === 2) { cls += ' ms-hy'; label = ''; }
  else if (pieces.length === 2 && numeric && colors.length === 1) { cls += ' ms-hy'; label = pieces[0]; }
  else if (colors.length === 1) { cls += ` ms-${colors[0]}`; label = phyrexian ? 'ϕ' : colors[0]; }
  else if (numeric) { cls += ' ms-N'; label = pieces[0]; }
  const node = h('span', { class: cls, title: `{${raw}}`, 'aria-label': `{${raw}}` }, label);
  const [first, second] = colors.length === 2 ? colors : pieces.length === 2 && numeric && colors.length === 1 ? ['N', colors[0]] : [];
  if (first) node.style.background = `linear-gradient(135deg, var(--mana-${first}) 50%, var(--mana-${second}) 50%)`;
  if (colors.length === 2 && phyrexian) node.textContent = 'ϕ';
  return node;
}

export function manaCost(text) {
  return h('span.mana', manaNodes(text));
}

/** Rules text with symbols, one <p> per paragraph. */
export function rulesText(text) {
  const box = h('div.rules');
  for (const line of String(text || '').split('\n')) {
    const p = h('p');
    // italicise reminder text in parentheses
    for (const piece of line.split(/(\([^)]*\))/g)) {
      if (!piece) continue;
      if (/^\(.*\)$/.test(piece)) p.append(h('i.reminder', manaNodes(piece)));
      else p.append(...manaNodes(piece));
    }
    box.append(p);
  }
  return box;
}

// ---------- colors & formats ----------
export const COLOR_NAMES = { W: 'White', U: 'Blue', B: 'Black', R: 'Red', G: 'Green', C: 'Colorless', M: 'Multicolor' };
export const RARITY_ORDER = ['common', 'uncommon', 'rare', 'mythic', 'special', 'bonus'];
export const FORMAT_ORDER = ['standard', 'pioneer', 'modern', 'legacy', 'vintage', 'pauper', 'commander', 'oathbreaker',
  'brawl', 'standardbrawl', 'competitivebrawl', 'historic', 'timeless', 'alchemy', 'explorer', 'penny', 'premodern', 'oldschool',
  'duel', 'predh', 'paupercommander', 'gladiator', 'future', 'redux'];
export const FORMAT_LABELS = { standardbrawl: 'Std Brawl', competitivebrawl: 'Comp Brawl', tlr: 'TLR', paupercommander: 'Pauper EDH', predh: 'PreDH', oldschool: 'Old School', penny: 'Penny', duel: 'Duel Cmdr' };
export const formatLabel = f => FORMAT_LABELS[f] || f.charAt(0).toUpperCase() + f.slice(1);
export function sortFormats(list) {
  return [...list].sort((a, b) => {
    const ia = FORMAT_ORDER.indexOf(a), ib = FORMAT_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
}

// ---------- misc ----------
export function debounce(fn, ms) {
  let timer;
  const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

export const store = {
  get(key, fallback) {
    try { const raw = localStorage.getItem(key); return raw == null ? fallback : JSON.parse(raw); } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode / blocked storage */ }
  },
};

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = h('textarea', { style: { position: 'fixed', opacity: '0' } });
    area.value = text;
    document.body.append(area);
    area.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { ok = false; }
    area.remove();
    return ok;
  }
}

let toastTimer;
export function toast(message) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 2400);
}

export function spinner(label) {
  return h('div.spinner-wrap', { role: 'status' }, h('span.spinner', { 'aria-hidden': 'true' }), label ? h('span', label) : null);
}

export function errorBox(message) {
  return h('div.error-box', { role: 'alert' }, h('b', 'Hmm. '), message);
}

export const finishLabel = f => ({ normal: 'Non-foil', foil: 'Foil', etched: 'Etched', mixed: 'Mixed' }[f] || f);

export function titleCase(s) {
  return String(s || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
