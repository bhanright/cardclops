// First-run setup wizard, the shared collection-import flow, background-job banner, and the
import { reconciledList } from './addcards.js';
// housekeeping actions in the Tools menu (update collection, refresh card data, quit). docs/SETUP.md.
import { h, $, clear, int, spinner, errorBox, store, toast } from './util.js';
import { api } from './api.js';

const STEP_KEY = 'gallery.setupStep';
const STEPS = ['Welcome', 'Card data', 'Your collection', 'Options', 'Done'];
const SUPPORTED = ['ManaBox', 'Moxfield', 'Archidekt', 'Deckbox', 'TCGplayer', 'Dragon Shield', 'Delver Lens',
  'any CSV with a Scryfall ID, set + collector number, or card name column'];
const FORMAT_NAMES = { manabox: 'ManaBox', moxfield: 'Moxfield', deckbox: 'Deckbox', archidekt: 'Archidekt', dragonshield: 'Dragon Shield',
  tcgplayer: 'TCGplayer', tcgplayer_inventory: 'TCGplayer inventory', delverlens: 'Delver Lens', helvault: 'Helvault', generic: 'a generic CSV' };
const HOW_TO = [
  ['ManaBox', 'Collection → ⋯ menu → Export → CSV. Send the file to this PC. To add one binder, export just that binder the same way and choose "Add to my collection".'],
  ['Moxfield', 'Collection → More → Export → CSV.'],
  ['Archidekt', 'Collection → Export → CSV (keep the default columns).'],
  ['Deckbox', 'Inventory → Tools → Export → CSV.'],
];

// ---------- jobs ----------
/** Poll /api/setup/progress every `ms` until the job is done; onTick(progress) each time. */
export function watchJob(onTick, ms = 1000) {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    let p;
    try { p = await api.setup.progress(); } catch (error) { p = { done: true, error: error.message }; }
    onTick(p);
    if (!p.done && !stopped) setTimeout(tick, ms);
  };
  tick();
  return () => { stopped = true; };
}

function progressView(p) {
  const percent = Math.max(0, Math.min(100, p.percent ?? 0));
  return h('div.job-progress',
    h('div.jp-stage', h('b', p.stage || 'Starting…'), p.message ? h('span.muted', ' · ' + p.message) : null),
    h('div.progress.big', { role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(percent), 'aria-label': p.stage || 'Progress' },
      h('span.progress-fill', { class: percent >= 100 ? 'full' : null, style: { width: percent + '%' } })),
    h('div.jp-pct', `${Math.round(percent)}%`));
}

// ---------- background banner ----------
let bannerStop = null;
/** Shows a slim banner while a background job runs (e.g. the refresh on launch); polls every 3 s only then. */
export async function checkBackgroundJob() {
  let p;
  try { p = await api.setup.progress(); } catch { return; }
  if (p && !p.done && p.job) startBanner();
}
export function startBanner() {
  const banner = $('#jobBanner');
  if (!banner || bannerStop) return;
  const labels = { refresh: 'Updating card data and prices…', import: 'Importing your collection…', download: 'Downloading card data…' };
  banner.hidden = false;
  bannerStop = watchJob(p => {
    if (!p.done) {
      clear(banner).append(h('span.spinner', { 'aria-hidden': 'true' }), h('b', labels[p.job] || 'Working…'),
        h('span.muted', ` ${p.stage || ''}${p.message ? ' · ' + p.message : ''}`), h('span.jb-pct', `${Math.round(p.percent || 0)}%`));
      return;
    }
    bannerStop = null;
    if (p.error) {
      clear(banner).append(h('b', '✗ '), `${labels[p.job] ? labels[p.job].replace('…', '') + ' failed' : 'Job failed'}: ${p.error} `,
        h('button.link-btn', { type: 'button', onclick: () => { banner.hidden = true; } }, 'Dismiss'));
      banner.classList.add('bad');
    } else {
      clear(banner).append(h('b', '✓ '), p.job === 'import' ? 'Collection updated. ' : 'Card data and prices are up to date. ',
        h('button.link-btn', { type: 'button', onclick: () => location.reload() }, 'Reload to see it'));
      banner.classList.add('good');
      setTimeout(() => { banner.hidden = true; banner.classList.remove('good'); }, 15000);
    }
  }, 3000);
}

// ---------- import flow (wizard step 3 and "Update my collection") ----------
// What each import mode does, for the choice in "Update my collection".
const IMPORT_MODES = [
  ['replace', 'Replace my collection', 'A full export of your collection. It replaces everything imported before; decks, the watchlist and price history stay, and deck copies are re-allocated.'],
  ['add', 'Add to my collection', 'A ManaBox binder export, or any list of cards. Its cards join your collection. Importing a binder you’ve imported before, on its own or as part of a full export, updates it rather than counting its cards twice.'],
];

/** File picker → POST /api/setup/import → progress → result. onDone(result) after a successful import.
 *  With chooseMode, the picker offers replacing the collection or adding the file's cards to it. */
export function importFlow({ onDone, intro, chooseMode = false } = {}) {
  const box = h('div.import-flow');
  let mode = 'replace';
  const modeChoice = () => {
    const note = h('p.small.muted');
    const buttons = IMPORT_MODES.map(([value, label]) => h('button.seg-btn', { type: 'button', 'aria-pressed': String(mode === value),
      class: mode === value ? 'on' : null, onclick: () => { mode = value; show(); } }, label));
    const show = () => {
      IMPORT_MODES.forEach(([value, , explain], i) => {
        buttons[i].classList.toggle('on', mode === value); buttons[i].setAttribute('aria-pressed', String(mode === value));
        if (mode === value) note.textContent = explain;
      });
    };
    show();
    return h('div.import-mode', h('div.seg', { role: 'group', 'aria-label': 'What the file is' }, buttons), note);
  };
  const pick = () => {
    const input = h('input', { type: 'file', accept: '.csv,text/csv,.txt', hidden: true });
    const drop = h('div.dropzone', { tabindex: '0', role: 'button', 'aria-label': 'Choose your collection CSV or drop it here',
      onclick: () => input.click(), onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); } } },
    h('div.dz-blob', { 'aria-hidden': 'true' }, '⇩'), h('div', h('b', 'Choose your collection CSV'), ' or drop it here'),
    h('div.muted.small', 'It stays on this device; the file is read here and matched against the card data.'));
    for (const type of ['dragenter', 'dragover']) drop.addEventListener(type, e => { e.preventDefault(); drop.classList.add('over'); });
    for (const type of ['dragleave', 'drop']) drop.addEventListener(type, e => { e.preventDefault(); drop.classList.remove('over'); });
    drop.addEventListener('drop', e => { const f = e.dataTransfer?.files?.[0]; if (f) start(f); });
    input.addEventListener('change', () => { if (input.files[0]) start(input.files[0]); });
    clear(box).append(
      ...[intro, chooseMode ? modeChoice() : null].filter(Boolean),     // append() would print null
      drop, input,
      h('p.small', h('b', 'Works with: '), SUPPORTED.join(' · ')),
      h('details.howto', h('summary', 'How do I export my collection?'), h('ul', HOW_TO.map(([app, how]) => h('li', h('b', app + ': '), how)))));
  };
  const start = file => {
    const reader = new FileReader();
    clear(box).append(spinner(`Reading ${file.name}…`));
    reader.onerror = () => { clear(box).append(errorBox('Could not read that file.')); addRetry(); };
    reader.onload = async () => {
      try { await api.setup.import(file.name, String(reader.result || ''), mode); } catch (error) { clear(box).append(errorBox(error.message)); addRetry(); return; }
      const view = h('div');
      clear(box).append(h('p', 'Importing ', h('b', file.name), '…'), view);
      watchJob(p => {
        if (!p.done) { clear(view).append(progressView(p)); return; }
        if (p.error) { clear(view).append(errorBox(p.error)); addRetry(); return; }
        showResult(file.name, p.result || {});
      });
    };
    reader.readAsText(file);
  };
  const addRetry = () => box.append(h('div.form-row', h('button.btn', { type: 'button', onclick: pick }, 'Try another file')));
  const showResult = (name, r) => {
    const unmatched = r.unmatched || [];
    const approx = Array.isArray(r.approximate) ? r.approximate : [];
    const approxCount = Array.isArray(r.approximate) ? r.approximate.length : r.approximate;
    const rowList = (items, title) => items.length ? h('details.unmatched', h('summary', title),
      h('ol', items.map(u => h('li', u.line != null ? h('span.muted', `line ${u.line}: `) : null, h('code', u.text ?? u.name ?? ''), u.reason ? [' — ', u.reason] : null)))) : null;
    clear(box).append(
      h('div.import-summary',
        h('h3', r.mode === 'add' ? '✓ Added ' : '✓ Imported ', name),
        r.added_as?.length ? h('p.small', 'Filed under ', r.added_as.map((tag, i) => [i ? ', ' : '', h('b', tag)]),
          '. Importing that again updates those cards instead of adding them twice.') : null,
        r.format ? h('p.small.muted', `Read as ${FORMAT_NAMES[r.format] || r.format} export.`) : null,
        h('div.bignums',
          h('div.bignum.c-cyan', h('div.bn-value', int(r.rows)), h('div.bn-label', 'Rows')),
          h('div.bignum.c-pink', h('div.bn-value', int(r.copies)), h('div.bn-label', 'Copies')),
          h('div.bignum.c-lime', h('div.bn-value', int(r.matched)), h('div.bn-label', 'Matched')),
          approxCount != null ? h('div.bignum.c-yellow', { title: 'Matched by name when the set or number didn’t line up' }, h('div.bn-value', int(approxCount)), h('div.bn-label', 'Approximate')) : null,
          h('div', { class: 'bignum ' + (unmatched.length ? 'c-orange' : 'c-mute') }, h('div.bn-value', int(Math.max(unmatched.length, (r.rows || 0) - (r.matched || 0)))), h('div.bn-label', 'Not matched'))),
        rowList(unmatched, `${unmatched.length} row${unmatched.length === 1 ? '' : 's'} not matched${unmatched.length >= 200 ? ' (first 200)' : ''}`),
        rowList(approx, `${approx.length} approximate match${approx.length === 1 ? '' : 'es'} — worth a look`),
        reconciledList(r.manual_reconciled)),
      h('div.form-row', h('button.btn.ghost', { type: 'button', onclick: pick }, 'Import a different file')));
    onDone?.(r);
  };
  pick();
  return box;
}

// ---------- dialog helper ----------
export function dialog(title, content, { onClose } = {}) {
  const last = document.activeElement;
  const close = () => { wrap.remove(); document.removeEventListener('keydown', key); onClose?.(); last?.focus?.(); };
  const key = e => { if (e.key === 'Escape') close(); };
  const card = h('div.dialog-card', { role: 'dialog', 'aria-modal': 'true', 'aria-label': title, tabindex: '-1' },
    h('div.panel-head', h('h2', title), h('button.icon-btn.close', { type: 'button', 'aria-label': 'Close', onclick: close }, '×')), content);
  const wrap = h('div.dialog', h('div.modal-backdrop', { onclick: close }), card);
  document.body.append(wrap);
  document.addEventListener('keydown', key);
  card.focus();
  return close;
}

// ---------- housekeeping (Tools menu) ----------
export function updateCollection() {
  dialog('Update my collection', importFlow({ chooseMode: true, onDone: () => startBanner() }));
}

export async function refreshNow() {
  try { await api.refresh(); toast('Refreshing card data and prices…'); startBanner(); }
  catch (error) { toast('Could not start a refresh: ' + error.message); }
}

export function quitApp() {
  const yes = h('button.btn.danger', { type: 'button', onclick: async () => {
    yes.disabled = true;
    try { await api.quit(); } catch { /* the server may already be gone */ }
    closeDialog();
    document.body.replaceChildren(h('main.stopped', h('div.panel.stopped-card',
      h('div.blob-eye', { 'aria-hidden': 'true' }),
      h('h1', 'Cardclops has stopped'),
      h('p', 'You can close this tab. Start Cardclops again from the Start menu whenever you like.'))));
    document.title = 'Cardclops (stopped)';
  } }, 'Quit');
  const closeDialog = dialog('Quit Cardclops?', h('div',
    h('p', 'This stops the gallery on this device. Nothing is lost; your collection and decks are saved.'),
    h('div.form-row', yes, h('button.btn.ghost', { type: 'button', onclick: () => closeDialog() }, 'Cancel'))));
  yes.focus();
}

// ---------- wizard ----------
/** Resolves once setup is complete (or immediately if it isn't needed). */
export async function runSetupIfNeeded() {
  let status;
  try { status = await api.setup.status(); } catch { return; }       // an older server without setup
  if (!status?.needs_setup) return;
  await new Promise(resolve => wizard(status, resolve));
}

function wizard(status, resolve) {
  const root = h('div.setup', { role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Set up Cardclops' });
  document.body.classList.add('in-setup');
  document.body.append(root);
  const saved = store.get(STEP_KEY, 0);
  // Resume where the data says we are, never ahead of what's actually done.
  let step = !status.has_card_data ? Math.min(saved, 1) : !status.has_collection ? Math.max(2, Math.min(saved, 3)) : Math.max(3, Math.min(saved, 4));
  let priceHistory = true;
  const browser = status.edition === 'browser';
  let cardDataReady = status.has_card_data;
  let collectionDone = status.has_collection;
  // Reflect a task that already exists (a reinstall); otherwise off until the user ticks it.
  const opts = { daily_refresh: !!status.daily_refresh_scheduled, move_alerts: false, reprint_alerts: false, legality_alerts: false, windows_notifications: false };

  const go = n => { step = Math.max(0, Math.min(STEPS.length - 1, n)); store.set(STEP_KEY, step); draw(); };
  const navRow = (...extra) => h('div.setup-nav',
    step > 0 ? h('button.btn.ghost', { type: 'button', onclick: () => go(step - 1) }, '← Back') : h('span'), ...extra);

  function draw() {
    const body = [welcome, cardData, collection, options, done][step]();
    clear(root).append(h('div.setup-card.panel',
      h('div.setup-brand', h('span.setup-logo', document.querySelector('.logo-mark')?.cloneNode(true)), h('span.logo-text', h('span.l2', h('span.l2-card', 'Card'), 'clops')), h('span.muted.small', 'First-time setup')),
      h('ol.setup-steps', STEPS.map((name, i) => h('li', { class: i === step ? 'on' : i < step ? 'done' : '', 'aria-current': i === step ? 'step' : null },
        h('span.ss-num', i < step ? '✓' : String(i + 1)), h('span.ss-name', name)))),
      h('div.setup-body', body)));
    root.querySelector('h2')?.setAttribute('tabindex', '-1');
    root.querySelector('h2')?.focus();
  }

  function welcome() {
    return [h('h2', 'Welcome!'),
      h('p', 'Cardclops shows your Magic cards as a gallery you can search like Scryfall, tracks what they’re worth, checks decks against what you own and a lot more.'),
      h('ul.setup-facts',
        h('li', h('b', 'Free, and it stays on this device. '), 'Nothing you import is uploaded anywhere. The only things it downloads are public card data and prices, and card images as you look at them.'),
        ...(browser ? [
          h('li', h('b', 'Your data lives in this browser, on this device. '), 'There is no account and no server copy, so clearing this site’s data in the browser deletes it. Back it up now and then from Settings.')] : [
          h('li', h('b', 'Your data lives in '), h('code', status.data_dir || '—'), h('span.muted', ' (your collection, decks, backups)')),
          h('li', h('b', 'Card data and images are cached in '), h('code', status.cache_dir || '—'), h('span.muted', ' (safe to delete; it downloads again)'))])),
      navRow(h('button.btn.go', { type: 'button', onclick: () => go(1) }, 'Get started →'))];
  }

  function cardData() {
    const view = h('div.setup-job');
    const mb = status.downloads || {};
    const next = h('button.btn.go', { type: 'button', disabled: !cardDataReady, onclick: () => go(2) }, 'Next →');
    const history = h('input', { type: 'checkbox', checked: priceHistory, onchange: e => { priceHistory = e.target.checked; } });
    const cardMb = browser ? mb.pack_mb : mb.scryfall_mb;
    const label = () => `⇩ Download (${int((cardMb || 0) + (priceHistory ? mb.history_mb || 0 : 0))} MB)`;
    const download = h('button.btn.go', { type: 'button', onclick: () => start() }, label());
    history.addEventListener('change', () => { download.textContent = label(); });
    const start = async () => {
      download.disabled = true;
      try { await api.setup.download(priceHistory); } catch (error) { clear(view).append(errorBox(error.message)); download.disabled = false; return; }
      follow();
    };
    const follow = () => watchJob(p => {
      if (!p.done) { clear(view).append(progressView(p)); return; }
      if (p.error) {
        clear(view).append(errorBox(p.error), h('div.form-row', h('button.btn', { type: 'button', onclick: () => { clear(view); start(); } }, '⟳ Try again')));
        download.disabled = false;
        return;
      }
      cardDataReady = true;
      status.card_data_date = null;
      draw();                      // re-render the step as done: the download controls go away
    });
    // Resuming mid-download: pick the progress back up.
    api.setup.progress().then(p => { if (p && !p.done && p.job === 'download') { download.disabled = true; follow(); } }).catch(() => {});
    return [h('h2', 'Card data'),
      h('p', browser ? 'To show and search your cards, Cardclops needs the card database: every card, printing and today’s price, from Scryfall, prepared by Cardclops each day.'
        : 'To show and search your cards, Cardclops needs Scryfall’s card database: every card, printing, image link and today’s price.'),
      cardDataReady ? h('div.setup-ok', `✓ Card data is here${status.card_data_date ? ` (from ${status.card_data_date})` : ''}. You can move on.`) : h('div.setup-choice',
        h('div.setup-item', h('b', 'Scryfall card data'), h('span.muted', ` · about ${int(cardMb)} MB · required`)),
        h('label.setup-item.check', history, h('span', h('b', browser ? ' Price history' : ' Price history for the last 90 days'),
          h('span.muted', browser ? ' · MTGJSON · fetched for your cards after you import them · optional' : ` · MTGJSON · about ${int(mb.history_mb)} MB · optional`),
          h('span.small.muted.block', 'Fills the price charts right away instead of starting from today.')))),
      cardDataReady ? null : h('p.small.muted', 'Nothing is downloaded until you press the button.'),
      cardDataReady ? null : h('div.form-row', download),
      view,
      navRow(next)];
  }

  function collection() {
    const next = h('button.btn.go', { type: 'button', disabled: !collectionDone, onclick: () => go(3) }, 'Next →');
    const flow = importFlow({ onDone: () => { collectionDone = true; next.disabled = false; } });
    return [h('h2', 'Your collection'),
      collectionDone && status.collection_rows ? h('div.setup-ok', `✓ You already have a collection here (${int(status.collection_rows)} rows). Import again to replace it, or move on.`) : null,
      h('p', 'Import the CSV export from the app you track your cards in.'),
      flow,
      navRow(h('button.btn.ghost', { type: 'button', onclick: () => go(3) }, 'Skip — start with an empty collection'), next)];
  }

  function options() {
    const box = (key, label, hint) => h('label.setup-item.check', h('input', { type: 'checkbox', checked: opts[key], onchange: e => { opts[key] = e.target.checked; } }),
      h('span', h('b', ' ' + label), hint ? h('span.small.muted.block', hint) : null));
    const saving = h('span.muted.small');
    const next = h('button.btn.go', { type: 'button', onclick: async () => {
      next.disabled = true; saving.textContent = 'Saving…';
      try {
        // Only touch the scheduled task when the choice differs from what's already there.
        if (status.platform === 'win32' && opts.daily_refresh !== !!status.daily_refresh_scheduled) {
          const r = await api.setup.options({ daily_refresh: opts.daily_refresh });
          status.daily_refresh_scheduled = !!r.daily_refresh_scheduled;
        }
        const chosen = Object.fromEntries(['move_alerts', 'reprint_alerts', 'legality_alerts', 'windows_notifications'].filter(k => opts[k]).map(k => [k, true]));
        if (Object.keys(chosen).length) await api.alerts.saveSettings(chosen);
        go(4);
      } catch (error) { saving.textContent = '✗ ' + error.message; next.disabled = false; }
    } }, 'Next →');
    return [h('h2', 'Options'),
      h('p.muted', 'Everything here is off unless you tick it, and you can change it later.'),
      status.platform === 'win32' ? h('section.setup-group', h('h3', 'Keeping prices current'),
        box('daily_refresh', 'Update prices every morning', status.daily_refresh_scheduled ? 'A Windows scheduled task already does this at 7:30 AM. Untick to remove it.' : 'Adds a Windows scheduled task at 7:30 AM (only while you’re signed in). Otherwise prices update when you open the gallery.')) : null,
      h('section.setup-group', h('h3', 'Price alerts'),
        box('move_alerts', 'Big price moves on cards I hold', 'When a card worth $10+ moves 20% in a week.'),
        box('reprint_alerts', 'Wide reprints of cards I hold', 'When a reprint that could lower a card’s price is announced.'),
        box('legality_alerts', 'Bans, unbans and rotations', 'When a format change touches a card you hold.'),
        status.platform === 'win32' ? box('windows_notifications', 'Show Windows notifications', 'Otherwise alerts wait quietly in the gallery.') : null),
      status.ask_available === false && status.platform !== 'android' && !browser ? h('p.small.hint', h('b', 'About the Ask box: '), 'turning English questions into searches needs the Claude command-line tool, which isn’t installed. Everything else works without it.') : null,
      navRow(saving, next)];
  }

  function done() {
    const finish = h('button.btn.go.big', { type: 'button', onclick: async () => {
      finish.disabled = true;
      try { await api.setup.complete(); } catch (error) { toast('Could not finish setup: ' + error.message); finish.disabled = false; return; }
      store.set(STEP_KEY, 0);
      root.remove();
      document.body.classList.remove('in-setup');
      resolve();
    } }, 'Open my gallery →');
    return [h('h2', 'All set!'),
      h('p', collectionDone ? 'Your collection is in. Search it, build decks and watch prices from the gallery.' : 'You can import your collection any time from More → Update my collection.'),
      h('ul.setup-facts', h('li', 'Tip: press ', h('kbd', '/'), ' anywhere to jump to the search box.'), h('li', 'The ', h('b', 'More'), ' menu has Deck check, Extras, Price alerts, Update my collection and Refresh card data.')),
      navRow(finish)];
  }

  draw();
}
