// The engine in the browser (the public edition): Pyodide runs gallery/browser.py in this Web Worker,
// off the page's main thread. The page (boot.js) passes it every /api/ request the service worker
// (sw.js) catches, and it answers as the server would.
//
// Storage: the browser's private file system (OPFS) is mounted at /data, so the databases survive
// closing the tab; after a write the changed files are saved back (syncfs), shortly and in one go.
/* global loadPyodide */
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
importScripts(PYODIDE + 'pyodide.js');

let engine = null;
let storage = null;
let saveTimer = null;

const status = text => postMessage({ type: 'status', text });

async function boot() {
  status('Starting Python…');
  const py = await loadPyodide({ indexURL: PYODIDE });
  await py.loadPackage(['sqlite3']);
  try { await py.loadPackage(['certifi']); } catch { /* only used for downloads, which go through fetch here */ }

  status('Loading Cardclops…');
  const manifest = await (await fetch('manifest.json')).json();
  py.FS.mkdirTree('/engine/gallery');
  await Promise.all(manifest.files.map(async name => {
    py.FS.writeFile(`/engine/gallery/${name}`, await (await fetch(`py/gallery/${name}`)).text());
  }));

  status('Opening your collection…');
  py.FS.mkdirTree('/data');
  storage = await py.mountNativeFS('/data', await navigator.storage.getDirectory());
  py.runPython("import sys; sys.path.insert(0, '/engine')");
  engine = py.pyimport('gallery.browser');
  const started = engine.start().toJs({ dict_converter: Object.fromEntries });
  return { version: manifest.version, ...started };
}

const ready = boot();
ready.then(info => postMessage({ type: 'ready', info }), error => postMessage({ type: 'failed', error: String(error?.message || error) }));

/** Save changed files to the browser's storage, once writes have settled. */
function scheduleSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => storage.syncfs().catch(error => postMessage({ type: 'save-failed', error: String(error) })), 400);
}

onmessage = async ({ data }) => {
  if (data.type === 'save-now') {
    clearTimeout(saveTimer);
    await ready;
    await storage.syncfs();
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
