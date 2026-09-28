// The public edition's stand-in for the server. The page's code fetches /api/... and shows images
// from /img/... exactly as it does with the server; this service worker answers both:
//   /img/<scryfall id>/<front|back>/<size>  -> Scryfall's image server (a fixed pattern from the id)
//   /api/...                                -> the engine, running in a page's Web Worker (engine/boot.js)
// The page and its own scripts and styles are always revalidated with the site (a cheap 304 when
// unchanged), whatever a browser's cache holds: cardclops.com once served the private edition, whose
// cached scripts must never run here, and a release must never mix old and new files.
// Everything else is fetched as usual.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

let engineClientId = null;            // the tab whose worker runs the engine
self.addEventListener('message', event => {
  if (event.data?.type === 'engine-ready') engineClientId = event.source.id;
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/img/')) event.respondWith(image(url));
  else if (url.pathname.startsWith('/api/')) event.respondWith(api(event, url));
  else if (event.request.mode === 'navigate' || OWN_CODE.test(url.pathname)) event.respondWith(fresh(event.request, url));
});

const OWN_CODE = /^\/(app\.js|styles\.css|js\/[\w-]+\.js|engine\/[\w.-]+|fonts\/fonts\.css)$/;

function fresh(request, url) {
  // A navigation's Request can't be copied with new options, so it's fetched by its URL.
  return request.mode === 'navigate'
    ? fetch(url.href, { cache: 'no-cache', credentials: 'same-origin' })
    : fetch(request, { cache: 'no-cache' });
}

function image(url) {
  const [, , id, face, size] = url.pathname.split('/');
  if (!/^[0-9a-f-]{36}$/.test(id || '')) return new Response('', { status: 404 });
  const extension = size === 'png' ? 'png' : 'jpg';
  return Response.redirect(`https://cards.scryfall.io/${size}/${face}/${id[0]}/${id[1]}/${id}.${extension}`, 302);
}

async function engineClient(event) {
  const byId = id => (id ? self.clients.get(id) : Promise.resolve(null));
  return (await byId(engineClientId)) || (await byId(event.clientId))
    || (await self.clients.matchAll({ type: 'window' }))[0] || null;
}

async function api(event, url) {
  const client = await engineClient(event);
  if (!client) return json(503, { error: 'Cardclops isn’t running: open it in a tab first.' });
  const request = event.request;
  const body = request.method === 'GET' || request.method === 'HEAD' ? '' : await request.text();
  const reply = await new Promise(resolve => {
    const channel = new MessageChannel();
    channel.port1.onmessage = message => resolve(message.data);
    client.postMessage({ type: 'api', method: request.method, path: url.pathname,
      params: Object.fromEntries(url.searchParams), body }, [channel.port2]);
  });
  const headers = { 'Content-Type': reply.content_type || 'application/json', 'Cache-Control': 'no-store' };
  if (reply.kind === 'file' || reply.kind === 'download') headers['Content-Disposition'] = `attachment; filename="${reply.filename}"`;
  return new Response(reply.body, { status: reply.status, headers });
}

function json(status, data) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}
