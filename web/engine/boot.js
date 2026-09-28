// Starts the public edition: the service worker (sw.js) that stands in for the server, and the engine
// (worker.js, Pyodide) that answers it. When both are ready it loads the normal page (app.js), which
// then talks to /api/ exactly as it does with the server edition.
//
// One tab runs the engine: the databases are files in the browser's storage, and two engines writing
// them at once would lose changes. A second tab says so instead of starting.

const splash = document.createElement('div');
splash.className = 'boot-splash';
splash.innerHTML = `<div class="boot-card"><div class="blob-eye" aria-hidden="true"></div>
  <h1>Cardclops</h1><p class="boot-status" role="status">Starting…</p></div>`;
document.body.append(splash);
const say = text => { splash.querySelector('.boot-status').textContent = text; };

async function serviceWorkerReady() {
  if (!('serviceWorker' in navigator)) throw new Error('This browser can’t run Cardclops (no service workers). Try a current Chrome, Edge, Firefox or Safari.');
  await navigator.serviceWorker.register('/sw.js', { scope: '/' });
  if (!navigator.serviceWorker.controller) {
    await new Promise(resolve => navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true }));
  }
}

/** Development only (?devdata=1): seed the browser's storage with databases served from /devdata/. */
async function seedDevData() {
  const root = await navigator.storage.getDirectory();
  const copy = async (dir, name, url) => {
    const folder = await root.getDirectoryHandle(dir, { create: true });
    try { await folder.getFileHandle(name); return; } catch { /* not there yet */ }
    say(`Copying ${url}…`);
    const data = await (await fetch(url)).arrayBuffer();
    const writable = await (await folder.getFileHandle(name, { create: true })).createWritable();
    await writable.write(data);
    await writable.close();
  };
  await copy('user', 'cardclops.sqlite', '/devdata/cardclops.sqlite');
  await copy('cache', 'cards.sqlite', '/devdata/cards.sqlite');
}

function startEngine() {
  return new Promise((resolve, reject) => {
    const worker = new Worker('/engine/worker.js');
    const waiting = new Map();
    let next = 0;
    worker.onmessage = ({ data }) => {
      if (data.type === 'status') say(data.text);
      else if (data.type === 'ready') resolve({ worker, info: data.info });
      else if (data.type === 'failed') reject(new Error(data.error));
      else if (data.type === 'reply' || data.type === 'saved') { waiting.get(data.id)?.(data.reply); waiting.delete(data.id); }
      else if (data.type === 'save-failed') console.error('Cardclops couldn’t save to browser storage:', data.error);
    };
    // The service worker hands over each /api/ request with a port to answer on.
    navigator.serviceWorker.addEventListener('message', event => {
      if (event.data?.type !== 'api') return;
      const id = ++next;
      const port = event.ports[0];
      waiting.set(id, reply => port.postMessage(reply, reply.body instanceof Uint8Array ? [reply.body.buffer] : []));
      worker.postMessage({ type: 'request', id, method: event.data.method, path: event.data.path,
        params: event.data.params, body: event.data.body });
    });
    // Save when the tab is hidden or closed, rather than waiting for the timer.
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') worker.postMessage({ type: 'save-now', id: ++next });
    });
  });
}

async function boot() {
  try {
    await serviceWorkerReady();
    if (navigator.storage?.persist) navigator.storage.persist().catch(() => {});
    const owner = await new Promise(resolve => navigator.locks.request('cardclops-engine', { ifAvailable: true }, lock => {
      resolve(Boolean(lock));
      return lock ? new Promise(() => {}) : null;           // hold it for as long as this tab is open
    }));
    if (!owner) {
      say('Cardclops is already open in another tab. Use that one, or close it and reload this one.');
      return;
    }
    if (new URLSearchParams(location.search).has('devdata')) await seedDevData();
    const { info } = await startEngine();
    navigator.serviceWorker.controller.postMessage({ type: 'engine-ready' });
    console.info('Cardclops engine ready', info);
    await import('/app.js');
    splash.remove();
  } catch (error) {
    say('Cardclops couldn’t start: ' + (error?.message || error));
    console.error(error);
  }
}

boot();
