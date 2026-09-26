// The search bar: Scryfall-syntax input with live search, cheat sheet, otag autocomplete,
// Ask (English → query) mode, recent and saved searches.
import { h, $, clear, debounce, store, spinner, int } from './util.js';
import { api } from './api.js';

const EXAMPLES = [
  ['c:b mv<3 o:/destroy target nonland permanent/', 'Cheap black cards that destroy nonland permanents'],
  ['otag:removal f:pauper', 'Removal (Tagger function tag) legal in Pauper'],
  ['id<=esper t:legendary t:creature', 'Legendary creatures an Esper commander deck can run'],
  ['usd>10 -is:foil', 'Non-foil cards worth more than $10'],
  ['qty>=4', 'Cards you hold a playset of'],
  ['added>=2026-01-01', 'Everything added this year'],
  ['gain>5', 'Up more than $5 on what you paid'],
  ['deck:any', 'Everything your active decks use'],
  ['deck:"atraxa"', 'Cards in a deck whose name contains “atraxa”'],
  ['spare>=4', 'Four or more copies free of any active deck'],
  ['-deck:any usd>5', 'Valuable cards sitting outside every deck'],
];
const SYNTAX = [
  ['c:rg', 'colors'], ['id<=esper', 'color identity'], ['t:goblin', 'type line'], ['o:"draw a card"', 'rules text'],
  ['o:/^flying/', 'regex'], ['mv>=5', 'mana value'], ['r:mythic', 'rarity'], ['s:neo', 'set'], ['f:modern', 'legal in format'],
  ['otag:ramp', 'Tagger function tag'], ['usd<1', 'price (usd eur tix)'], ['is:foil', 'finish'], ['qty>=4', 'copies held'],
  ['cond:nm', 'condition'], ['lang:ja', 'language'], ['added>=2025-06', 'date added'], ['paid>2', 'purchase price'],
  ['gain<0', '$ gained per copy'], ['-t:land', 'negate with -'], ['a or b', 'either'], ['( … )', 'grouping'],
  ['order:usd', 'sort'], ['unique:cards', 'one per card'], ['deck:any', 'in an active deck'], ['deck:"name"', 'in a named deck'],
  ['used>0', 'copies in active decks'], ['spare>=1', 'copies not in decks'],
];
const RECENT_KEY = 'gallery.recent', SAVED_KEY = 'gallery.saved';

let handlers;
let input, form, info, askButton, searchModeButton, chipRow, cheat, suggest, askStatus;
let mode = 'search';
let buffers = { search: '', ask: '' };
let askExplanation = null;
let lastResponse = null;
let askController = null;

export function initSearchBar(options) {
  handlers = options;
  const root = $('#searchbar');
  input = h('input#q.search-input', {
    type: 'search', autocomplete: 'off', spellcheck: 'false', 'aria-label': 'Search your collection (Scryfall syntax)',
    placeholder: 'Search your cards… try  t:dragon usd>2',
  });
  searchModeButton = h('button.seg-btn.on', { type: 'button', 'aria-pressed': 'true', onclick: () => setMode('search') }, 'Search');
  askButton = h('button.seg-btn', { type: 'button', 'aria-pressed': 'false', title: 'Ask in plain English; Claude writes the query', onclick: () => setMode('ask') }, 'Ask ✶');
  suggest = h('ul.suggest', { role: 'listbox', hidden: true });
  cheat = buildCheatSheet();
  const help = h('button.icon-btn', { type: 'button', 'aria-label': 'Search syntax help', 'aria-expanded': 'false', title: 'Syntax cheat sheet',
    onclick: e => { e.stopPropagation(); toggleCheat(); } }, '?');
  const save = h('button.icon-btn.star', { type: 'button', 'aria-label': 'Save this search', title: 'Save this search', onclick: toggleSaved }, '★');
  form = h('form.search-form', { role: 'search', onsubmit: submit },
    h('div.seg.mode', { role: 'group', 'aria-label': 'Search mode' }, searchModeButton, askButton),
    h('div.input-wrap', input, suggest),
    help, save,
    h('button.btn.go', { type: 'submit' }, 'Go'));
  chipRow = h('div.chip-row', { 'aria-label': 'Saved and recent searches' });
  askStatus = h('div.ask-status', { hidden: true });
  info = h('div.query-info', { 'aria-live': 'polite' });
  root.append(form, cheat, askStatus, info, chipRow);

  const live = debounce(() => handlers.onInput(input.value), 380);
  input.addEventListener('input', () => {
    if (mode === 'ask') { buffers.ask = input.value; return; }
    buffers.search = input.value;
    askExplanation = null;
    live();
    autocomplete();
  });
  input.addEventListener('keydown', suggestKeys);
  input.addEventListener('blur', () => {
    setTimeout(() => { suggest.hidden = true; }, 150);
    // A live search the user moved on from counts as "recent" (only if it parsed).
    if (mode === 'search' && input.value.trim() && lastResponse && !lastResponse.error && (lastResponse.query?.text || '').trim() === input.value.trim()) remember(input.value);
  });
  document.addEventListener('click', event => {
    if (!cheat.hidden && !cheat.contains(event.target)) toggleCheat(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === '/' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA' && !$('#modal:not([hidden])')) {
      event.preventDefault();
      input.focus();
    }
    if (event.key === 'Escape' && !cheat.hidden) toggleCheat(false);
  });
  renderChips();
}

export function setInput(q) {
  buffers.search = q;
  if (mode === 'search' && input.value !== q) input.value = q;
}

function setMode(next) {
  if (mode === next) return;
  if (mode === 'ask' && askController) return; // wait for the pending question
  mode = next;
  searchModeButton.classList.toggle('on', mode === 'search');
  askButton.classList.toggle('on', mode === 'ask');
  searchModeButton.setAttribute('aria-pressed', mode === 'search');
  askButton.setAttribute('aria-pressed', mode === 'ask');
  form.classList.toggle('asking', mode === 'ask');
  input.value = buffers[mode];
  input.placeholder = mode === 'ask' ? 'Ask in English… e.g. “green creatures that make treasure, legal in pauper”' : 'Search your cards… try  t:dragon usd>2';
  input.setAttribute('aria-label', mode === 'ask' ? 'Ask a question in English' : 'Search your collection (Scryfall syntax)');
  suggest.hidden = true;
  input.focus();
}

function submit(event) {
  event.preventDefault();
  suggest.hidden = true;
  if (mode === 'ask') return ask(input.value.trim());
  handlers.onSubmit(input.value);
  remember(input.value);
}

async function ask(question) {
  if (!question || askController) return;
  askController = new AbortController();
  const started = Date.now();
  input.disabled = true;
  const elapsed = h('span.elapsed', '0 s');
  clear(askStatus).append(
    h('div.asking-box', spinner(), h('div', h('b', 'Asking Claude to write the query… '), elapsed, h('div.muted', 'Usually 5–20 seconds.')),
      h('button.btn.small', { type: 'button', onclick: () => askController?.abort() }, 'Cancel')));
  askStatus.hidden = false;
  const timer = setInterval(() => { elapsed.textContent = Math.round((Date.now() - started) / 1000) + ' s'; }, 500);
  try {
    const result = await api.ask(question, askController.signal);
    if (result.error || !result.query) throw new Error(result.error || 'No query came back.');
    askExplanation = { question, explanation: result.explanation || '' };
    buffers.ask = '';
    askController = null;
    setMode('search');
    setInput(result.query);
    input.value = result.query;
    remember(result.query);
    handlers.onSubmit(result.query);
    askStatus.hidden = true;
  } catch (error) {
    if (error.name === 'AbortError') askStatus.hidden = true;
    else clear(askStatus).append(h('div.error-box', { role: 'alert' }, h('b', 'Ask failed. '), error.message,
      ' ', h('button.link-btn', { type: 'button', onclick: () => { askStatus.hidden = true; } }, 'Dismiss')));
  } finally {
    clearInterval(timer);
    askController = null;
    input.disabled = false;
    input.focus();
  }
}

/** Show description / warnings / parse error for the latest search response. */
export function showQueryInfo(response) {
  lastResponse = response;
  clear(info);
  if (askExplanation) {
    info.append(h('div.ask-note', h('span.tag-label', 'Asked'), h('q', askExplanation.question), ' ', askExplanation.explanation));
  }
  if (!response) return;
  if (response.error) {
    info.append(h('div.error-box', { role: 'alert' }, h('b', 'That query didn’t parse. '), response.error));
    return;
  }
  const q = response.query || {};
  if (q.description && (q.text || '').trim()) info.append(h('div.desc', h('span.tag-label', 'Showing'), q.description));
  for (const warning of q.warnings || []) info.append(h('div.warn', '⚠ ', warning));
}

// ---------- cheat sheet ----------
function buildCheatSheet() {
  return h('div.cheat.panel', { hidden: true, role: 'dialog', 'aria-label': 'Search syntax cheat sheet' },
    h('div.cheat-head', h('h3', 'Search syntax'), h('a', { href: 'https://scryfall.com/docs/syntax', target: '_blank', rel: 'noopener' }, 'Full Scryfall reference ↗')),
    h('div.cheat-examples', EXAMPLES.map(([query, text]) => h('button.example', { type: 'button', onclick: () => {
      toggleCheat(false); setMode('search'); askExplanation = null; setInput(query); input.value = query; remember(query); handlers.onSubmit(query);
    } }, h('code', query), h('span', text)))),
    h('div.cheat-grid', SYNTAX.map(([code, text]) => h('div', h('code', code), ' ', h('span.muted', text)))),
    h('p.muted.small', 'Press / anywhere to jump to the search box. Type otag: for tag suggestions.'));
}

function toggleCheat(force) {
  cheat.hidden = force === undefined ? !cheat.hidden : !force;
  $('.icon-btn[aria-label="Search syntax help"]').setAttribute('aria-expanded', String(!cheat.hidden));
}

// ---------- otag autocomplete ----------
let suggestController = null;
let suggestItems = [];
let suggestIndex = -1;
const fetchTags = debounce(async (partial, token) => {
  suggestController?.abort();
  suggestController = new AbortController();
  try {
    const tags = await api.tags(partial, suggestController.signal);
    showSuggestions(tags, token);
  } catch (error) { if (error.name !== 'AbortError') suggest.hidden = true; }
}, 150);

function currentToken() {
  const before = input.value.slice(0, input.selectionStart ?? input.value.length);
  const match = before.match(/(^|[\s(])(-?otag[:=])([\w-]*)$/i);
  return match ? { start: before.length - match[3].length, partial: match[3] } : null;
}

function autocomplete() {
  const token = currentToken();
  if (!token) { suggest.hidden = true; return; }
  fetchTags(token.partial, token);
}

function showSuggestions(tags, token) {
  suggestItems = tags.slice(0, 12);
  suggestIndex = -1;
  clear(suggest);
  if (!suggestItems.length || mode !== 'search') { suggest.hidden = true; return; }
  suggestItems.forEach((tag, i) => suggest.append(h('li', { role: 'option', dataset: { i },
    onmousedown: e => { e.preventDefault(); accept(i, token); } },
    h('code', tag.slug), h('span.count', int(tag.count)), tag.description ? h('span.desc', tag.description) : null)));
  suggest.hidden = false;
  suggest.token = token;
}

function accept(i, token = suggest.token) {
  const tag = suggestItems[i];
  if (!tag || !token) return;
  const value = input.value;
  const endOfToken = value.slice(token.start).search(/[\s)]|$/) + token.start;
  input.value = value.slice(0, token.start) + tag.slug + ' ' + value.slice(endOfToken).replace(/^\s+/, '');
  const caret = token.start + tag.slug.length + 1;
  input.setSelectionRange(caret, caret);
  suggest.hidden = true;
  buffers.search = input.value;
  handlers.onInput(input.value);
}

function suggestKeys(event) {
  if (suggest.hidden) return;
  const items = [...suggest.children];
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    suggestIndex = (suggestIndex + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items.forEach((li, i) => li.classList.toggle('on', i === suggestIndex));
    items[suggestIndex].scrollIntoView({ block: 'nearest' });
  } else if ((event.key === 'Enter' || event.key === 'Tab') && suggestIndex >= 0) {
    event.preventDefault();
    accept(suggestIndex);
  } else if (event.key === 'Tab' && items.length) {
    event.preventDefault();
    accept(0);
  } else if (event.key === 'Escape') {
    suggest.hidden = true;
  }
}

// ---------- recent & saved ----------
export function remember(q) {
  q = (q || '').trim();
  if (!q) return;
  const recent = store.get(RECENT_KEY, []).filter(r => r !== q);
  recent.unshift(q);
  store.set(RECENT_KEY, recent.slice(0, 10));
  renderChips();
}

function toggleSaved() {
  const q = (buffers.search || '').trim();
  if (!q) return;
  let saved = store.get(SAVED_KEY, []);
  saved = saved.includes(q) ? saved.filter(s => s !== q) : [q, ...saved];
  store.set(SAVED_KEY, saved.slice(0, 30));
  renderChips();
}

export function renderChips() {
  if (!chipRow) return;
  const saved = store.get(SAVED_KEY, []);
  const recent = store.get(RECENT_KEY, []).filter(r => !saved.includes(r)).slice(0, 6);
  const current = (buffers.search || '').trim();
  $('.icon-btn.star')?.classList.toggle('on', saved.includes(current));
  clear(chipRow);
  const run = q => { setMode('search'); setInput(q); input.value = q; askExplanation = null; handlers.onSubmit(q); };
  for (const q of saved) {
    chipRow.append(h('span.qchip.saved', h('button', { type: 'button', title: 'Run saved search', onclick: () => run(q) }, '★ ', q),
      h('button.x', { type: 'button', 'aria-label': `Remove saved search ${q}`, onclick: () => {
        store.set(SAVED_KEY, store.get(SAVED_KEY, []).filter(s => s !== q)); renderChips();
      } }, '×')));
  }
  for (const q of recent) chipRow.append(h('span.qchip', h('button', { type: 'button', title: 'Run recent search', onclick: () => run(q) }, '↺ ', q)));
  chipRow.hidden = !chipRow.childElementCount;
}

export function clearAskExplanation() { askExplanation = null; showQueryInfo(lastResponse); }
