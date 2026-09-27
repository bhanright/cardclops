// Settings (#/settings): how Cardclops looks on this device (theme, text size, motion, foil
// shimmer, hover previews), and the app's data and updates. Appearance is saved in this
// browser only; index.html applies it before the first paint so a chosen theme never flashes.
import { h, clear, int, spinner, errorBox, toast, store } from './util.js';
import { api } from './api.js';
import { refreshNow, updateCollection } from './setup.js';
import { openHandAdded } from './addcards.js';

export const SETTINGS_KEY = 'gallery.settings';     // index.html reads the same key
const DEFAULTS = { theme: 'neon', textScale: 100, motion: 'system', foil: true, hoverPreview: true };
const THEMES = [
  { id: 'neon', name: 'Neon', note: 'The original: saturated color on deep violet', swatch: ['#140a24', '#ff3fa4', '#b8ff3c', '#2ee6ff'] },
  { id: 'dark', name: 'Dark', note: 'Softer colors on charcoal', swatch: ['#131317', '#f06aa6', '#b7e06e', '#72d0e6'] },
  { id: 'light', name: 'Light', note: 'Warm paper and deeper inks', swatch: ['#f6f0e4', '#e0237f', '#5b9e00', '#0a8aab'] },
  { id: 'auto', name: 'Match device', note: 'Light or Dark, following your device', swatch: ['#f6f0e4', '#131317', '#e0237f', '#f06aa6'] },
];
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
  document.querySelector('meta[name="color-scheme"]')?.setAttribute('content', theme === 'light' ? 'light' : 'dark');
  const style = getComputedStyle(root);
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', style.getPropertyValue('--bg').trim());
  // The Android app colors the phone's status and navigation bars to match (MainActivity.ThemeBridge).
  window.CardclopsAndroid?.setBars(style.getPropertyValue('--bg-deep').trim(), style.getPropertyValue('--bg').trim(), theme === 'light');
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

function themePicks(current) {
  const box = h('div.theme-picks', { role: 'group', 'aria-label': 'Theme' });
  const draw = value => clear(box).append(...THEMES.map(t => h('button.theme-pick', {
    type: 'button', 'aria-pressed': String(t.id === value), title: t.note,
    onclick: () => { change({ theme: t.id }); draw(t.id); } },
  h('span.theme-swatch', { 'aria-hidden': 'true' }, t.swatch.map(color => h('span', { style: { background: color } }))),
  h('b', t.name), h('span.small.muted', t.note))));
  draw(current);
  return box;
}

export async function showSettings() {
  const host = document.getElementById('view-settings');
  const s = settings();
  const canHover = matchMedia('(hover: hover) and (pointer: fine)').matches;
  const dataBox = h('div', spinner('Reading the app’s data…'));
  clear(host).append(h('div.settings-page',
    h('section.panel',
      h('div.panel-head', h('h2', 'Appearance'), h('span.muted.small', 'Saved on this device')),
      row('Theme', 'Colors for the whole app', themePicks(s.theme)),
      row('Text size', 'Scales text and most spacing', choices('textScale', SIZES, s.textScale)),
      row('Motion', 'Wobbling logo, ringing bell, sliding cards', choices('motion', [['system', 'Follow my device'], ['reduce', 'Reduce']], s.motion)),
      row('Foil shimmer', 'The rainbow sheen on foil cards', toggle('foil', 'Show it', s.foil !== false)),
      canHover ? row('Card previews', 'A large image when the pointer rests on a card name', toggle('hoverPreview', 'Show them', s.hoverPreview !== false)) : null),
    h('section.panel', h('div.panel-head', h('h2', 'Data and updates')), dataBox)));
  document.title = 'Settings · Cardclops';

  let status, summary;
  try { [status, summary] = await Promise.all([api.setup.status(), api.summary()]); }
  catch (error) { clear(dataBox).append(errorBox(error.message)); return; }
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
      : 'Automatically, once a day.');
  clear(dataBox).append(
    h('dl.pref-facts',
      h('dt', 'Version'), h('dd', `Cardclops ${status.version}`),
      h('dt', 'Card data'), h('dd', status.card_data_date ? `Scryfall and prices from ${status.card_data_date}` : 'Not downloaded yet'),
      h('dt', 'Collection'), h('dd', `${int(summary.copies)} copies of ${int(summary.unique_cards)} cards`),
      h('dt', 'Your data'), h('dd', h('code', status.data_dir), h('span.muted.small', ' — collection, decks, backups')),
      h('dt', 'Card cache'), h('dd', h('code', status.cache_dir), h('span.muted.small', ' — safe to delete; it downloads again'))),
    row('Price updates', null, daily),
    row('Housekeeping', null, h('div.form-row',
      h('button.btn.small', { type: 'button', onclick: refreshNow }, '⟳ Refresh card data now'),
      h('button.btn.small', { type: 'button', onclick: updateCollection }, '⇪ Update my collection'),
      h('button.btn.small.ghost', { type: 'button', onclick: openHandAdded }, '✎ Cards added by hand'),
      h('a.btn.small.ghost', { href: '#/alerts' }, '🔔 Price alert settings'))));
}
