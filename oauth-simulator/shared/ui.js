'use strict';
/** Shared HTML/CSS kit so every page in the simulator looks like one product. */

const escapeHtml = (value) => String(value ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const CSS = `
:root{
  --bg:#f6f7fb; --panel:#ffffff; --panel-2:#f0f2f8; --ink:#131722; --muted:#5b6479;
  --line:#dfe3ee; --accent:#2f6df6; --accent-ink:#1b4ed1; --good:#0f8a5f; --warn:#b8730a;
  --bad:#cf2e4e; --code-bg:#151a29; --code-ink:#e6ebf5; --radius:12px;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{--bg:#0d1018;--panel:#151a25;--panel-2:#1b212f;--ink:#e8ecf5;--muted:#9aa5bd;
  --line:#28303f;--accent:#6f9dff;--accent-ink:#a8c3ff;--good:#3ecf95;--warn:#e0a13c;
  --bad:#ff6b86;--code-bg:#0a0d15;--code-ink:#e6ebf5;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code,pre,kbd{font-family:var(--mono)}
.wrap{max-width:1080px;margin:0 auto;padding:24px 20px 80px}
.wrap.narrow{max-width:640px}
header.top{border-bottom:1px solid var(--line);background:var(--panel);position:sticky;top:0;z-index:20}
header.top .inner{max-width:1080px;margin:0 auto;padding:12px 20px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.brand{font-weight:700;letter-spacing:-.2px;display:flex;gap:9px;align-items:center;font-size:15px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 22%,transparent)}
nav.top-nav{display:flex;gap:4px;flex-wrap:wrap;margin-left:auto}
nav.top-nav a{padding:6px 11px;border-radius:8px;color:var(--muted);font-size:13.5px;font-weight:500}
nav.top-nav a:hover{background:var(--panel-2);text-decoration:none;color:var(--ink)}
nav.top-nav a.active{background:var(--accent);color:#fff}
h1{font-size:27px;line-height:1.25;letter-spacing:-.5px;margin:22px 0 6px}
h2{font-size:19px;letter-spacing:-.2px;margin:30px 0 10px}
h3{font-size:15px;margin:20px 0 6px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
p{margin:9px 0}
.lede{color:var(--muted);font-size:16px;max-width:70ch;margin-bottom:6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:18px 20px;margin:14px 0}
.card.tight{padding:14px 16px}
.card h2:first-child,.card h3:first-child{margin-top:0}
.grid{display:grid;gap:14px}
@media(min-width:760px){.grid.two{grid-template-columns:1fr 1fr}.grid.three{grid-template-columns:repeat(3,1fr)}}
pre{background:var(--code-bg);color:var(--code-ink);padding:13px 15px;border-radius:10px;overflow-x:auto;font-size:12.5px;line-height:1.55;margin:9px 0;border:1px solid #00000030}
pre.wrapped{white-space:pre-wrap;word-break:break-all}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;border:1px solid transparent}
.pill.get{background:color-mix(in srgb,var(--good) 15%,transparent);color:var(--good);border-color:color-mix(in srgb,var(--good) 35%,transparent)}
.pill.post{background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 35%,transparent)}
.pill.ok{background:color-mix(in srgb,var(--good) 15%,transparent);color:var(--good)}
.pill.err{background:color-mix(in srgb,var(--bad) 15%,transparent);color:var(--bad)}
.pill.warn{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--warn)}
.pill.mute{background:var(--panel-2);color:var(--muted)}
.btn{display:inline-block;background:var(--accent);color:#fff;border:0;padding:10px 17px;border-radius:9px;font:600 14px var(--sans);cursor:pointer}
.btn:hover{filter:brightness(1.08);text-decoration:none}
.btn.ghost{background:transparent;color:var(--accent);border:1px solid var(--line)}
.btn.danger{background:var(--bad)}
.btn.sm{padding:6px 12px;font-size:13px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
label.field{display:block;margin:12px 0 0;font-size:13px;font-weight:600;color:var(--muted)}
input[type=text],input[type=password],select{width:100%;padding:10px 12px;margin-top:5px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);font:14px var(--sans)}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:8px 0}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;font-weight:700}
td.k{color:var(--muted);white-space:nowrap;font-family:var(--mono);font-size:12.5px}
td.v{font-family:var(--mono);font-size:12.5px;word-break:break-all}
.note{border-left:3px solid var(--accent);background:var(--panel-2);padding:11px 14px;border-radius:0 9px 9px 0;margin:12px 0;font-size:14px}
.note.warn{border-color:var(--warn)}
.note.bad{border-color:var(--bad)}
.note.good{border-color:var(--good)}
.note b{font-weight:700}
.steps{list-style:none;padding:0;margin:14px 0;counter-reset:s}
.steps li{position:relative;padding:0 0 18px 40px;counter-increment:s;border-left:2px solid var(--line);margin-left:13px}
.steps li:last-child{border-left-color:transparent;padding-bottom:0}
.steps li::before{content:counter(s);position:absolute;left:-15px;top:-2px;width:28px;height:28px;border-radius:50%;
  background:var(--accent);color:#fff;font:700 13px var(--mono);display:grid;place-items:center}
.steps li.done::before{background:var(--good)}
.steps li.fail::before{background:var(--bad)}
.steps li.pending::before{background:var(--panel-2);color:var(--muted);border:1px solid var(--line)}
.steps h4{margin:0 0 4px;font-size:15px}
.steps .when{font:11.5px var(--mono);color:var(--muted)}
.trace{background:var(--code-bg);color:var(--code-ink);border-radius:10px;padding:10px;max-height:340px;overflow:auto;font:12px/1.5 var(--mono)}
.trace .ev{padding:3px 6px;border-bottom:1px solid #ffffff12;display:flex;gap:8px}
.trace .ev:last-child{border-bottom:0}
.trace .t{color:#7f8bab;flex:none}
.trace .n{color:#ffd479;flex:none}
.trace .m{color:#d8e2f5}
.trace .ev.error .m{color:#ff9db0}
.trace .ev.ok .m{color:#7ee0b0}
.muted{color:var(--muted)}
.small{font-size:13px}
.tiny{font-size:11.5px}
.mono{font-family:var(--mono)}
.break{word-break:break-all}
footer.foot{border-top:1px solid var(--line);margin-top:36px;padding:16px 0;color:var(--muted);font-size:12.5px}
.lab-card{display:block;border:1px solid var(--line);border-radius:var(--radius);padding:16px;background:var(--panel);color:inherit;transition:transform .08s ease,border-color .12s ease}
.lab-card:hover{text-decoration:none;border-color:var(--accent);transform:translateY(-1px)}
.lab-card .n{font:700 11.5px var(--mono);color:var(--accent);letter-spacing:.08em}
.lab-card h3{text-transform:none;letter-spacing:0;color:var(--ink);font-size:16px;margin:6px 0 4px}
.lab-card p{color:var(--muted);font-size:13.5px;margin:0}
.flowdiag{width:100%;height:auto;display:block;margin:10px 0}
`;

function page({ title, subtitle, body, nav = [], active = '', brand = 'OAuth 2.1 Simulator', footer = '', head = '', wrapClass = '' }) {
  const navHtml = nav.map((item) => `<a href="${item.href}"${item.label === active ? ' class="active"' : ''}>${escapeHtml(item.label)}</a>`).join('');
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${escapeHtml(title)}</title>
<style>${CSS}</style>${head}
</head><body>
<header class="top"><div class="inner">
  <span class="brand"><span class="dot"></span>${escapeHtml(brand)}</span>
  <nav class="top-nav">${navHtml}</nav>
</div></header>
<div class="wrap ${wrapClass}">
${subtitle ? `<h1>${escapeHtml(title)}</h1><p class="lede">${subtitle}</p>` : ''}
${body}
${footer ? `<footer class="foot">${footer}</footer>` : ''}
</div></body></html>`;
}

const card = (inner, cls = '') => `<div class="card ${cls}">${inner}</div>`;
const pre = (content, cls = '') => `<pre class="${cls}">${escapeHtml(content)}</pre>`;
const preJson = (obj, cls = '') => pre(typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2), cls);
const note = (html, kind = '') => `<div class="note ${kind}">${html}</div>`;
const pill = (text, kind = 'mute') => `<span class="pill ${kind}">${escapeHtml(text)}</span>`;

function kvTable(obj, { highlight = [] } = {}) {
  const rows = Object.entries(obj || {}).map(([k, v]) => {
    const val = v === null || v === undefined ? '<span class="muted">(absent)</span>'
      : typeof v === 'object' ? escapeHtml(JSON.stringify(v))
      : escapeHtml(String(v));
    const mark = highlight.includes(k) ? ' style="background:color-mix(in srgb,var(--accent) 10%,transparent)"' : '';
    return `<tr${mark}><td class="k">${escapeHtml(k)}</td><td class="v">${val}</td></tr>`;
  }).join('');
  return `<table><tbody>${rows || '<tr><td class="muted">(empty)</td></tr>'}</tbody></table>`;
}

module.exports = { CSS, page, card, pre, preJson, note, pill, kvTable, escapeHtml };
