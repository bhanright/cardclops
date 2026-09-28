// Entry point: routing (#/tab?params), gallery state, wiring between views.
import { $, $$, h, int, money } from './js/util.js';
import { api } from './js/api.js';
import { initSearchBar, setInput, remember } from './js/search.js';
import { initGallery, updateGallery } from './js/gallery.js';
import { setDetailHooks, closeCard } from './js/detail.js';
import { initDashboard, showDashboard } from './js/dashboard.js';
import { showDeckCheck } from './js/deckcheck.js';
import { showExtras } from './js/extras.js';
import { showDecks } from './js/decks.js';
import { showRadar } from './js/radar.js';
import { showBuild } from './js/build.js';
import { showAlerts, initBell } from './js/alerts.js';
import { showSets } from './js/sets.js';
import { openHandAdded } from './js/addcards.js';
import { showSettings, applySettings } from './js/settings.js';
import { showBinders } from './js/binders.js';
import { showRules } from './js/rules.js';
import { runSetupIfNeeded, checkBackgroundJob, updateCollection, refreshNow, quitApp } from './js/setup.js';

const TABS = ['gallery', 'decks', 'sets', 'build', 'dashboard', 'radar', 'deck', 'extras', 'alerts', 'settings', 'binders', 'rules'];
const TOOL_TABS = { radar: 'Radar', deck: 'Deck check', extras: 'Extras', alerts: 'Alerts', settings: 'Settings', binders: 'Binders', rules: 'Rules' };   // live under the Tools menu
const DEFAULTS = { q: '', sort: 'name', dir: 'asc', unique: 'prints', view: 'grid' };
const VALID = {
  sort: ['name', 'usd', 'value', 'mv', 'added', 'set', 'rarity', 'qty', 'gain', 'change1', 'change7', 'change30', 'color', 'released'],
  dir: ['asc', 'desc'], unique: ['prints', 'cards'], view: ['grid', 'list'],
};

let tab = 'gallery';
let state = { ...DEFAULTS };
let deckId = null;          // #/decks/<id> shows one deck
let buildId = null;         // #/build/<oracle_id>?partner=<oracle_id> shows a draft
let partnerId = null;
let setCode = null;          // #/sets/<code> shows one set
let galleryStale = false;    // a deck or collection change since the gallery last searched

function readHash() {
  const raw = decodeURIComponent(location.hash.replace(/^#\/?/, '').split('?')[0]);
  const params = new URLSearchParams(location.hash.split('?').slice(1).join('?'));
  const deckMatch = raw.match(/^decks\/(\d+)$/);
  deckId = deckMatch ? deckMatch[1] : null;
  const buildMatch = raw.match(/^build\/([0-9a-f-]{8,})$/i);
  buildId = buildMatch ? buildMatch[1] : null;
  partnerId = buildMatch ? params.get('partner') : null;
  const setMatch = raw.match(/^sets\/([a-z0-9]+)$/i);
  setCode = setMatch ? setMatch[1].toLowerCase() : null;
  tab = deckMatch ? 'decks' : buildMatch ? 'build' : setMatch ? 'sets' : TABS.includes(raw) ? raw : 'gallery';
  if (tab === 'gallery') {
    const next = { ...DEFAULTS, q: params.get('q') ?? '' };
    for (const key of ['sort', 'dir', 'unique', 'view']) if (VALID[key].includes(params.get(key))) next[key] = params.get(key);
    state = next;
  }
}

function galleryHash(s = state) {
  const params = new URLSearchParams();
  for (const key of Object.keys(DEFAULTS)) if (s[key] !== DEFAULTS[key]) params.set(key, s[key]);
  const query = params.toString().replace(/\+/g, '%20');
  return '#/gallery' + (query ? '?' + query : '');
}

function writeHash(push) {
  const target = tab === 'gallery' ? galleryHash() : tab === 'decks' && deckId ? `#/decks/${deckId}`
    : tab === 'sets' && setCode ? `#/sets/${setCode}`
    : tab === 'build' && buildId ? `#/build/${buildId}${partnerId ? '?partner=' + partnerId : ''}` : '#/' + tab;
  if (location.hash === target) return;
  if (push) history.pushState(null, '', target);
  else history.replaceState(null, '', target);
  syncTabs();
}

function setState(partial, { push = false, record = false } = {}) {
  state = { ...state, ...partial };
  if (tab !== 'gallery') { tab = 'gallery'; deckId = null; buildId = null; showTab(); push = true; }
  writeHash(push);
  setInput(state.q);
  if (record) remember(state.q);
  showGallery();
}

function syncTabs() {
  for (const link of $$('.tabs a')) {
    const name = link.dataset.tab;
    link.href = name === 'gallery' ? galleryHash() : '#/' + name;
    if (name === tab) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }
  const toolsBtn = $('#toolsBtn');
  const inTools = tab in TOOL_TABS;
  toolsBtn.classList.toggle('on', inTools);
  // On a phone the tab row has no room for a longer name ("Settings", "Deck check"), which would
  // wrap the button onto a row of its own; it stays "Tools", highlighted.
  const roomy = !matchMedia('(max-width: 700px)').matches;
  toolsBtn.firstChild.textContent = inTools && roomy ? `${TOOL_TABS[tab]} ` : 'Tools ';
  toolsBtn.setAttribute('aria-label', inTools ? `Tools menu, showing ${TOOL_TABS[tab]}` : 'Tools menu');
}

/** Tools disclosure menu: the list is position:fixed so the scrolling nav on phones can't clip it. */
function initToolsMenu() {
  const button = $('#toolsBtn'), list = $('#toolsList');
  const links = () => [...list.querySelectorAll('a, button:not([hidden])')];
  const place = () => {
    const r = button.getBoundingClientRect();
    const width = Math.min(260, innerWidth - 16);
    list.style.top = `${r.bottom + 8}px`;
    // Fixed to the screen, so it can't scroll with the page: in landscape on a phone it would run
    // off the bottom. It gets the height that's there and scrolls inside that.
    list.style.maxHeight = `${Math.max(160, innerHeight - r.bottom - 16)}px`;
    list.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, r.right - width))}px`;
    list.style.width = `${width}px`;
  };
  const open = focusFirst => {
    list.hidden = false; button.setAttribute('aria-expanded', 'true'); place();
    if (focusFirst) (links().find(a => a.getAttribute('aria-current')) || links()[0]).focus();
  };
  const close = refocus => { if (list.hidden) return; list.hidden = true; button.setAttribute('aria-expanded', 'false'); if (refocus) button.focus(); };
  button.addEventListener('click', () => (list.hidden ? open(false) : close(false)));
  button.addEventListener('keydown', e => { if (e.key === 'ArrowDown') { e.preventDefault(); open(true); } });
  list.addEventListener('keydown', e => {
    const items = links(), i = items.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); close(true); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  });
  list.addEventListener('click', e => { if (e.target.closest('a, button')) close(false); });
  $('#toolUpdate').addEventListener('click', updateCollection);
  $('#toolAdd').addEventListener('click', openHandAdded);
  $('#toolRefresh').addEventListener('click', refreshNow);
  $('#toolQuit').addEventListener('click', quitApp);
  document.addEventListener('click', e => { if (!e.target.closest('.tools-menu')) close(false); });
  document.addEventListener('focusin', e => { if (!list.hidden && !e.target.closest('.tools-menu')) close(false); });
  addEventListener('resize', () => { if (!list.hidden) place(); });
  addEventListener('scroll', () => { if (!list.hidden) place(); }, { passive: true });
}

let previousTab = null;
function showTab() {
  for (const name of TABS) $('#view-' + name).hidden = name !== tab;
  document.body.dataset.tab = tab;
  syncTabs();
  const place = tab + (tab === 'decks' ? '/' + (deckId || '') : '') + (tab === 'build' ? '/' + (buildId || '') : '') + (tab === 'sets' ? '/' + (setCode || '') : '');
  if (previousTab && previousTab !== place) scrollTo({ top: 0 });
  previousTab = place;
  if (tab === 'dashboard') showDashboard();
  if (tab === 'deck') showDeckCheck();
  if (tab === 'extras') showExtras();
  if (tab === 'decks') showDecks(deckId);
  if (tab === 'radar') showRadar();
  if (tab === 'build') showBuild(buildId, partnerId);
  if (tab === 'alerts') showAlerts();
  if (tab === 'sets') showSets(setCode);
  if (tab === 'settings') showSettings();
  if (tab === 'binders') showBinders();
  if (tab === 'rules') showRules();
  $('#alertBell').toggleAttribute('aria-current', tab === 'alerts');
  const titles = { gallery: 'Gallery', decks: 'Decks', sets: 'Sets', build: 'What can I build?', dashboard: 'Dashboard', radar: 'Reprint radar', alerts: 'Price alerts', deck: 'Deck check', extras: 'Trade binder', settings: 'Settings', binders: 'Binders', rules: 'Comprehensive Rules' };
  document.title = `${titles[tab]} · Cardclops`;
}

function route() {
  closeCard();
  readHash();
  showTab();
  if (tab === 'gallery') {
    setInput(state.q);
    showGallery();
  }
}

/** Search again if the query changed, or if a deck or the collection changed since the last search. */
function showGallery() {
  updateGallery(state, { force: galleryStale });
  galleryStale = false;
}

const searchFromElsewhere = (q, extra = {}) => setState({ ...DEFAULTS, view: state.view, q, ...extra }, { push: true, record: true });

async function loadHeaderSummary() {
  const box = $('#headerStats');
  try {
    const s = await api.summary();
    $('#toolQuit').hidden = !s.app?.installed;
    // The Ask box needs the Claude command-line tool, which can't run on a phone.
    const askMode = $('.search-form .seg.mode');
    if (askMode) askMode.hidden = s.app?.platform === 'android';
    box.replaceChildren(
      h('span.hs', h('b', money(s.value_usd, { whole: true })), ' value'),
      h('span.hs', h('b', int(s.copies)), ' copies'),
      h('span.hs.hide-sm', h('b', int(s.unique_cards)), ' cards'));
  } catch {
    box.replaceChildren(h('span.hs.muted', 'offline?'));
  }
}

async function init() {
  applySettings();                // the inline script in index.html did most of this before paint
  // Sticky things below the header (deck section links, the job banner) sit just under it; its
  // height changes with the width, wrapping tabs and the text size, so it is measured.
  const header = document.querySelector('.topbar');
  new ResizeObserver(() => document.documentElement.style.setProperty('--header-h', `${header.offsetHeight}px`)).observe(header);
  // A fresh install has no card data: the setup wizard comes first, before any view asks for data.
  await runSetupIfNeeded();
  initSearchBar({
    onInput: q => setState({ q }),
    onSubmit: q => setState({ q }, { push: true }),
  });
  initGallery({ getState: () => state, setState });
  setDetailHooks({ search: searchFromElsewhere });
  initDashboard({ search: searchFromElsewhere });
  addEventListener('hashchange', route);
  // Cards added or edited by hand: refresh whatever view is open once the server has reloaded.
  // The gallery's results (spare counts, "in decks") also go stale when a deck changes; it
  // searches again the next time it is shown.
  addEventListener('cardclops:collection-changed', () => {
    loadHeaderSummary();
    galleryStale = true;
    if (tab === 'gallery') showGallery();
    else if (!document.querySelector('.dialog')) route();
  });
  addEventListener('cardclops:decks-changed', () => { galleryStale = true; });
  addEventListener('cardclops:binders-changed', () => { galleryStale = true; });
  addEventListener('popstate', route);
  initToolsMenu();
  route();
  loadHeaderSummary();
  initBell();
  checkBackgroundJob();
}

init();
