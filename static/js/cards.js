// Card tiles and thumbnails shared by every view, plus the lazy image loader.
import { h, money, changeChip, finishLabel, int } from './util.js';
import { handMark } from './addcards.js';
import { resize } from './api.js';

/** One observer for every lazy <img data-src>: images load only as they near the viewport. */
const lazy = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const img = entry.target;
    lazy.unobserve(img);
    if (img.dataset.src) { img.src = img.dataset.src; delete img.dataset.src; }
  }
}, { rootMargin: '500px 200px' });

export function lazyImg(src, alt, className = 'card-img') {
  const img = h('img', { class: className, alt, decoding: 'async', draggable: 'false', dataset: { src } });
  img.addEventListener('load', () => img.classList.add('loaded'));
  img.addEventListener('error', () => img.classList.add('broken'));
  lazy.observe(img);
  return img;
}
export const unobserve = img => img && lazy.unobserve(img);

export const rarityLetter = r => (r ? r[0].toUpperCase() : '?');
export function rarityGem(rarity) {
  return h('span', { class: `gem r-${rarity || 'common'}`, title: rarity || '' }, rarityLetter(rarity));
}

function finishTag(finish) {
  if (finish === 'foil') return h('span.finish-tag.foil', { title: 'Foil' }, '✦ Foil');
  if (finish === 'etched') return h('span.finish-tag.etched', { title: 'Etched foil' }, '✦ Etched');
  if (finish === 'mixed') return h('span.finish-tag.mixed', { title: 'Several finishes' }, 'Mixed');
  return null;
}

/** Face of a card image with foil sheen and DFC flip. Returns {node, img}. */
export function cardFace(card, { size = 'normal', flip = true, eager = false } = {}) {
  const front = resize(card.image, size);
  const back = card.image_back ? resize(card.image_back, size) : null;
  const img = eager ? h('img.card-img.loaded', { src: front, alt: card.name, decoding: 'async' }) : lazyImg(front, card.name);
  const face = h('div.card-face' + (card.finish === 'foil' || card.finish === 'etched' ? '.is-foil' : ''), img,
    card.finish === 'foil' || card.finish === 'etched' ? h('span.foil-sheen', { 'aria-hidden': 'true' }) : null);
  if (back && flip) {
    let showingBack = false;
    face.append(h('button.flip-btn', { type: 'button', title: 'Flip card', 'aria-label': `Flip ${card.name}`, onclick: event => {
      event.stopPropagation();
      showingBack = !showingBack;
      face.classList.add('flipping');
      setTimeout(() => {
        img.src = showingBack ? back : front;
        face.classList.remove('flipping');
      }, 140);
    } }, '⟲'));
  }
  return { node: face, img };
}

/** "In decks" marker: copies held by active decks; says so plainly when none are spare. */
export function usedBadge(card) {
  if (!card.used) return null;
  const allUsed = card.spare === 0;
  return h('span', { class: 'used-tag' + (allUsed ? ' all' : ''),
    title: `${card.used} ${card.used === 1 ? 'copy is' : 'copies are'} in active decks${card.spare != null ? `; ${card.spare} spare` : ''}` },
  allUsed ? `▣ all in decks` : `▣ ${card.used} in decks`);
}

/** Full gallery tile. */
export function cardTile(card, { onOpen, changeKey = 'd7' } = {}) {
  const { node: face, img } = cardFace(card);
  const change = card.change ? card.change[changeKey] : null;
  const tile = h('div', { class: `tile r-${card.rarity || 'common'}` },
    h('button.tile-hit', { type: 'button', 'aria-label': `${card.name} — ${card.set_name || card.set_code}, ${finishLabel(card.finish)}, ${card.quantity} copies`, onclick: onOpen }),
    face,
    card.quantity > 1 ? h('span.qty', { title: `${card.quantity} copies` }, '×' + int(card.quantity)) : null,
    finishTag(card.finish),
    usedBadge(card),
    h('div.tile-info',
      h('div.tile-name', { title: card.name }, card.name),
      h('div.tile-meta',
        rarityGem(card.rarity),
        handMark(card.source),
        h('span.set-code', card.printings > 1 && card.row_id == null ? `${card.printings} prints` : (card.set_code || '').toUpperCase()),
        h('span.price', money(card.price_usd)),
        changeChip(change, { small: true }))));
  tile.img = img;
  return tile;
}

/** Small clickable thumbnail used in rows (other printings, similar, deck check…). */
export function thumb(card, { onOpen, caption } = {}) {
  const { node: face } = cardFace(card, { size: 'small', flip: false });
  return h('button.thumb', { type: 'button', title: `${card.name} (${(card.set_code || '').toUpperCase()} #${card.collector_number || ''})`, onclick: onOpen },
    face,
    card.quantity > 1 ? h('span.qty.small', '×' + card.quantity) : null,
    h('span.thumb-cap', caption ?? [(card.set_code || '').toUpperCase(), ' · ', money(card.price_usd)]));
}

/** Floating preview image that follows the pointer (list views on desktop). */
let preview;
export function attachHoverPreview(node, card) {
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches) return;
  node.addEventListener('pointerenter', event => {
    if (!preview) { preview = h('img.hover-preview', { alt: '' }); document.body.append(preview); }
    preview.src = card.image;
    preview.hidden = false;
    place(event);
  });
  node.addEventListener('pointermove', place);
  node.addEventListener('pointerleave', () => { if (preview) preview.hidden = true; });
}
function place(event) {
  if (!preview) return;
  const w = 244, hgt = 340;
  let x = event.clientX + 24, y = event.clientY - hgt / 2;
  if (x + w > innerWidth - 8) x = event.clientX - w - 24;
  y = Math.max(8, Math.min(innerHeight - hgt - 8, y));
  preview.style.transform = `translate(${x}px, ${y}px)`;
}
export function hideHoverPreview() { if (preview) preview.hidden = true; }
