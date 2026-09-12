'use strict';
/**
 * A tiny SVG sequence-diagram renderer.
 *
 * Used by the client app to draw the flow it is currently executing, with
 * completed steps highlighted. Themed with CSS variables so it reads
 * correctly in both light and dark browsers.
 */

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/**
 * Wrap a label into lines short enough for the available width.
 * Rough metric: ~6.1px per character at 11.5px monospace.
 */
function wrapLabel(text, maxChars) {
  const words = String(text).split(/\s+/);
  const lines = [];
  let line = '';
  for (const word of words) {
    if (!line.length) line = word;
    else if (`${line} ${word}`.length <= maxChars) line += ` ${word}`;
    else { lines.push(line); line = word; }
  }
  if (line) lines.push(line);
  return lines;
}

/**
 * @param {object}   opts
 * @param {string[]} opts.lanes     lane titles, left to right
 * @param {object[]} opts.messages  {from, to, label, note, kind, step}
 *                                  kind: 'request' | 'response' | 'redirect' |
 *                                        'internal' | 'blocked'
 * @param {number[]} opts.highlight step numbers to draw as completed
 */
function sequenceDiagram({ lanes, messages, highlight = [], width = 880, laneLabelLines = 2 }) {
  const laneCount = lanes.length;
  const margin = 14;
  const laneWidth = (width - margin * 2) / laneCount;
  const laneCenter = (i) => margin + laneWidth * i + laneWidth / 2;
  const headerH = 26 + laneLabelLines * 13;
  const rowGap = 12;

  // Lay the rows out first so the total height is known before drawing.
  const rows = [];
  let y = headerH + 22;
  for (const msg of messages) {
    const fromIdx = lanes.indexOf(msg.from);
    const toIdx = lanes.indexOf(msg.to);
    const span = Math.max(1, Math.abs(toIdx - fromIdx));
    const maxChars = Math.floor((laneWidth * span) / 6.1) - 4;
    const labelLines = wrapLabel(msg.label, Math.max(18, maxChars));
    const noteLines = msg.note ? wrapLabel(msg.note, Math.max(24, maxChars + 8)) : [];
    const height = labelLines.length * 13 + noteLines.length * 12 + 20;
    rows.push({ msg, fromIdx, toIdx, labelLines, noteLines, y, height });
    y += height + rowGap;
  }
  const totalH = y + 8;

  const colour = {
    request: 'var(--accent,#2f6df6)',
    response: 'var(--good,#0f8a5f)',
    redirect: 'var(--warn,#b8730a)',
    internal: 'var(--muted,#5b6479)',
    blocked: 'var(--bad,#cf2e4e)',
  };

  const parts = [];
  parts.push(`<svg class="flowdiag" viewBox="0 0 ${width} ${totalH}" xmlns="http://www.w3.org/2000/svg" role="img" font-family="var(--mono,monospace)">`);
  parts.push(`<defs>
    <marker id="ar" viewBox="0 0 10 8" refX="9" refY="4" markerWidth="8" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,0 L10,4 L0,8 z" fill="context-stroke"/>
    </marker>
  </defs>`);

  // Lane headers and lifelines
  lanes.forEach((lane, i) => {
    const cx = laneCenter(i);
    const boxW = laneWidth - 12;
    parts.push(`<rect x="${cx - boxW / 2}" y="6" width="${boxW}" height="${headerH - 12}" rx="8"
      fill="var(--panel-2,#f0f2f8)" stroke="var(--line,#dfe3ee)"/>`);
    wrapLabel(lane, Math.floor(boxW / 6.4)).slice(0, laneLabelLines).forEach((line, li) => {
      parts.push(`<text x="${cx}" y="${24 + li * 13}" text-anchor="middle" font-size="11" font-weight="700"
        fill="var(--ink,#131722)">${esc(line)}</text>`);
    });
    parts.push(`<line x1="${cx}" y1="${headerH - 2}" x2="${cx}" y2="${totalH - 4}"
      stroke="var(--line,#dfe3ee)" stroke-width="1.5" stroke-dasharray="3 4"/>`);
  });

  // Messages
  for (const row of rows) {
    const { msg, fromIdx, toIdx, labelLines, noteLines } = row;
    const done = highlight.includes(msg.step);
    const stroke = colour[msg.kind] || colour.request;
    const opacity = highlight.length && !done ? 0.34 : 1;
    const x1 = laneCenter(fromIdx);
    const x2 = laneCenter(toIdx);
    const arrowY = row.y + labelLines.length * 13 + 4;
    const mid = (x1 + x2) / 2;

    parts.push(`<g opacity="${opacity}">`);
    labelLines.forEach((line, li) => {
      parts.push(`<text x="${mid}" y="${row.y + 10 + li * 13}" text-anchor="middle" font-size="11.5"
        font-weight="${li === 0 ? 600 : 400}" fill="var(--ink,#131722)">${esc(line)}</text>`);
    });

    if (fromIdx === toIdx) {
      // Self-call: a little loop back into the same lifeline.
      const w = 34;
      parts.push(`<path d="M${x1},${arrowY} h${w} v14 h-${w}" fill="none" stroke="${stroke}" stroke-width="1.6"
        marker-end="url(#ar)" ${msg.kind === 'internal' ? 'stroke-dasharray="4 3"' : ''}/>`);
    } else {
      const dash = msg.kind === 'redirect' ? ' stroke-dasharray="6 4"' : msg.kind === 'internal' ? ' stroke-dasharray="4 3"' : '';
      parts.push(`<line x1="${x1}" y1="${arrowY}" x2="${x2}" y2="${arrowY}" stroke="${stroke}" stroke-width="1.8"
        marker-end="url(#ar)"${dash}/>`);
      if (msg.kind === 'blocked') {
        // An X across the arrow: this message was refused.
        parts.push(`<g stroke="${colour.blocked}" stroke-width="2.4">
          <line x1="${mid - 7}" y1="${arrowY - 7}" x2="${mid + 7}" y2="${arrowY + 7}"/>
          <line x1="${mid - 7}" y1="${arrowY + 7}" x2="${mid + 7}" y2="${arrowY - 7}"/></g>`);
      }
    }

    // Step number badge, sitting on the originating lifeline.
    parts.push(`<circle cx="${x1}" cy="${arrowY}" r="8.5" fill="${done ? 'var(--good,#0f8a5f)' : stroke}"/>`);
    parts.push(`<text x="${x1}" y="${arrowY + 3.4}" text-anchor="middle" font-size="9.5" font-weight="700" fill="#fff">${msg.step}</text>`);

    noteLines.forEach((line, li) => {
      parts.push(`<text x="${mid}" y="${arrowY + 24 + li * 12}" text-anchor="middle" font-size="10.5"
        font-style="italic" fill="var(--muted,#5b6479)">${esc(line)}</text>`);
    });
    parts.push('</g>');
  }

  parts.push('</svg>');
  return parts.join('\n');
}

/** The canonical OAuth 2.1 Authorization Code + PKCE flow. */
const LANES = ['User + Browser', 'Client App', 'Authorization Server + IdP', 'Resource Server (API)'];

function authCodePkceDiagram(highlight = [], width = 880) {
  return sequenceDiagram({
    lanes: LANES,
    highlight,
    width,
    messages: [
      { step: 1, from: 'Client App', to: 'Client App', kind: 'internal', label: 'create code_verifier, hash it into code_challenge', note: 'the verifier never leaves the client' },
      { step: 2, from: 'Client App', to: 'User + Browser', kind: 'redirect', label: '302 to /authorize?...&code_challenge=...&state=...' },
      { step: 3, from: 'User + Browser', to: 'Authorization Server + IdP', kind: 'request', label: 'GET /authorize  (front channel)', note: 'AS stores the challenge against this request' },
      { step: 4, from: 'Authorization Server + IdP', to: 'User + Browser', kind: 'response', label: 'login form, then consent screen', note: 'the password is typed here, never into the app' },
      { step: 5, from: 'Authorization Server + IdP', to: 'User + Browser', kind: 'redirect', label: '302 back to redirect_uri?code=...&state=...' },
      { step: 6, from: 'User + Browser', to: 'Client App', kind: 'request', label: 'GET /callback?code=...&state=...', note: 'client checks state matches' },
      { step: 7, from: 'Client App', to: 'Authorization Server + IdP', kind: 'request', label: 'POST /token  code + code_verifier  (back channel)', note: 'AS re-hashes the verifier and compares' },
      { step: 8, from: 'Authorization Server + IdP', to: 'Client App', kind: 'response', label: 'access_token + refresh_token (+ id_token)' },
      { step: 9, from: 'Client App', to: 'Resource Server (API)', kind: 'request', label: 'GET /api/accounts   Authorization: Bearer ...' },
      { step: 10, from: 'Resource Server (API)', to: 'Client App', kind: 'response', label: '200 OK  data', note: 'signature, iss, aud, exp and scope all checked' },
    ],
  });
}

module.exports = { sequenceDiagram, authCodePkceDiagram, wrapLabel, LANES };
