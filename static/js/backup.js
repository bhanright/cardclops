// Backup and restore (Settings → Backup; gallery/backups.py). A backup is the whole of your own
// database as one file; it restores in any edition. In the browser edition that file is the only copy
// outside the browser, so the page also reminds you when you haven't downloaded one for a while.
import { h, int, errorBox, toast, spinner, clear } from './util.js';
import { api } from './api.js';
import { dialog } from './setup.js';
import { browserEdition } from './edition.js';

const REMINDER_DISMISSED = 'cardclops.backupReminderDismissed';     // this session only

const when = iso => (iso ? new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : 'never');

/** Download a backup: the browser saves /api/backup as a file. */
export function downloadBackup() {
  const link = h('a', { href: api.backup.url, download: '' });
  document.body.append(link);
  link.click();
  link.remove();
  toast('Saving a backup…');
}

/** The Settings panel. */
export function backupPanel(summary) {
  const last = h('span', when(summary?.backup?.last));
  const persisted = h('p.small.muted');
  if (browserEdition && navigator.storage?.persisted) {
    navigator.storage.persisted().then(yes => {
      clear(persisted).append(yes
        ? 'This browser has agreed to keep Cardclops’s data until you clear it yourself.'
        : 'This browser may clear Cardclops’s data if the device runs short of space, so keep a recent backup.',
      yes ? '' : ' ', yes ? null : h('button.link-btn', { type: 'button', onclick: async () => {
        const granted = await navigator.storage.persist().catch(() => false);
        toast(granted ? 'The browser will keep it' : 'The browser said no; backups are the safe way');
        if (granted) clear(persisted).append('This browser has agreed to keep Cardclops’s data until you clear it yourself.');
      } }, 'Ask it to keep it'));
    }).catch(() => {});
  }
  const pick = h('input', { type: 'file', accept: '.sqlite,.db,application/vnd.sqlite3,application/x-sqlite3', hidden: true,
    onchange: () => { if (pick.files[0]) restoreFlow(pick.files[0]); pick.value = ''; } });
  return h('section.panel',
    h('div.panel-head', h('h2', 'Backup'), h('span.muted.small', `Last downloaded: `, last)),
    h('p', browserEdition
      ? 'Your collection, decks, binders, alerts and price history live only in this browser. A backup is one file with all of it; keep it somewhere safe, and restore it here or in the Cardclops app.'
      : 'A backup is one file with your collection, decks, binders, alerts and price history. The app also keeps daily copies in its Backups folder; a downloaded one restores in any edition, including cardclops.com in a browser.'),
    h('div.form-row',
      h('button.btn.go', { type: 'button', onclick: () => { downloadBackup(); last.textContent = when(new Date().toISOString()); hideReminder(); } }, '⇩ Download a backup'),
      h('button.btn', { type: 'button', onclick: () => pick.click() }, '⇪ Restore from a backup…'), pick),
    browserEdition ? persisted : null);
}

const readBase64 = file => new Promise((resolve, reject) => {
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
  reader.onerror = () => reject(reader.error);
  reader.readAsDataURL(file);
});

/** Check the file, show what it holds, and replace everything here only once confirmed. */
async function restoreFlow(file) {
  const body = h('div', spinner(`Reading ${file.name}…`));
  const close = dialog('Restore from a backup', body);
  let data, found;
  try {
    data = await readBase64(file);
    found = await api.backup.check(data);
  } catch (error) {
    clear(body).append(errorBox(error.message));
    return;
  }
  const go = h('button.btn.go', { type: 'button', onclick: async () => {
    go.disabled = true;
    clear(status).append(spinner('Restoring…'));
    try {
      const result = await api.backup.restore(data);
      clear(body).append(h('div.setup-ok', `✓ Restored ${int(result.copies)} copies and ${int(result.decks)} decks.`),
        result.refreshing ? h('p.muted', 'Card details for the restored cards are downloading in the background.') : null,
        h('div.form-row', h('button.btn.go', { type: 'button', onclick: () => { close(); location.reload(); } }, 'Show it')));
    } catch (error) {
      clear(status).append(errorBox(error.message));
      go.disabled = false;
    }
  } }, 'Replace everything here');
  const status = h('div');
  clear(body).append(
    h('p', h('b', file.name), ' holds:'),
    h('ul.pref-list',
      h('li', `${int(found.copies)} copies in ${int(found.rows)} rows`),
      h('li', `${int(found.decks)} decks, ${int(found.binders)} binders, ${int(found.watchlist)} watched cards`),
      found.made_at ? h('li', `made ${when(found.made_at)}`) : null),
    h('p.warn-box', '⚠ Restoring replaces everything in Cardclops here (collection, decks, binders, alerts and price history) with the backup’s. Card data isn’t affected.'),
    h('div.form-row', h('button.btn', { type: 'button', onclick: downloadBackup }, '⇩ Back up what’s here first'), go),
    status);
}

// ---------- the reminder (browser edition) ----------

let reminder = null;
function hideReminder() {
  reminder?.remove();
  reminder = null;
  try { sessionStorage.setItem(REMINDER_DISMISSED, '1'); } catch { /* fine: it just shows again next time */ }
}

/** A banner under the header when the summary says a backup is due and it wasn't dismissed. */
export function remindIfDue(summary) {
  let dismissed = false;
  try { dismissed = sessionStorage.getItem(REMINDER_DISMISSED) === '1'; } catch { /* treat as not dismissed */ }
  if (!summary?.backup?.due) { reminder?.remove(); reminder = null; return; }   // backed up since (a restore, say)
  if (dismissed || reminder) return;
  reminder = h('div.job-banner.backup-reminder', { role: 'status' },
    h('span', summary.backup.last
      ? `Your last backup was ${when(summary.backup.last)}. Your collection lives only in this browser.`
      : 'Your collection lives only in this browser. Keep a backup in case its data is ever cleared.'),
    h('button.link-btn', { type: 'button', onclick: () => { downloadBackup(); hideReminder(); } }, 'Download a backup'),
    h('button.link-btn', { type: 'button', onclick: hideReminder }, 'Not now'));
  document.getElementById('jobBanner')?.after(reminder);
}
