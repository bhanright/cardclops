// About, credits and privacy (#/about): the Fan Content notice Wizards of the Coast asks for, where
// the data comes from and on what terms, the licenses of the software Cardclops ships, and what
// leaves the device. The footer on every page (index.html) links here.
import { h, clear } from './util.js';
import { browserEdition } from './edition.js';

const link = (href, text) => h('a', { href, target: '_blank', rel: 'noopener' }, text);

export function showAbout() {
  const host = document.getElementById('view-about');
  clear(host).append(h('div.settings-page.about-page',
    h('section.panel',
      h('div.panel-head', h('h2', 'About Cardclops')),
      h('p', 'Cardclops is a free Magic: The Gathering collection manager: search your cards, track what they’re worth, build and check decks against what you own. There are no accounts, ads or payments.'),
      h('p.fan-notice', 'Cardclops is unofficial Fan Content permitted under the ', link('https://company.wizards.com/en/legal/fancontentpolicy', 'Fan Content Policy'),
        '. Not approved/endorsed by Wizards. Portions of the materials used are property of Wizards of the Coast. ©Wizards of the Coast LLC.'),
      h('p.small.muted', 'Magic: The Gathering, its card names, text, mana symbols and set symbols are trademarks and copyright of Wizards of the Coast. Card art is by the artists credited on each card.')),

    h('section.panel',
      h('div.panel-head', h('h2', 'Where the data comes from')),
      h('ul.pref-list',
        h('li', h('b', 'Card data, images, set symbols and rulings: '), link('https://scryfall.com', 'Scryfall'),
          '. Cardclops isn’t produced by or endorsed by Scryfall. Card images load from Scryfall’s image server, unaltered.'),
        h('li', h('b', 'Price history: '), link('https://mtgjson.com', 'MTGJSON'), ', under the MIT License (',
          h('a', { href: 'licenses/MTGJSON-LICENSE.txt', target: '_blank' }, 'its notice'), ').'),
        h('li', h('b', 'Combos and their bracket ratings: '), link('https://commanderspellbook.com', 'Commander Spellbook'),
          ', prepared about weekly from its published data and served by Cardclops. Cardclops isn’t affiliated with Commander Spellbook.'),
        h('li', h('b', 'Prices '), 'originally come from TCGplayer, Cardmarket, Card Kingdom and ManaPool, through Scryfall and MTGJSON. They’re estimates, a day old at best; no price here is an offer.'),
        h('li', h('b', 'The Comprehensive Rules and official rulings: '), 'Wizards of the Coast, from ', link('https://magic.wizards.com/en/rules', 'magic.wizards.com'), '.'),
        h('li', h('b', 'Links out '), 'to EDHREC, Archidekt, Moxfield and card shops are only links; Cardclops isn’t affiliated with them.'),
        browserEdition ? h('li', h('b', 'Card data on this site '), 'is prepared once a day from Scryfall’s published data and served by Cardclops, so each visitor doesn’t download all of it from Scryfall.') : null)),

    h('section.panel',
      h('div.panel-head', h('h2', 'Software')),
      h('ul.pref-list',
        browserEdition ? h('li', link('https://pyodide.org', 'Pyodide'), ' (Mozilla Public License 2.0; ', link('https://github.com/pyodide/pyodide', 'source'),
          '), which brings ', link('https://www.python.org', 'Python'), ' (PSF License) and ', link('https://sqlite.org', 'SQLite'), ' (public domain) to the browser.') : null,
        !browserEdition ? h('li', link('https://www.python.org', 'Python'), ' (PSF License) and ', link('https://sqlite.org', 'SQLite'), ' (public domain); ',
          link('https://github.com/certifi/python-certifi', 'certifi'), ' (Mozilla Public License 2.0). The Windows app adds ', link('https://pywebview.flowrl.com', 'pywebview'),
          ' (BSD 3-Clause) and ', link('https://pyinstaller.org', 'PyInstaller'), '’s loader (GPL 2.0 with its distribution exception); the Android app, ',
          link('https://chaquo.com/chaquopy/', 'Chaquopy'), ' (MIT) and AndroidX (Apache 2.0).') : null,
        h('li', 'The fonts ', link('https://fonts.google.com/specimen/Lilita+One', 'Lilita One'), ' and ', link('https://fonts.google.com/specimen/Rubik', 'Rubik'),
          ', under the SIL Open Font License 1.1 (', h('a', { href: 'fonts/OFL-LilitaOne.txt', target: '_blank' }, 'Lilita One'), ', ',
          h('a', { href: 'fonts/OFL-Rubik.txt', target: '_blank' }, 'Rubik'), '), served with Cardclops.'),
        h('li', 'The optional Ask box uses ', link('https://www.anthropic.com', 'Anthropic'), '’s Claude.'))),

    h('section.panel',
      h('div.panel-head', h('h2', 'Privacy')),
      browserEdition ? h('ul.pref-list',
        h('li', h('b', 'Your collection, decks and settings stay in this browser. '), 'Imports are read here; nothing you import is uploaded. There are no accounts, analytics or ads, and Cardclops sets no cookies.'),
        h('li', h('b', 'What this page requests: '), 'the site and its card data from cardclops.com (hosted by Cloudflare, which keeps ordinary server logs); details and rulings for your cards from Scryfall’s API, by card id; card images and set symbols from Scryfall.'),
        h('li', h('b', 'The Ask box, only if you add your own key, '), 'sends your question and a list of card-function tags to Anthropic’s API with that key. The key stays in this browser.'),
        h('li', h('b', 'Clearing this site’s data in your browser deletes your collection here, '), 'so keep a backup (Settings → Backup).'))
        : h('ul.pref-list',
          h('li', h('b', 'Your collection, decks and settings stay on this device '), '(or on your own server). Nothing you import is uploaded anywhere.'),
          h('li', h('b', 'What it downloads: '), 'card data and prices from Scryfall and MTGJSON, card details and rulings from Scryfall’s API, card images as you look at them, and Commander Spellbook’s combos from cardclops.com.'),
          h('li', h('b', 'The Ask box '), 'sends your question and a list of card-function tags to Claude.')))));
  document.title = 'About · Cardclops';
}
