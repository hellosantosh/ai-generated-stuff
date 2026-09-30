/* Hand-rolled SVG charts.
 *
 * The rest of this UI has no build step and no dependencies, and two chart
 * forms do not justify breaking that. Both are drawn into a fixed 1000-unit
 * viewBox and scaled by CSS, so the layout maths stays in one coordinate
 * system and the type scales with the figure.
 *
 * Colors come from CSS custom properties defined in app.css, which is where
 * the light and dark steps of the validated palette live. Nothing here picks
 * a hue; callers pass the role they want.
 */
'use strict';

(function (global) {
  const NS = 'http://www.w3.org/2000/svg';
  const VB = { w: 1000, h: 420 };

  const el = (name, attrs = {}) => {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) {
      if (value !== null && value !== undefined) node.setAttribute(key, String(value));
    }
    return node;
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const parseDate = (iso) => new Date(`${iso}T12:00:00`).getTime();

  /* Axis ticks on round numbers. A tick at 137.4 tells the reader nothing that
     a tick at 140 does not. */
  function niceTicks(min, max, count = 5) {
    if (!(max > min)) return [min];
    const raw = (max - min) / count;
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const candidates = [1, 2, 2.5, 5, 10].map(m => m * magnitude);
    const step = candidates.find(c => c >= raw) || candidates[candidates.length - 1];
    const first = Math.ceil(min / step) * step;
    const ticks = [];
    for (let v = first; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
    return ticks;
  }

  function dateLabel(ms, span) {
    const d = new Date(ms);
    if (span > 3 * 365 * 86400000) return String(d.getFullYear());
    return d.toLocaleDateString(undefined, { month: 'short', year: '2-digit' });
  }

  function shell(container) {
    container.innerHTML = '';
    container.classList.add('chart');
    const svg = el('svg', {
      viewBox: `0 0 ${VB.w} ${VB.h}`,
      role: 'img',
      preserveAspectRatio: 'xMidYMid meet',
    });
    container.appendChild(svg);
    const tip = document.createElement('div');
    tip.className = 'chart-tip';
    tip.hidden = true;
    container.appendChild(tip);
    return { svg, tip };
  }

  /* ---------- line chart ---------- */
  /* series: [{ label, color, points: [[iso, value], ...], width?, dashed? }] */
  function line(container, options) {
    const { series = [], baseline = null, baselineLabel = '', format = (v) => v.toFixed(1) } = options;
    const { svg, tip } = shell(container);
    const live = series.filter(s => s.points && s.points.length > 1);
    if (!live.length) {
      container.innerHTML = '<p class="empty">Nothing to plot for this window.</p>';
      return;
    }

    const m = { t: 18, r: series.length <= 4 ? 108 : 20, b: 34, l: 54 };
    const plot = { w: VB.w - m.l - m.r, h: VB.h - m.t - m.b };

    const prepared = live.map(s => ({
      ...s,
      data: s.points.map(([iso, value]) => ({ t: parseDate(iso), v: Number(value), iso })),
    }));
    const allT = prepared.flatMap(s => s.data.map(d => d.t));
    const allV = prepared.flatMap(s => s.data.map(d => d.v)).filter(Number.isFinite);
    const t0 = Math.min(...allT), t1 = Math.max(...allT);
    let vMin = Math.min(...allV), vMax = Math.max(...allV);
    if (baseline !== null) { vMin = Math.min(vMin, baseline); vMax = Math.max(vMax, baseline); }
    const pad = (vMax - vMin) * 0.06 || 1;
    vMin -= pad; vMax += pad;

    const x = (t) => m.l + (t1 === t0 ? 0 : (t - t0) / (t1 - t0)) * plot.w;
    const y = (v) => m.t + (1 - (v - vMin) / (vMax - vMin)) * plot.h;

    // grid and y axis
    niceTicks(vMin, vMax).forEach(value => {
      svg.appendChild(el('line', {
        x1: m.l, x2: m.l + plot.w, y1: y(value), y2: y(value),
        stroke: 'var(--rule)', 'stroke-width': 1,
      }));
      const label = el('text', {
        x: m.l - 8, y: y(value) + 4, 'text-anchor': 'end',
        class: 'ax', fill: 'var(--text-muted)', 'font-size': 12,
      });
      label.textContent = format(value);
      svg.appendChild(label);
    });

    // x axis
    const span = t1 - t0;
    for (let i = 0; i <= 5; i++) {
      const t = t0 + (span * i) / 5;
      const label = el('text', {
        x: x(t), y: VB.h - 10, 'text-anchor': i === 0 ? 'start' : i === 5 ? 'end' : 'middle',
        fill: 'var(--text-muted)', 'font-size': 12,
      });
      label.textContent = dateLabel(t, span);
      svg.appendChild(label);
    }

    if (baseline !== null) {
      svg.appendChild(el('line', {
        x1: m.l, x2: m.l + plot.w, y1: y(baseline), y2: y(baseline),
        stroke: 'var(--text-secondary)', 'stroke-width': 1.5, 'stroke-dasharray': '5 4',
      }));
    }

    prepared.forEach(s => {
      const d = s.data
        .filter(p => Number.isFinite(p.v))
        .map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`)
        .join(' ');
      svg.appendChild(el('path', {
        d, fill: 'none', stroke: s.color, 'stroke-width': s.width || 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round',
        'stroke-dasharray': s.dashed ? '6 4' : null,
      }));
    });

    // Direct labels: with four or fewer lines identity never depends on the
    // legend, which is where the light-mode contrast relief comes from. Lines
    // that end close together would print on top of each other, so the labels
    // are laid out in one pass and pushed apart.
    if (prepared.length <= 4) {
      const GAP = 17;
      const labels = prepared
        .map(s => ({ label: s.label, color: s.color, y: y(s.data[s.data.length - 1].v) }));
      // The baseline label lives in the same margin, so it joins the same
      // layout pass rather than being drawn over the data.
      if (baseline !== null && baselineLabel) {
        labels.push({ label: baselineLabel, color: 'var(--text-secondary)', y: y(baseline), small: true });
      }
      labels.sort((a, b) => a.y - b.y);
      labels.forEach((label, i) => {
        if (i > 0) label.y = Math.max(label.y, labels[i - 1].y + GAP);
      });
      // If pushing down ran past the bottom, push the whole stack back up.
      const overflow = labels.length
        ? labels[labels.length - 1].y - (m.t + plot.h) : 0;
      if (overflow > 0) labels.forEach(label => { label.y -= overflow; });
      labels.forEach(({ label, color, y: ly, small }) => {
        const text = el('text', {
          x: m.l + plot.w + 8, y: Math.max(m.t + 10, ly) + 4, fill: color,
          'font-size': small ? 11.5 : 13, 'font-weight': small ? 500 : 600,
        });
        text.textContent = label;
        svg.appendChild(text);
      });
    }

    // hover layer
    const crosshair = el('line', {
      y1: m.t, y2: m.t + plot.h, stroke: 'var(--text-muted)', 'stroke-width': 1,
      'stroke-dasharray': '3 3', visibility: 'hidden',
    });
    svg.appendChild(crosshair);
    const dots = prepared.map(s => {
      const dot = el('circle', {
        r: 4.5, fill: s.color, stroke: 'var(--surface)', 'stroke-width': 2, visibility: 'hidden',
      });
      svg.appendChild(dot);
      return dot;
    });
    const overlay = el('rect', {
      x: m.l, y: m.t, width: plot.w, height: plot.h, fill: 'transparent',
    });
    svg.appendChild(overlay);

    const nearest = (data, t) => {
      let best = data[0], bestGap = Infinity;
      for (const point of data) {
        const gap = Math.abs(point.t - t);
        if (gap < bestGap) { best = point; bestGap = gap; }
      }
      return best;
    };

    function move(event) {
      const rect = svg.getBoundingClientRect();
      const vx = (event.clientX - rect.left) / rect.width * VB.w;
      if (vx < m.l || vx > m.l + plot.w) return hide();
      const t = t0 + ((vx - m.l) / plot.w) * span;
      const rows = prepared.map((s, i) => {
        const point = nearest(s.data, t);
        dots[i].setAttribute('cx', x(point.t));
        dots[i].setAttribute('cy', y(point.v));
        dots[i].setAttribute('visibility', 'visible');
        return { s, point };
      });
      crosshair.setAttribute('x1', vx);
      crosshair.setAttribute('x2', vx);
      crosshair.setAttribute('visibility', 'visible');

      const when = rows[0]?.point.iso || '';
      tip.innerHTML = `<div class="when">${esc(when)}</div>`
        + rows.sort((a, b) => b.point.v - a.point.v).map(r =>
          `<div class="row"><span class="chip" style="background:${r.s.color}"></span>
             <span class="name">${esc(r.s.label)}</span>
             <span class="val">${esc(format(r.point.v))}</span></div>`).join('');
      tip.hidden = false;
      const left = (vx / VB.w) * rect.width;
      tip.style.left = `${Math.min(Math.max(left, 8), rect.width - tip.offsetWidth - 8)}px`;
    }
    function hide() {
      tip.hidden = true;
      crosshair.setAttribute('visibility', 'hidden');
      dots.forEach(d => d.setAttribute('visibility', 'hidden'));
    }
    svg.addEventListener('mousemove', move);
    svg.addEventListener('mouseleave', hide);
    svg.addEventListener('touchmove', e => {
      if (e.touches[0]) move(e.touches[0]);
    }, { passive: true });
  }

  /* ---------- diverging bar chart ---------- */
  /* items: [{ label, value, note? }]; value is signed, zero is the axis. */
  function bars(container, options) {
    const { items = [], format = (v) => v.toFixed(1), positiveColor, negativeColor } = options;
    const { svg } = shell(container);
    if (!items.length) {
      container.innerHTML = '<p class="empty">Nothing to plot for this window.</p>';
      return;
    }

    const m = { t: 14, r: 76, b: 26, l: 86 };
    const height = Math.max(240, items.length * 34 + m.t + m.b);
    svg.setAttribute('viewBox', `0 0 ${VB.w} ${height}`);
    const plot = { w: VB.w - m.l - m.r, h: height - m.t - m.b };

    const values = items.map(i => i.value).filter(Number.isFinite);
    const extent = Math.max(Math.abs(Math.min(...values, 0)), Math.abs(Math.max(...values, 0))) || 1;
    const lo = -extent * 1.12, hi = extent * 1.12;
    const x = (v) => m.l + ((v - lo) / (hi - lo)) * plot.w;
    const slot = plot.h / items.length;
    const barHeight = Math.min(22, slot - 6);   // the gap is the 2px+ surface rule

    niceTicks(lo, hi, 4).forEach(value => {
      svg.appendChild(el('line', {
        x1: x(value), x2: x(value), y1: m.t, y2: m.t + plot.h,
        stroke: 'var(--rule)', 'stroke-width': 1,
      }));
      const label = el('text', {
        x: x(value), y: height - 8, 'text-anchor': 'middle',
        fill: 'var(--text-muted)', 'font-size': 12,
      });
      label.textContent = format(value);
      svg.appendChild(label);
    });
    svg.appendChild(el('line', {
      x1: x(0), x2: x(0), y1: m.t, y2: m.t + plot.h,
      stroke: 'var(--text-secondary)', 'stroke-width': 1.5,
    }));

    items.forEach((item, i) => {
      const cy = m.t + slot * i + slot / 2;
      const value = Number(item.value) || 0;
      const left = Math.min(x(0), x(value));
      const width = Math.max(2, Math.abs(x(value) - x(0)));
      svg.appendChild(el('rect', {
        x: left, y: cy - barHeight / 2, width, height: barHeight, rx: 4,
        fill: value >= 0 ? positiveColor : negativeColor,
      }));
      const name = el('text', {
        x: m.l - 10, y: cy + 4, 'text-anchor': 'end',
        fill: 'var(--text-primary)', 'font-size': 13, 'font-weight': 600,
      });
      name.textContent = item.label;
      svg.appendChild(name);
      const amount = el('text', {
        x: value >= 0 ? left + width + 8 : left - 8, y: cy + 4,
        'text-anchor': value >= 0 ? 'start' : 'end',
        fill: 'var(--text-secondary)', 'font-size': 12.5,
      });
      amount.textContent = format(value);
      svg.appendChild(amount);
      if (item.title) {
        const tooltip = el('title');
        tooltip.textContent = item.title;
        svg.appendChild(tooltip);
      }
    });
  }

  global.Charts = { line, bars };
})(window);
