// Hand-drawn SVG/HTML charts: a time-series panel with crosshair, bar lists, stacked bars, column charts.
import { h, svg, clear, money, moneyShort, int, changeChip } from './util.js';

const DAY = 86400000;
let hatchCounter = 0;
const DAILY_GAP = 3;            // days; a longer step between points is a monthly close
const RANGES = [['1M', 31], ['3M', 92], ['1Y', 366], ['5Y', 1827], ['All', Infinity]];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const parseDay = day => Date.parse(day + 'T00:00:00Z');
function formatDay(ms, withYear = true) {
  const d = new Date(ms);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}` + (withYear ? `, ${d.getUTCFullYear()}` : '');
}

function niceTicks(min, max, count = 4) {
  const span = max - min || Math.abs(max) || 1;
  const raw = span / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(m => m * magnitude).find(s => s >= raw) || raw;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

/**
 * Time-series panel: range buttons, optional series selector, line chart with crosshair + tooltip,
 * and a daily-change bar strip. series = [{label, points: [[day, value], ...]}].
 */
export function timeSeriesPanel(container, { series, defaultIndex = 0, height = 220, format = money, axisFormat = moneyShort, bars = true, emptyNote }) {
  clear(container);
  container.classList.add('ts-panel');
  const usable = (series || []).filter(s => s.points && s.points.length);
  if (!usable.length) {
    container.append(h('div.chart-empty', emptyNote || 'No price data yet.'));
    return;
  }
  let seriesIndex = Math.min(Math.max(0, usable.indexOf((series || [])[defaultIndex])), usable.length - 1);
  if (seriesIndex < 0) seriesIndex = 0;
  const spanDays = s => (parseDay(s.points.at(-1)[0]) - parseDay(s.points[0][0])) / DAY;
  let range = spanDays(usable[seriesIndex]) > 400 ? '1Y' : 'All';

  const controls = h('div.chart-controls');
  const rangeGroup = h('div.seg', { role: 'group', 'aria-label': 'Time range' });
  for (const [label] of RANGES) {
    rangeGroup.append(h('button.seg-btn', { type: 'button', dataset: { range: label }, onclick: () => { range = label; draw(); } }, label));
  }
  controls.append(rangeGroup);
  if (usable.length > 1) {
    const select = h('select.select', { 'aria-label': 'Price series', onchange: e => { seriesIndex = +e.target.value; draw(); } },
      usable.map((s, i) => h('option', { value: i, selected: i === seriesIndex }, s.label)));
    controls.append(select);
  }
  const plot = h('div.chart-plot');
  const tooltip = h('div.chart-tip', { hidden: true });
  const stripWrap = h('div.chart-strip');
  container.append(controls, h('div.chart-stage', plot, stripWrap, tooltip));

  let lastWidth = 0;
  const observer = new ResizeObserver(() => {
    if (!container.isConnected) { observer.disconnect(); return; }
    if (Math.abs(container.clientWidth - lastWidth) > 2) draw();
  });
  observer.observe(container);

  function draw() {
    lastWidth = container.clientWidth;
    const fmt = usable[seriesIndex].format || format;
    for (const b of rangeGroup.children) b.classList.toggle('on', b.dataset.range === range);
    const all = usable[seriesIndex].points.map(([d, v]) => [parseDay(d), v, d]).filter(p => p[1] != null);
    const end = all.at(-1)[0];
    const days = RANGES.find(r => r[0] === range)[1];
    let pts = all.filter(p => p[0] >= end - days * DAY);
    if (!pts.length) pts = all.slice(-1);
    clear(plot); clear(stripWrap); tooltip.hidden = true;

    if (all.length === 1) {
      plot.append(h('div.chart-single',
        h('div.big-number', fmt(all[0][1])),
        h('div', `Only one day recorded (${formatDay(all[0][0])}).`),
        h('div.muted', emptyNote || 'History starts today; it grows each refresh.')));
      return;
    }

    const W = Math.max(260, plot.clientWidth || container.clientWidth);
    const H = height;
    const pad = { l: 54, r: 14, t: 14, b: 26 };
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    let x0 = xs[0], x1 = xs.at(-1);
    if (x0 === x1) { x0 -= DAY; x1 += DAY; }
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    const yPad = (y1 - y0) * 0.1 || Math.abs(y1) * 0.1 || 1;
    y0 = Math.max(0, y0 - yPad); y1 += yPad;
    const sx = x => pad.l + ((x - x0) / (x1 - x0)) * (W - pad.l - pad.r);
    const sy = y => pad.t + (1 - (y - y0) / (y1 - y0)) * (H - pad.t - pad.b);

    const root = svg('svg', { class: 'chart-svg', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': 'Price over time' });
    for (const t of niceTicks(y0, y1)) {
      if (t < y0 || t > y1) continue;
      root.append(svg('line', { x1: pad.l, x2: W - pad.r, y1: sy(t), y2: sy(t), class: 'grid' }),
        svg('text', { x: pad.l - 8, y: sy(t) + 4, class: 'axis', 'text-anchor': 'end' }, (usable[seriesIndex].axisFormat || axisFormat)(t)));
    }
    const spanAll = (x1 - x0) / DAY;
    const tickCount = Math.max(2, Math.min(6, Math.floor(W / 110)));
    for (let i = 0; i < tickCount; i++) {
      const t = x0 + ((x1 - x0) * i) / (tickCount - 1);
      const d = new Date(t);
      const label = spanAll > 300 ? `${MONTHS[d.getUTCMonth()]} ’${String(d.getUTCFullYear()).slice(2)}` : formatDay(t, false);
      root.append(svg('text', { x: sx(t), y: H - 6, class: 'axis', 'text-anchor': i === 0 ? 'start' : i === tickCount - 1 ? 'end' : 'middle' }, label));
    }
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join('');
    const area = `${line}L${sx(pts.at(-1)[0]).toFixed(1)},${H - pad.b}L${sx(pts[0][0]).toFixed(1)},${H - pad.b}Z`;
    const rising = pts.at(-1)[1] >= pts[0][1];
    const hatchId = 'hatch-' + (++hatchCounter);
    root.append(
      svg('defs', {}, svg('pattern', { id: hatchId, width: 8, height: 8, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(40)' },
        svg('line', { x1: 0, y1: 0, x2: 0, y2: 8, class: 'hatch ' + (rising ? 'up' : 'down') }))),
      svg('path', { d: area, class: 'area ' + (rising ? 'up' : 'down') }),
      svg('path', { d: area, fill: `url(#${hatchId})` }),
      svg('path', { d: line, class: 'ink-line' }),
      svg('path', { d: line, class: 'line ' + (rising ? 'up' : 'down') }),
    );
    // History older than ~30 days is thinned to one closing point per month: mark those points so
    // the sparse stretch reads as monthly closes rather than a smooth daily line.
    const gapBefore = i => (i ? (pts[i][0] - pts[i - 1][0]) / DAY : 0);
    const isSparse = i => gapBefore(i) > DAILY_GAP || (i + 1 < pts.length && gapBefore(i + 1) > DAILY_GAP);
    if (pts.length < 80 || pts.some((_, i) => gapBefore(i) > DAILY_GAP)) {
      pts.forEach((p, i) => { if (isSparse(i) || pts.length < 40) root.append(svg('circle', { class: 'pt', cx: sx(p[0]).toFixed(1), cy: sy(p[1]).toFixed(1), r: 3.5 })); });
    }
    const cross = svg('line', { class: 'cross', y1: pad.t, y2: H - pad.b, visibility: 'hidden' });
    const dot = svg('circle', { class: 'dot', r: 6, visibility: 'hidden' });
    root.append(cross, dot);
    plot.append(root);

    // Change strip: one bar per daily step (gap of a few days at most). Monthly steps get no bar,
    // just a hatched band, because a month's move and a day's move don't share a scale.
    let barRects = [];
    let strip = null;
    const changes = pts.map((p, i) => (i ? p[1] - pts[i - 1][1] : 0));
    const daily = changes.map((_, i) => i > 0 && gapBefore(i) <= DAILY_GAP);
    if (bars && daily.some(Boolean)) {
      const SH = 54, mid = SH / 2;
      const maxAbs = Math.max(...changes.filter((_, i) => daily[i]).map(Math.abs)) || 1;
      strip = svg('svg', { class: 'strip-svg', viewBox: `0 0 ${W} ${SH}`, width: W, height: SH, 'aria-hidden': 'true' });
      const firstDaily = daily.indexOf(true);
      const dailyFrom = pts[firstDaily - 1][0];
      if (dailyFrom > pts[0][0]) {
        strip.append(svg('rect', { class: 'monthly-band', x: sx(pts[0][0]).toFixed(1), y: 4, width: Math.max(0, sx(dailyFrom) - sx(pts[0][0])).toFixed(1), height: SH - 8, rx: 6, fill: `url(#${hatchId})` }));
        if (sx(dailyFrom) - sx(pts[0][0]) > 120) strip.append(svg('text', { x: (sx(pts[0][0]) + sx(dailyFrom)) / 2, y: mid + 4, class: 'axis band-label', 'text-anchor': 'middle' }, 'monthly closes'));
      }
      strip.append(svg('line', { x1: sx(dailyFrom), x2: W - pad.r, y1: mid, y2: mid, class: 'baseline' }));
      const dailyCount = daily.filter(Boolean).length;
      const step = (sx(pts.at(-1)[0]) - sx(dailyFrom)) / Math.max(1, dailyCount);
      const bw = Math.max(1, Math.min(14, step * 0.7));
      barRects = changes.map((c, i) => {
        if (!daily[i]) return null;
        const hgt = Math.max(1, (Math.abs(c) / maxAbs) * (mid - 3));
        const r = svg('rect', { x: (sx(pts[i][0]) - bw / 2).toFixed(1), y: c >= 0 ? mid - hgt : mid, width: bw.toFixed(1), height: hgt.toFixed(1), class: c > 0 ? 'up' : c < 0 ? 'down' : 'flat' });
        strip.append(r);
        return r;
      });
      strip.append(svg('text', { x: 4, y: 12, class: 'axis' }, 'daily ▲▼'));
      stripWrap.append(strip);
    }

    let active = -1;
    const move = event => {
      const box = root.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * W;
      const t = x0 + ((x - pad.l) / (W - pad.l - pad.r)) * (x1 - x0);
      let lo = 0, hi = pts.length - 1;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (pts[m][0] < t) lo = m; else hi = m; }
      const i = Math.abs(pts[lo][0] - t) <= Math.abs(pts[hi][0] - t) ? lo : hi;
      if (i === active) return;
      if (barRects[active]) barRects[active].classList.remove('hot');
      active = i;
      if (barRects[i]) barRects[i].classList.add('hot');
      const px = sx(pts[i][0]), py = sy(pts[i][1]);
      cross.setAttribute('x1', px); cross.setAttribute('x2', px); cross.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', px); dot.setAttribute('cy', py); dot.setAttribute('visibility', 'visible');
      const prev = i ? pts[i - 1][1] : null;
      const diff = prev != null ? pts[i][1] - prev : null;
      const gap = gapBefore(i);
      const since = gap > 1.5 ? `since ${formatDay(pts[i - 1][0], new Date(pts[i - 1][0]).getUTCFullYear() !== new Date(pts[i][0]).getUTCFullYear())}` : 'vs. previous day';
      clear(tooltip).append(
        h('div.tip-date', formatDay(pts[i][0]), gap > DAILY_GAP ? h('span.tip-kind', ' · monthly close') : ''),
        h('div.tip-price', fmt(pts[i][1])),
        diff == null ? h('div.muted', 'first point in range')
          : h('div.tip-change', h('span', { class: 'chg ' + (diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat') },
            (diff > 0 ? '▲ +' : diff < 0 ? '▼ −' : '● ') + fmt(Math.abs(diff))), ' ', changeChip(prev ? (diff / prev) * 100 : null, { small: true }),
            h('div.muted', since)),
      );
      tooltip.hidden = false;
      const stage = tooltip.parentElement.getBoundingClientRect();
      const left = (px / W) * box.width + (box.left - stage.left);
      const tipW = tooltip.offsetWidth;
      tooltip.style.left = Math.min(stage.width - tipW - 4, Math.max(4, left + (left > stage.width / 2 ? -tipW - 14 : 14))) + 'px';
      tooltip.style.top = Math.max(4, (py / H) * box.height - 30) + 'px';
    };
    const leave = () => {
      cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden');
      if (barRects[active]) barRects[active].classList.remove('hot');
      active = -1; tooltip.hidden = true;
    };
    for (const target of [root, strip].filter(Boolean)) {
      target.addEventListener('pointermove', move);
      target.addEventListener('pointerdown', move);
      target.addEventListener('pointerleave', leave);
    }
  }
  draw();
}

/** Horizontal bar list: [{label, value, color, title, onclick}] */
export function barList(items, { format = int, max } = {}) {
  const top = max ?? Math.max(1, ...items.map(i => i.value));
  return h('div.barlist', items.map(item => {
    const row = h(item.onclick ? 'button.bar-row.clickable' : 'div.bar-row', { type: item.onclick ? 'button' : null, title: item.title, onclick: item.onclick },
      h('span.bar-label', item.label),
      h('span.bar-track', h('span.bar-fill', { style: { width: `${(item.value / top) * 100}%`, background: item.color || 'var(--cyan)' } })),
      h('span.bar-value', format(item.value)));
    return row;
  }));
}

/** One stacked horizontal bar + legend: [{key, label, value, color}] */
export function stackBar(items, { format = int } = {}) {
  const total = items.reduce((s, i) => s + i.value, 0) || 1;
  const shown = items.filter(i => i.value > 0);
  return h('div.stack',
    h('div.stack-bar', { role: 'img', 'aria-label': shown.map(i => `${i.label} ${i.value}`).join(', ') },
      shown.map(i => h('span.stack-seg', { style: { flexGrow: i.value, background: i.color }, title: `${i.label}: ${format(i.value)} (${((i.value / total) * 100).toFixed(1)}%)` },
        i.value / total > 0.07 ? i.short || i.key : ''))),
    h('div.stack-legend', shown.map(i => h('span.legend-item', h('span.swatch', { style: { background: i.color } }), `${i.label} `, h('b', format(i.value))))));
}

/** Vertical column chart in SVG: [{label, value, color, tip}] with hover tooltips. */
export function columnChart(items, { height = 140, format = int, labelEvery = 1, minColWidth = 0 } = {}) {
  const wrap = h('div.columns');
  const tip = h('div.chart-tip', { hidden: true });
  const max = Math.max(1, ...items.map(i => i.value));
  const n = items.length || 1;
  const W = Math.max(320, n * Math.max(minColWidth, 12));
  const pad = { t: 16, b: 22 };
  const cw = W / n;
  const root = svg('svg', { class: 'col-svg', viewBox: `0 0 ${W} ${height}`, preserveAspectRatio: 'none', width: '100%', height, role: 'img' });
  const labels = h('div.col-labels');
  items.forEach((item, i) => {
    const hgt = (item.value / max) * (height - pad.t - pad.b);
    const x = i * cw + cw * 0.14;
    const rect = svg('rect', { x, y: height - pad.b - hgt, width: cw * 0.72, height: Math.max(item.value ? 2 : 0, hgt), rx: Math.min(6, cw * 0.2), class: 'col', fill: item.color || 'var(--cyan)' });
    const hit = svg('rect', { x: i * cw, y: 0, width: cw, height, fill: 'transparent' });
    hit.addEventListener('pointerenter', () => {
      rect.classList.add('hot');
      clear(tip).append(h('div.tip-date', item.label), h('div.tip-price', format(item.value)), item.tip ? h('div.muted', item.tip) : '');
      tip.hidden = false;
      const box = wrap.getBoundingClientRect();
      const left = ((i + 0.5) / n) * box.width;
      tip.style.left = Math.min(box.width - tip.offsetWidth - 2, Math.max(2, left - tip.offsetWidth / 2)) + 'px';
      tip.style.top = '-8px';
    });
    hit.addEventListener('pointerleave', () => { rect.classList.remove('hot'); tip.hidden = true; });
    root.append(rect, hit);
  });
  root.append(svg('line', { x1: 0, x2: W, y1: height - pad.b, y2: height - pad.b, class: 'baseline' }));
  items.forEach((item, i) => {
    labels.append(h('span', { style: { width: `${100 / n}%` } }, i % labelEvery === 0 ? item.label : ''));
  });
  wrap.append(root, labels, tip);
  return wrap;
}

/**
 * Several series over turns 1..N on one set of axes, with dots, a legend and a hover readout.
 * series = [{label, color, values: {"1": 1.0, ...}}]. Redraws on resize.
 */
export function turnChart(container, { series, height = 200, format = v => v.toFixed(1), yMax } = {}) {
  clear(container);
  container.classList.add('turn-chart');
  const plot = h('div.tc-plot');
  const readout = h('div.tc-readout', { 'aria-live': 'polite' }, 'Hover or tap a turn for exact numbers.');
  container.append(
    h('div.stack-legend', series.map(s => h('span.legend-item', h('span.swatch', { style: { background: s.color } }), s.label))),
    plot, readout);
  const turns = [...new Set(series.flatMap(s => Object.keys(s.values || {})))].map(Number).sort((a, b) => a - b);
  let lastWidth = 0;
  const draw = () => {
    const W = Math.max(260, plot.clientWidth || container.clientWidth || 500);
    lastWidth = W;
    const H = height, pad = { l: 38, r: 12, t: 12, b: 26 };
    const max = yMax ?? Math.max(1, ...series.flatMap(s => Object.values(s.values || {})));
    const sx = t => pad.l + ((t - turns[0]) / Math.max(1, turns.at(-1) - turns[0])) * (W - pad.l - pad.r);
    const sy = v => pad.t + (1 - v / max) * (H - pad.t - pad.b);
    const root = svg('svg', { class: 'chart-svg', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img',
      'aria-label': series.map(s => `${s.label}: ` + turns.map(t => `T${t} ${format(s.values[t] ?? 0)}`).join(', ')).join('; ') });
    for (const t of niceTicks(0, max)) {
      if (t > max) continue;
      root.append(svg('line', { x1: pad.l, x2: W - pad.r, y1: sy(t), y2: sy(t), class: 'grid' }),
        svg('text', { x: pad.l - 6, y: sy(t) + 4, class: 'axis', 'text-anchor': 'end' }, format(t)));
    }
    for (const t of turns) root.append(svg('text', { x: sx(t), y: H - 6, class: 'axis', 'text-anchor': 'middle' }, 'T' + t));
    for (const s of series) {
      const d = turns.map((t, i) => `${i ? 'L' : 'M'}${sx(t).toFixed(1)},${sy(s.values[t] ?? 0).toFixed(1)}`).join('');
      root.append(svg('path', { d, class: 'ink-line' }), svg('path', { d, fill: 'none', stroke: s.color, 'stroke-width': 3.2, 'stroke-linejoin': 'round', 'stroke-dasharray': s.dash || null }));
      for (const t of turns) root.append(svg('circle', { cx: sx(t), cy: sy(s.values[t] ?? 0), r: 4, fill: s.color, stroke: '#000', 'stroke-width': 2 }));
    }
    const cross = svg('line', { class: 'cross', y1: pad.t, y2: H - pad.b, visibility: 'hidden' });
    root.append(cross);
    const pick = event => {
      const box = root.getBoundingClientRect();
      const x = ((event.clientX - box.left) / box.width) * W;
      const t = turns.reduce((best, tt) => (Math.abs(sx(tt) - x) < Math.abs(sx(best) - x) ? tt : best), turns[0]);
      cross.setAttribute('x1', sx(t)); cross.setAttribute('x2', sx(t)); cross.setAttribute('visibility', 'visible');
      clear(readout).append(h('b', `Turn ${t}: `), ...series.map((s, i) => h('span', i ? ' · ' : '', h('span.swatch', { style: { background: s.color } }), ` ${s.label} ${format(s.values[t] ?? 0)}`)));
    };
    root.addEventListener('pointermove', pick);
    root.addEventListener('pointerdown', pick);
    clear(plot).append(root);
  };
  const observer = new ResizeObserver(() => {
    if (!container.isConnected) { observer.disconnect(); return; }
    if (Math.abs((plot.clientWidth || 0) - lastWidth) > 2) draw();
  });
  observer.observe(container);
  requestAnimationFrame(draw);
}
