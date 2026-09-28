// Which Cardclops this page is talking to. The public edition runs the engine in the browser
// (docs/PUBLIC_EDITION_PLAN.md); its loader (engine/boot.js) marks the page before app.js starts.
// There, a few things work differently: the Ask box uses the visitor's own Anthropic API key, and
// features that need a server of one's own are left out.
import { store } from './util.js';

export const browserEdition = window.CARDCLOPS_EDITION === 'browser';

// The visitor's Anthropic API key for the Ask box. It stays in this browser's storage for this site
// and goes only to Anthropic, with each question (gallery/ask.py).
const ASK_KEY = 'cardclops.anthropicKey';
export const askKey = () => (browserEdition && store.get(ASK_KEY, '')) || '';
export function setAskKey(value) {
  try { if (value) localStorage.setItem(ASK_KEY, JSON.stringify(value)); else localStorage.removeItem(ASK_KEY); }
  catch { /* blocked storage: the key simply isn't kept */ }
}

/** The Ask box shows where it can answer: the apps with the Claude command-line tool (not on a
 *  phone), or the browser edition once a key is set. */
export const askShown = platform => (browserEdition ? Boolean(askKey()) : platform !== 'android');
