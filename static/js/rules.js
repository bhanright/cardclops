// Rules (#/rules, under Tools): the Comprehensive Rules, downloaded from Wizards on first use
// (gallery/rules.py). Contents, one section at a time, search by rule number or words, the glossary,
// and rule numbers anywhere turned into links (#/rules?r=702.19b).
import { h, clear, int, spinner, errorBox, debounce } from './util.js';
import { api } from './api.js';

let overview = null;

/** Text with rule numbers ("702.19b", "rule 614") as links to that rule. */
export function linkRules(text) {
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(/\b(\d{3}\.\d+[a-z]?|(?<=rules? )\d{3})\b/g)) {
    parts.push(text.slice(last, m.index), h('a.rule-link', { href: `#/rules?r=${m[1]}` }, m[1]));
    last = m.index + m[1].length;
  }
  parts.push(text.slice(last));
  return parts;
}

export async function showRules() {
  const params = new URLSearchParams(location.hash.split('?').slice(1).join('?'));
  const host = document.getElementById('view-rules');
  if (!overview?.downloaded) {
    clear(host).append(spinner('Opening the rules…'));
    try { overview = await api.rules.overview(); } catch (error) { clear(host).append(errorBox(error.message)); return; }
  }
  if (!overview.downloaded) return offerDownload(host);
  const results = h('div.rules-results', { 'aria-live': 'polite' });
  const search = h('input.search-input', { type: 'search', placeholder: 'Rule number or words, e.g. 702.19  or  trample', 'aria-label': 'Search the rules', autocomplete: 'off' });
  const run = debounce(() => find(search.value, results), 300);
  search.addEventListener('input', run);
  const index = h('nav.rules-index', { 'aria-label': 'Contents' }, overview.chapters.map(chapter => h('details',
    h('summary', h('b', `${chapter.number}. ${chapter.title}`)),
    h('ul', chapter.sections.map(s => h('li', h('a', { href: `#/rules?s=${s.number}` }, `${s.number}. ${s.title}`)))))),
    h('details', h('summary', h('b', 'Glossary')), h('p', h('a', { href: '#/rules?g=1' }, `All ${int(overview.glossary_count)} terms`))));
  clear(host).append(h('section.panel.rules-page',
    h('div.panel-head', h('h2', 'Comprehensive Rules'),
      h('span.muted.small', `Effective ${overview.effective || '?'} · `, h('a', { href: overview.source_url || 'https://magic.wizards.com/en/rules', target: '_blank', rel: 'noopener' }, 'Wizards of the Coast ↗'))),
    search,
    h('div.rules-layout', index, results)));
  const r = params.get('r'), s = params.get('s'), g = params.get('g'), q = params.get('q');
  if (r) await openSection(r.slice(0, 3), results, r);          // the rule in its section, highlighted
  else if (s) await openSection(s, results);
  else if (g) await openGlossary(results);
  else if (q) { search.value = q; await find(q, results); }
  else clear(results).append(h('p.muted', 'Pick a section, or search. Rule numbers in card rulings link here.'));
}

function offerDownload(host) {
  const go = h('button.btn.go', { type: 'button', onclick: async () => {
    go.disabled = true;
    go.textContent = 'Downloading…';
    try { overview = await api.rules.download(); showRules(); }
    catch (error) { go.disabled = false; go.textContent = 'Try again'; host.append(errorBox(error.message)); }
  } }, '⇩ Download the rules (about 1 MB)');
  clear(host).append(h('section.panel.rules-page',
    h('h2', 'Comprehensive Rules'),
    h('p', 'The full rules of Magic, from Wizards of the Coast. Cardclops downloads the current edition from ',
      h('a', { href: 'https://magic.wizards.com/en/rules', target: '_blank', rel: 'noopener' }, 'magic.wizards.com/rules'),
      ' and checks for a newer one each month.'), go));
}

function ruleNode(rule, highlight) {
  const depth = /[a-z]$/.test(rule.number) ? 'sub' : 'main';
  return h('div', { class: `rule rule-${depth}${highlight === rule.number ? ' hit' : ''}`, id: `rule-${rule.number}` },
    h('a.rule-num', { href: `#/rules?r=${rule.number}` }, rule.number),
    h('div.rule-text', ...rule.text.split('\n').map(line => h('p', ...linkRules(line))),
      ...rule.examples.map(ex => h('p.rule-example', h('b', 'Example: '), ...linkRules(ex)))));
}

async function openSection(number, results, highlight = null) {
  clear(results).append(spinner('Turning pages…'));
  try {
    const section = await api.rules.section(number);
    clear(results).append(h('h3', `${section.number}. ${section.title}`), ...section.rules.map(r => ruleNode(r, highlight)));
    const target = highlight && document.getElementById(`rule-${highlight}`);
    (target || results).scrollIntoView({ block: target ? 'center' : 'start' });
  } catch (error) { clear(results).append(errorBox(error.message)); }
}

async function openGlossary(results) {
  clear(results).append(spinner('Opening the glossary…'));
  try {
    const { glossary } = await api.rules.glossary();
    clear(results).append(h('h3', 'Glossary'), ...glossary.map(glossaryNode));
  } catch (error) { clear(results).append(errorBox(error.message)); }
}

function glossaryNode(g) {
  return h('div.rule.glossary', h('b.rule-term', g.term), h('div.rule-text', ...g.text.split('\n').map(line => h('p', ...linkRules(line)))));
}

async function find(query, results, highlight = null) {
  query = query.trim();
  if (!query) { clear(results); return; }
  let found;
  try { found = await api.rules.search(query); } catch (error) { clear(results).append(errorBox(error.message)); return; }
  const total = found.rules.length + found.glossary.length;
  clear(results).append(
    h('p.muted.small', total ? `${int(total)} match${total === 1 ? '' : 'es'}${total >= 150 ? ' (first 150)' : ''}` : 'Nothing matches.'),
    ...found.glossary.map(glossaryNode),
    ...found.rules.map(r => ruleNode(r, highlight)));
  if (highlight) document.getElementById(`rule-${highlight}`)?.scrollIntoView({ block: 'center' });
}
