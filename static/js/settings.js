// Settings (#/settings): how Cardclops looks on this device (theme, text size, motion, foil
// shimmer, hover previews), and the app's data and updates. Appearance is saved in this
// browser only; index.html applies it before the first paint so a chosen theme never flashes.
import { h, clear, int, spinner, errorBox, toast, store } from './util.js';
import { api } from './api.js';
import { refreshNow, updateCollection } from './setup.js';
import { openHandAdded } from './addcards.js';
import { browserEdition, askKey, setAskKey } from './edition.js';
import { backupPanel } from './backup.js';

export const SETTINGS_KEY = 'gallery.settings';     // index.html reads the same key
const DEFAULTS = { theme: 'neon', textScale: 100, motion: 'system', foil: true, hoverPreview: true };
// The top row is the default and the two plain themes; "More themes" holds every other palette
// (future ones included). Neon, Light and Dark are drawn in styles.css; every other theme is in
// themes.css, each under its own :root[data-theme].
const THEMES = [
  { id: 'neon', name: 'Neon', note: 'The original: saturated color on deep violet', swatch: ['#140a24', '#ff3fa4', '#b8ff3c', '#2ee6ff'] },
  { id: 'light', name: 'Light', note: 'Warm paper and deeper inks', swatch: ['#f6f0e4', '#e0237f', '#5b9e00', '#0a8aab'], light: true },
  { id: 'dark', name: 'Dark', note: 'Softer colors on charcoal', swatch: ['#131317', '#f06aa6', '#b7e06e', '#72d0e6'] },
];
const MORE_THEMES = [
  { id: 'lollipop', name: 'Lollipop', note: 'A candy shop: a striped awning, gumdrop tabs, sprinkles and rising bubbles', swatch: ['#ffd9ec', '#ec1a78', '#459600', '#0b8fd0'], light: true },
  { id: 'astronaut', name: 'Astronaut', note: 'Orbit at night: a twinkling starfield, Earth’s edge, HUD brackets and a mission patch', swatch: ['#070b17', '#ff6b2c', '#4fd3ff', '#f4c542'] },
  { id: 'necronomicon', name: 'Necronomicon', note: 'Bound in something it shouldn’t be: stitched leather, a ritual circle, candlelight and blood', swatch: ['#110807', '#e0314a', '#9ad14a', '#d8b25c'] },
  { id: 'stovepipe', name: 'Stovepipe', note: 'A cast-iron wood stove: brick, riveted iron, brass nameplates and drifting embers', swatch: ['#12100e', '#f08a2b', '#d9aa4a', '#7fb3c9'] },
  { id: 'gamma', name: 'Gamma', note: 'A lab accident: hazard tape, warning labels, the trefoil and a pulsing green glow', swatch: ['#07110b', '#7dff3a', '#ffe23d', '#b077ff'] },
  { id: 'galactus', name: 'Galactus', note: 'The Devourer of Worlds: cosmic crackle, a consumed planet, caption boxes and halftone panels', swatch: ['#0a0720', '#a77bff', '#ff4fb8', '#ffd23f'] },
  { id: 'unicorn', name: 'Unicorn', note: 'A pastel sky: clouds, a rainbow arc, rainbow-ringed buttons and twinkling sparkles', swatch: ['#f4ecff', '#e2449c', '#9d62e6', '#1c8fc4'], light: true },
  { id: 'lemonhead', name: 'Lemonhead', note: 'The candy box: lemon slices, sugar, a scalloped red lid, polka dots and a sunburst', swatch: ['#fff5b3', '#e0312c', '#4f9a00', '#a88000'], light: true },
  { id: 'olympic', name: 'Olympic', note: 'Race day: a running track, the five-color ribbon, race-bib tabs and a gold medal', swatch: ['#ffffff', '#0085c7', '#a88400', '#009f3d', '#e8173a'], light: true },
  { id: 'spqr', name: 'SPQR', note: 'Imperial Rome: veined marble, Ionic columns, a gold meander, stone tablets and laurels', swatch: ['#f1ebe0', '#a855c0', '#e03a50', '#a88418'], light: true },
  { id: 'sasquatch', name: 'Sasquatch', note: 'A Pacific Northwest forest at dusk: pines, fog, fireflies and carved trail signs', swatch: ['#0d130e', '#c9793c', '#8cc063', '#e0bd52'] },
  { id: 'win95', name: 'Windows 95', note: 'The 1995 desktop: the taskbar and Start button, beveled windows with title bars, the Start menu', swatch: ['#008080', '#c0c0c0', '#000080', '#ffffff'], light: true },
  { id: 'mac8', name: 'Mac OS 8', note: 'Platinum: the menu bar, pinstriped windows with close boxes, Balloon Help and the barber pole', swatch: ['#66669a', '#dddddd', '#333399', '#ffffff'], light: true },
  { id: 'mac27', name: 'macOS 27', note: 'Liquid Glass: frosted panels, a floating dock, the window lights and a drifting aurora', swatch: ['#6f8cff', '#b88cff', '#ff9cc2', '#ffffff'], light: true },
  { id: 'xp', name: 'Windows XP', note: 'Luna: the green Start button, blue title bars, the Start menu and clouds over a green hill', swatch: ['#2f6fd8', '#0054e3', '#3c9b3c', '#ece9d8'], light: true },
  { id: 'win8', name: 'Windows 8', note: 'Metro: the Start screen’s purple and its tattoo, lowercase pivots and live tiles', swatch: ['#1b0b3a', '#2d89ef', '#00b7b5', '#ff3db8'] },
  { id: 'ps2', name: 'PlayStation 2', note: 'The boot screen: towers of blue light, drifting crystal orbs, glowing capitals and the controller’s symbols', swatch: ['#02040c', '#7ab4ff', '#3ee6a0', '#ff8ad8'] },
  { id: 'n64', name: 'Nintendo 64', note: 'Charcoal plastic: cartridge tabs, the four-color stripe, C-buttons and drifting low-poly gems', swatch: ['#1c1c22', '#ff4d4d', '#33d65a', '#5b8cff', '#ffd21a'] },
  { id: 'ps3', name: 'PlayStation 3', note: 'The XMB: the rolling white wave, the category row, piano black and a trophy for every message', swatch: ['#0d0f14', '#1c2230', '#ffffff', '#6fb0ff'] },
  { id: 'ps4', name: 'PlayStation 4', note: 'The home screen: PlayStation blue, drifting bokeh, tile tabs and white focus rings', swatch: ['#04163d', '#0070d1', '#5fa8ff', '#ffffff'] },
  { id: 'genesis', name: 'Sega Genesis', note: 'Sixteen bits: a scrolling starfield over a grid floor, chrome headings, speed stripes and a gold ring', swatch: ['#050506', '#ff3b3b', '#ffd23a', '#3b8cff'] },
  { id: 'gbc', name: 'Game Boy Color', note: 'A teal shell: LCD panels in the screen bezel, a power light, pixel hearts and a ▶ menu cursor', swatch: ['#1fa3b4', '#6f58a8', '#eef3e2', '#e0237f'], light: true },
  { id: 'coleco', name: 'ColecoVision', note: 'Black and brushed aluminum: numbered keypad tabs, warm stripes and a waggling joystick', swatch: ['#0c0c0d', '#c6c6c8', '#ff8a1a', '#ffc23a'] },
  { id: 'cyberpunk', name: 'Cyberpunk', note: 'A city at night: rain, neon windows, glitching headings, cut corners and hazard stripes', swatch: ['#050508', '#fcee0a', '#ff2a6d', '#00f0ff'] },
  { id: 'hokkaido', name: 'Hokkaido', note: 'Winter in the north: falling snow, indigo waves, noren tabs, washi paper and vermilion seals', swatch: ['#e3ecf5', '#1f3a5f', '#c8372d', '#fbf8f1'], light: true },
];
const LIGHT_THEMES = new Set([...THEMES, ...MORE_THEMES].filter(t => t.light).map(t => t.id));
const SIZES = [[90, 'Smaller'], [100, 'Normal'], [112.5, 'Larger'], [125, 'Largest']];
const lightQuery = matchMedia('(prefers-color-scheme: light)');

export function settings() {
  return { ...DEFAULTS, ...store.get(SETTINGS_KEY, {}) };
}

/** Put the settings on the page: the theme and the flags the stylesheet reads from <html>. */
export function applySettings(s = settings()) {
  const root = document.documentElement;
  const theme = s.theme === 'auto' ? (lightQuery.matches ? 'light' : 'dark') : s.theme;
  root.dataset.theme = theme;
  root.style.fontSize = s.textScale === 100 ? '' : `${s.textScale}%`;
  if (s.motion === 'reduce') root.dataset.motion = 'reduce'; else delete root.dataset.motion;
  if (s.foil === false) root.dataset.foil = 'off'; else delete root.dataset.foil;
  const light = LIGHT_THEMES.has(theme);
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', light ? 'light' : 'dark');
  const style = getComputedStyle(root);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', style.getPropertyValue('--bg').trim());
  // The Android app colors the phone's status and navigation bars to match (MainActivity.ThemeBridge).
  window.CardclopsAndroid?.setBars(style.getPropertyValue('--bg-deep').trim(), style.getPropertyValue('--bg').trim(), light);
}
lightQuery.addEventListener('change', () => { if (settings().theme === 'auto') applySettings(); });

function change(fields) {
  const next = { ...settings(), ...fields };
  store.set(SETTINGS_KEY, next);
  applySettings(next);
  return next;
}

const row = (label, hint, ...control) => h('div.pref-row', h('div.pref-label', h('b', label), hint ? h('span', hint) : null), h('div.pref-control', ...control));

/** A row of choices, one pressed; picking one saves it and redraws the row. */
function choices(key, options, current) {
  const box = h('div.seg', { role: 'group' });
  const draw = value => clear(box).append(...options.map(([v, label]) => h('button.seg-btn', {
    type: 'button', class: v === value ? 'on' : null, 'aria-pressed': String(v === value),
    onclick: () => { change({ [key]: v }); draw(v); } }, label)));
  draw(current);
  return box;
}

function toggle(key, label, current) {
  const box = h('input', { type: 'checkbox', checked: current, onchange: () => change({ [key]: box.checked }) });
  return h('label.inline-label.check', box, ' ', label);
}

/** Theme cards in two groups, plus the "match my device" switch between Light and Dark. */
function themeChooser(current) {
  const main = h('div.theme-picks', { role: 'group', 'aria-label': 'Themes' });
  const more = h('div.theme-picks', { role: 'group', 'aria-label': 'More themes' });
  const follow = h('input', { type: 'checkbox' });
  const card = (t, value) => h('button.theme-pick', {
    type: 'button', 'aria-pressed': String(t.id === value), title: t.note,
    onclick: () => { change({ theme: t.id }); draw(t.id); } },
  h('span.theme-swatch', { 'aria-hidden': 'true' }, t.swatch.map(color => h('span', { style: { background: color } }))),
  h('b', t.name), h('span.small.muted', t.note));
  const draw = theme => {
    // With "match my device" on, the card for whichever of Light and Dark is showing is pressed.
    const shown = theme === 'auto' ? (lightQuery.matches ? 'light' : 'dark') : theme;
    clear(main).append(...THEMES.map(t => card(t, shown)));
    clear(more).append(...MORE_THEMES.map(t => card(t, shown)));
    follow.checked = theme === 'auto';
  };
  follow.addEventListener('change', () => {
    const theme = follow.checked ? 'auto' : (lightQuery.matches ? 'light' : 'dark');
    change({ theme });
    draw(theme);
  });
  draw(current);
  return h('div.theme-chooser', main,
    h('label.inline-label.check.follow-device', follow, ' Match my device: Light when it’s in light mode, Dark when it’s in dark mode'),
    h('h3.more-themes', 'More themes'), more);
}

export async function showSettings() {
  const host = document.getElementById('view-settings');
  const s = settings();
  const canHover = matchMedia('(hover: hover) and (pointer: fine)').matches;
  const dataBox = h('div', spinner('Reading the app’s data…'));
  const backupHost = h('div');
  const portHost = h('div');
  clear(host).append(h('div.settings-page',
    h('section.panel',
      h('div.panel-head', h('h2', 'Appearance'), h('span.muted.small', 'Saved on this device')),
      row('Theme', 'Colors for the whole app', themeChooser(s.theme)),
      row('Text size', 'Scales text and most spacing', choices('textScale', SIZES, s.textScale)),
      row('Motion', 'Wobbling logo, ringing bell, sliding cards', choices('motion', [['system', 'Follow my device'], ['reduce', 'Reduce']], s.motion)),
      row('Foil shimmer', 'The rainbow sheen on foil cards', toggle('foil', 'Show it', s.foil !== false)),
      canHover ? row('Card previews', 'A large image when the pointer rests on a card name', toggle('hoverPreview', 'Show them', s.hoverPreview !== false)) : null),
    h('section.panel', h('div.panel-head', h('h2', 'Data and updates')), dataBox),
    backupHost,
    portHost,
    browserEdition ? askPanel() : null,
    browserEdition ? editionPanel() : null));
  document.title = 'Settings · Cardclops';

  let status, summary;
  try { [status, summary] = await Promise.all([api.setup.status(), api.summary()]); }
  catch (error) { clear(dataBox).append(errorBox(error.message)); return; }
  backupHost.replaceWith(backupPanel(summary));
  if (!browserEdition) api.app.port().then(p => { if (p.available) portHost.replaceWith(portPanel(p)); }).catch(() => {});
  const daily = status.platform === 'win32'
    ? (() => {
      const box = h('input', { type: 'checkbox', checked: !!status.daily_refresh_scheduled, onchange: async () => {
        box.disabled = true;
        try {
          const r = await api.setup.options({ daily_refresh: box.checked });
          box.checked = !!r.daily_refresh_scheduled;
          toast(box.checked ? 'Prices will update every morning' : 'Daily update turned off');
        } catch (error) { box.checked = !box.checked; toast('Could not change it: ' + error.message); }
        box.disabled = false;
      } });
      return h('label.inline-label.check', box, ' Update every morning at 7:30 (a Windows scheduled task, while you’re signed in)');
    })()
    : h('span', status.platform === 'android'
      ? 'When you open the app on Wi-Fi, if the data is more than a day old.'
      : browserEdition ? 'When you open Cardclops, if the card data is more than a day old.'
      : 'Automatically, once a day.');
  clear(dataBox).append(
    h('dl.pref-facts',
      h('dt', 'Version'), h('dd', `Cardclops ${status.version}`),
      h('dt', 'Card data'), h('dd', status.card_data_date ? `Scryfall and prices from ${status.card_data_date}` : 'Not downloaded yet'),
      h('dt', 'Collection'), h('dd', `${int(summary.copies)} copies of ${int(summary.unique_cards)} cards`),
      ...(browserEdition ? [
        h('dt', 'Your data'), h('dd', 'In this browser, on this device', h('span.muted.small', ' — clearing this site’s data in the browser deletes it'))] : [
        h('dt', 'Your data'), h('dd', h('code', status.data_dir), h('span.muted.small', ' — collection, decks, backups')),
        h('dt', 'Card cache'), h('dd', h('code', status.cache_dir), h('span.muted.small', ' — safe to delete; it downloads again'))])),
    row('Price updates', null, daily),
    row('Housekeeping', null, h('div.form-row',
      h('button.btn.small', { type: 'button', onclick: refreshNow }, '⟳ Refresh card data now'),
      h('button.btn.small', { type: 'button', onclick: updateCollection }, '⇪ Update my collection'),
      h('button.btn.small.ghost', { type: 'button', onclick: openHandAdded }, '✎ Cards added by hand'),
      h('a.btn.small.ghost', { href: '#/alerts' }, '🔔 Price alert settings'))));
}

/** The Windows app's port: the address it serves its pages on, for when another program uses 8765. */
function portPanel(p) {
  const input = h('input.num-input', { type: 'number', min: 1024, max: 65535, step: 1, value: p.saved, 'aria-label': 'Port' });
  const note = h('p.small.muted');
  const explain = r => {
    note.textContent = r.from_environment
      ? 'The CARDCLOPS_PORT environment variable is set, so it decides the port and this choice waits until it’s removed.'
      : r.saved !== r.port ? `Cardclops will use port ${r.saved} the next time it starts (it’s on ${r.port} now).`
      : `Cardclops is on port ${r.port}.`;
  };
  const save = async value => {
    try { const r = await api.app.setPort(value); input.value = r.saved; explain(r); toast(r.restart_needed ? 'Saved: restart Cardclops to use it' : 'Saved'); }
    catch (error) { toast('Could not save it: ' + error.message); }
  };
  explain(p);
  return h('section.panel',
    h('div.panel-head', h('h2', 'App'), h('span.muted.small', 'This computer')),
    row('Port', `The local address the app serves its pages on (default ${p.default}). Change it if another program uses ${p.default}.`,
      h('div.form-row', input,
        h('button.btn.small.go', { type: 'button', onclick: () => save(input.value) }, 'Save'),
        h('button.btn.small.ghost', { type: 'button', onclick: () => save(null) }, `Use ${p.default}`))),
    note);
}

/** The Ask box in the browser edition: the visitor's own Anthropic API key (static/js/edition.js). */
function askPanel() {
  const input = h('input.text-input', { type: 'password', autocomplete: 'off', spellcheck: 'false', placeholder: 'sk-ant-…',
    'aria-label': 'Anthropic API key', value: askKey() });
  const state = h('span.muted.small');
  const show = () => {
    state.textContent = askKey() ? '✓ Ask is on.' : 'Ask is off.';
    const askMode = document.querySelector('.search-form .seg.mode');
    if (askMode) askMode.hidden = !askKey();
  };
  const save = h('button.btn.small.go', { type: 'button', onclick: () => {
    const value = input.value.trim();
    if (value && !value.startsWith('sk-ant-')) { toast('That doesn’t look like an Anthropic API key (they start sk-ant-)'); return; }
    setAskKey(value); show(); toast(value ? 'Ask box on' : 'Key removed');
  } }, 'Save');
  const remove = h('button.btn.small.ghost', { type: 'button', onclick: () => { input.value = ''; setAskKey(''); show(); toast('Key removed'); } }, 'Remove');
  show();
  return h('section.panel',
    h('div.panel-head', h('h2', 'Ask box'), h('span.muted.small', 'Optional')),
    h('p', 'Ask turns a question in plain English (“blue instants under $1 that counter creatures”) into a search, using Claude. ',
      'It needs your own Anthropic API key, from ', h('a', { href: 'https://console.anthropic.com/settings/keys', target: '_blank', rel: 'noopener' }, 'console.anthropic.com'),
      '. Anthropic bills the key’s account per use; a question costs well under a cent.'),
    row('API key', 'Kept in this browser only; sent only to Anthropic, with each question', h('div.form-row', input, save, remove)),
    h('p.small.muted', 'Anyone who can use this browser profile could read the key. Use a key made for Cardclops, with a spending limit, and remove it from shared computers. ', state));
}

/** What the browser edition leaves out, and why. */
function editionPanel() {
  return h('section.panel',
    h('div.panel-head', h('h2', 'About this edition')),
    h('p', 'This is Cardclops in your browser: no account, and nothing you import leaves this device. A few things work differently from the app:'),
    h('ul.pref-list',
      h('li', h('b', 'Updates '), 'happen when you open Cardclops, if the card data is a day old, rather than on a schedule.'),
      h('li', h('b', 'Price alerts '), 'wait in the bell rather than as system notifications.'),
      h('li', h('b', 'Archidekt decks '), 'come in by pasting their list: Archidekt doesn’t let other websites read its decks.'),
      h('li', h('b', 'The Ask box '), 'needs your own Anthropic API key (above).'),
      h('li', h('b', 'Quitting '), 'is closing the tab.')));
}
