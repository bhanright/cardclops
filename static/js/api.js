// Thin wrappers over the JSON API in docs/API.md.

export class ApiError extends Error {}

async function request(path, { method = 'GET', body, signal } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    if (error.name === 'AbortError') throw error;
    throw new ApiError('Could not reach the gallery server. Is it still running?');
  }
  let data = null;
  try { data = await response.json(); } catch { /* non-JSON error page */ }
  if (!response.ok) throw new ApiError((data && data.error) || `Server answered ${response.status} for ${path}`);
  return data;
}

const qs = params => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value != null && value !== '') search.set(key, value);
  return search.toString();
};

export const api = {
  summary: () => request('/api/summary'),
  search: (params, signal) => request('/api/search?' + qs(params), { signal }),
  card: id => request('/api/card/' + encodeURIComponent(id) + '?holdings=card'),
  prices: id => request('/api/prices/' + encodeURIComponent(id)),
  portfolio: () => request('/api/portfolio'),
  movers: params => request('/api/movers?' + qs(params)),
  stats: () => request('/api/stats'),
  deckcheck: text => request('/api/deckcheck', { method: 'POST', body: { text } }),
  extras: params => request('/api/extras?' + qs(params)),
  ask: (question, signal) => request('/api/ask', { method: 'POST', body: { question }, signal }),
  tags: (q, signal) => request('/api/tags?' + qs({ q }), { signal }),
  legalityChanges: () => request('/api/legality-changes'),
  alerts: {
    list: params => request('/api/alerts?' + qs(params || {})),
    seen: body => request('/api/alerts/seen', { method: 'POST', body }),
    check: () => request('/api/alerts/check', { method: 'POST', body: {} }),
    settings: () => request('/api/alert-settings'),
    saveSettings: settings => request('/api/alert-settings', { method: 'PUT', body: settings }),
  },
  watchlist: {
    list: () => request('/api/watchlist'),
    add: item => request('/api/watchlist', { method: 'POST', body: item }),
    update: (id, fields) => request(`/api/watchlist/${id}`, { method: 'PATCH', body: fields }),
    remove: id => request(`/api/watchlist/${id}`, { method: 'DELETE' }),
  },
  sets: {
    list: params => request('/api/sets?' + qs(params || {})),
    get: (code, params) => request(`/api/sets/${encodeURIComponent(code)}?` + qs(params || {})),
    missingUrl: (code, params) => `/api/sets/${encodeURIComponent(code)}/missing.txt?` + qs(params || {}),
  },
  setup: {
    status: () => request('/api/setup/status'),
    progress: () => request('/api/setup/progress'),
    download: priceHistory => request('/api/setup/download', { method: 'POST', body: { price_history: priceHistory } }),
    import: (filename, text) => request('/api/setup/import', { method: 'POST', body: { filename, text } }),
    options: fields => request('/api/setup/options', { method: 'POST', body: fields }),
    complete: () => request('/api/setup/complete', { method: 'POST', body: {} }),
  },
  refresh: () => request('/api/refresh', { method: 'POST', body: {} }),
  quit: () => request('/api/quit', { method: 'POST', body: {} }),
  collection: {
    add: row => request('/api/collection/add', { method: 'POST', body: row }),
    manual: () => request('/api/collection/manual'),
    updateManual: (id, fields) => request(`/api/collection/manual/${id}`, { method: 'PATCH', body: fields }),
    removeManual: id => request(`/api/collection/manual/${id}`, { method: 'DELETE' }),
  },
  lookup: params => request('/api/cards/lookup?' + qs(params)),
  reprints: params => request('/api/reprints?' + qs(params || {})),
  build: {
    commanders: (params, signal) => request('/api/build/commanders?' + qs(params), { signal }),
    draft: (commander, partner) => request('/api/build/draft?' + qs({ commander, partner })),
  },
  decks: {
    list: () => request('/api/decks'),
    import: decks => request('/api/decks/import', { method: 'POST', body: { decks } }),
    get: id => request(`/api/decks/${id}`),
    update: (id, fields) => request(`/api/decks/${id}`, { method: 'PATCH', body: fields }),
    replaceList: (id, text) => request(`/api/decks/${id}/list`, { method: 'PUT', body: { text } }),
    remove: id => request(`/api/decks/${id}`, { method: 'DELETE' }),
    pin: (id, lineId, pool, quantity) => request(`/api/decks/${id}/pin`, { method: 'POST', body: { line_id: lineId, pool, quantity } }),
    valueHistory: id => request(`/api/decks/${id}/value-history`),
    suggestions: id => request(`/api/decks/${id}/suggestions`),
    sync: id => request(`/api/decks/${id}/sync`, { method: 'POST', body: {} }),
    importArchidekt: (urls, status) => request('/api/decks/import-archidekt', { method: 'POST', body: { urls, status } }),
    archidektUser: username => request('/api/archidekt/decks?' + qs({ username })),
    manafix: (id, maxSwaps = 6) => request(`/api/decks/${id}/manafix?` + qs({ max_swaps: maxSwaps })),
    copyPolicies: id => request(`/api/decks/${id}/copy-policies`),
    goldfish: (id, { games, turns, seed, play }) => request(`/api/decks/${id}/goldfish?` + qs({ games, turns, seed, play })),
    versions: id => request(`/api/decks/${id}/versions`),
    restoreVersion: (id, versionId) => request(`/api/decks/${id}/versions/${versionId}/restore`, { method: 'POST', body: {} }),
    manafixApply: (id, swaps) => request(`/api/decks/${id}/manafix/apply`, { method: 'POST', body: { swaps } }),
  },
};

/** Image URL for a card, e.g. img(id, 'front', 'small'). */
export const img = (id, face = 'front', size = 'normal') => `/img/${encodeURIComponent(id)}/${face}/${size}`;

/** Swap the size segment of an image path from the API ("/img/<id>/front/normal"). */
export const resize = (path, size) => (path ? path.replace(/\/(small|normal|large|art_crop|png)$/, '/' + size) : path);
