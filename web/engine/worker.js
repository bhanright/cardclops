// The engine in the browser (the public edition): Pyodide runs gallery/browser.py in this Web Worker,
// off the page's main thread. The page (boot.js) passes it every /api/ request the service worker
// (sw.js) catches, and it answers as the server would.
//
// Storage: the browser's private file system (OPFS) is loaded into Python's in-memory files at /data
// when the engine starts. After a write, the changed files are copied back (save()), shortly and in
// one go. Python's databases keep their journal in memory here, so a file is only copied while no
// transaction is open, and copied in one step: requests and jobs that run while the copy is being
// written can't tear it. OPFS replaces a file whole when its write closes, so what's stored is always
// the old version or the new one.
/* global loadPyodide */
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
importScripts(PYODIDE + 'pyodide.js');

let py = null;
let engine = null;
let saveTimer = null;
const savedAs = new Map();          // path under /data -> "mtime:size" as last saved (or loaded)

const status = text => postMessage({ type: 'status', text });

async function boot() {
  status('Starting Python…');
  py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage(['sqlite3']);
  try { await py.loadPackage(['certifi']); } catch { /* only used for downloads, which go through fetch here */ }

  status('Loading Cardclops…');
  // Revalidate every time (a cheap 304 when unchanged), so a new release never runs next to stale files.
  const manifest = await (await fetch('manifest.json', { cache: 'no-cache' })).json();
  py.FS.mkdirTree('/engine/gallery');
  await Promise.all(manifest.files.map(async name => {
    py.FS.writeFile(`/engine/gallery/${name}`, await (await fetch(`py/gallery/${name}`, { cache: 'no-cache' })).text());
  }));

  status('Opening your collection…');
  py.FS.mkdirTree('/data');
  await py.mountNativeFS('/data', await navigator.storage.getDirectory());
  walk('/data', (path, version) => savedAs.set(path, version));
  py.runPython("import sys; sys.path.insert(0, '/engine')");
  engine = py.pyimport('gallery.browser');
  self.requestSave = scheduleSave;                    // for jobs, which write between requests (gallery/runtime.py)
  // Card data and price files: published beside the site unless the build says elsewhere (scripts/build_static.py).
  const dataUrl = new URL(manifest.data_url || '/pack/', location.origin).href;
  const started = engine.start(dataUrl).toJs({ dict_converter: Object.fromEntries });
  return { version: manifest.version, ...started };
}

const ready = boot();
ready.then(info => postMessage({ type: 'ready', info }), error => postMessage({ type: 'failed', error: String(error?.message || error) }));

/** Every file under `dir`, with a version stamp ("mtime:size") that changes when it's written. */
function walk(dir, visit) {
  for (const name of py.FS.readdir(dir)) {
    if (name === '.' || name === '..') continue;
    const path = `${dir}/${name}`;
    const stat = py.FS.stat(path);
    if (py.FS.isDir(stat.mode)) walk(path, visit);
    else visit(path, `${+stat.mtime}:${stat.size}`);
  }
}

async function opfsFolder(parts, create) {
  let folder = await navigator.storage.getDirectory();
  for (const part of parts) folder = await folder.getDirectoryHandle(part, { create });
  return folder;
}

let saving = null;
let saveAgain = false;

/** Copy every file changed since the last save to the browser's storage; remove deleted ones. */
function save() {
  if (saving) { saveAgain = true; return saving; }
  saving = (async () => {
    do {
      saveAgain = false;
      while (!engine.idle()) await new Promise(resolve => setTimeout(resolve, 200));
      // From here to the copies, nothing awaits: the snapshot is of one moment with no transaction open.
      const changed = [];
      const present = new Set();
      walk('/data', (path, version) => {
        present.add(path);
        if (savedAs.get(path) !== version) changed.push({ path, version, data: py.FS.readFile(path) });
      });
      const removed = [...savedAs.keys()].filter(path => !present.has(path));
      for (const { path, version, data } of changed) {
        const parts = path.split('/').slice(2);                  // "/data/user/x" -> ["user", "x"]
        const folder = await opfsFolder(parts.slice(0, -1), true);
        const writable = await (await folder.getFileHandle(parts.at(-1), { create: true })).createWritable();
        await writable.write(data);
        await writable.close();
        savedAs.set(path, version);
      }
      for (const path of removed) {
        const parts = path.split('/').slice(2);
        try { await (await opfsFolder(parts.slice(0, -1), false)).removeEntry(parts.at(-1)); } catch { /* already gone */ }
        savedAs.delete(path);
      }
    } while (saveAgain);
  })().catch(error => postMessage({ type: 'save-failed', error: String(error?.message || error) }))
    .finally(() => { saving = null; });
  return saving;
}

/** Save once writes have settled. */
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 400);
}

onmessage = async ({ data }) => {
  if (data.type === 'save-now') {
    clearTimeout(saveTimer);
    await ready;
    await save();
    postMessage({ type: 'saved', id: data.id });
    return;
  }
  if (data.type !== 'request') return;
  let reply;
  try {
    await ready;
    const result = engine.handle(data.method, data.path, JSON.stringify(data.params || {}), data.body || '');
    reply = result.toJs({ dict_converter: Object.fromEntries });
    result.destroy?.();
    if (reply.wrote) scheduleSave();
  } catch (error) {
    reply = { status: 500, kind: 'json', content_type: 'application/json', body: JSON.stringify({ error: String(error?.message || error) }) };
  }
  const transfer = reply.body instanceof Uint8Array ? [reply.body.buffer] : [];
  postMessage({ type: 'reply', id: data.id, reply }, transfer);
};
