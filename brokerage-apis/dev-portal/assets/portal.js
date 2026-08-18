/* ===========================================================================
   Brokerage Developer Portal — progressive enhancement only.
   Every page is fully readable with JavaScript disabled; this file adds the
   theme toggle, language tabs, copy buttons, mobile nav, and scroll-spy.
   No dependencies, no build step.
   =========================================================================== */

(function () {
  'use strict';

  const THEME_KEY = 'brokerage-portal-theme';
  const LANG_KEY = 'brokerage-portal-lang';

  /* --- theme ------------------------------------------------------------ */

  function applyTheme(theme) {
    if (theme === 'light' || theme === 'dark') {
      document.documentElement.setAttribute('data-theme', theme);
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    const btn = document.getElementById('theme-toggle');
    if (btn) {
      const dark = theme === 'dark' ||
        (!theme && window.matchMedia('(prefers-color-scheme: dark)').matches);
      btn.textContent = dark ? '☀' : '☾';
      btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    }
  }

  function initTheme() {
    let stored = null;
    try { stored = localStorage.getItem(THEME_KEY); } catch (e) { /* private mode */ }
    applyTheme(stored);

    const btn = document.getElementById('theme-toggle');
    if (!btn) return;

    btn.addEventListener('click', () => {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark' ||
        (!document.documentElement.hasAttribute('data-theme') &&
          window.matchMedia('(prefers-color-scheme: dark)').matches);
      const next = isDark ? 'light' : 'dark';
      try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* ignore */ }
      applyTheme(next);
    });
  }

  /* --- tab groups ------------------------------------------------------- */

  /* Two kinds of group:
       .tabs[data-sync="lang"]  — language pickers. Choosing one switches every
                                  other language group on the page and persists,
                                  so a Python reader picks Python once.
       .tabs                    — local groups (order types, strategies). These
                                  switch independently and are never persisted;
                                  syncing them would clobber the language choice,
                                  since their keys are unrelated. */

  function applyToGroup(group, value) {
    const buttons = group.querySelectorAll('.tab-strip button[data-lang]');
    const panels = group.querySelectorAll('.tab-panel');
    if (!buttons.length) return false;

    const matched = Array.from(buttons).some((b) => b.dataset.lang === value);
    const target = matched ? value : buttons[0].dataset.lang;

    buttons.forEach((b) => {
      const on = b.dataset.lang === target;
      b.setAttribute('aria-selected', on ? 'true' : 'false');
      b.tabIndex = on ? 0 : -1;
    });
    panels.forEach((p) => { p.hidden = p.dataset.lang !== target; });
    return matched;
  }

  function selectLanguage(lang) {
    document.querySelectorAll('.tabs[data-sync="lang"]').forEach((g) => applyToGroup(g, lang));
  }

  function initTabs() {
    const groups = document.querySelectorAll('.tabs');
    if (!groups.length) return;

    let preferred = null;
    try { preferred = localStorage.getItem(LANG_KEY); } catch (e) { /* ignore */ }

    groups.forEach((group) => {
      const strip = group.querySelector('.tab-strip');
      if (!strip) return;
      const synced = group.dataset.sync === 'lang';

      const choose = (value) => {
        if (synced) {
          try { localStorage.setItem(LANG_KEY, value); } catch (e) { /* ignore */ }
          selectLanguage(value);
        } else {
          applyToGroup(group, value);
        }
      };

      strip.addEventListener('click', (event) => {
        const button = event.target.closest('button[data-lang]');
        if (button) choose(button.dataset.lang);
      });

      // Left/right arrow keys move between tabs, per the ARIA tabs pattern.
      strip.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        const buttons = Array.from(strip.querySelectorAll('button[data-lang]'));
        const current = buttons.findIndex((b) => b.getAttribute('aria-selected') === 'true');
        const next = buttons[(current + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length];
        if (!next) return;
        event.preventDefault();
        choose(next.dataset.lang);
        next.focus();
      });

      // Local groups just settle on their first tab.
      if (!synced) {
        applyToGroup(group, strip.querySelector('button[data-lang]').dataset.lang);
      }
    });

    const firstSynced = document.querySelector('.tabs[data-sync="lang"] .tab-strip button[data-lang]');
    if (firstSynced) selectLanguage(preferred || firstSynced.dataset.lang);
  }

  /* --- copy buttons ----------------------------------------------------- */

  function initCopy() {
    document.querySelectorAll('pre').forEach((pre) => {
      if (pre.querySelector('.copy-btn')) return;

      const button = document.createElement('button');
      button.className = 'copy-btn';
      button.type = 'button';
      button.textContent = 'Copy';
      button.setAttribute('aria-label', 'Copy code to clipboard');

      button.addEventListener('click', async () => {
        const code = pre.querySelector('code');
        const text = (code || pre).innerText;
        try {
          await navigator.clipboard.writeText(text);
        } catch (e) {
          // clipboard API is unavailable over file:// in some browsers
          const area = document.createElement('textarea');
          area.value = text;
          area.style.position = 'fixed';
          area.style.opacity = '0';
          document.body.appendChild(area);
          area.select();
          try { document.execCommand('copy'); } catch (e2) { /* give up quietly */ }
          document.body.removeChild(area);
        }
        button.textContent = 'Copied';
        button.classList.add('done');
        setTimeout(() => {
          button.textContent = 'Copy';
          button.classList.remove('done');
        }, 1400);
      });

      pre.appendChild(button);
    });
  }

  /* --- mobile nav ------------------------------------------------------- */

  function initNav() {
    const toggle = document.getElementById('menu-toggle');
    const scrim = document.querySelector('.scrim');
    if (!toggle) return;

    const close = () => document.body.classList.remove('nav-open');

    toggle.addEventListener('click', () => document.body.classList.toggle('nav-open'));
    if (scrim) scrim.addEventListener('click', close);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    document.querySelectorAll('.sidebar a').forEach((a) => a.addEventListener('click', close));
  }

  /* --- on-this-page: build and scroll-spy ------------------------------- */

  function initToc() {
    const toc = document.querySelector('.toc');
    const main = document.querySelector('main');
    if (!toc || !main) return;

    const headings = Array.from(main.querySelectorAll('h2[id], h3[id]'));
    if (!headings.length) { toc.style.display = 'none'; return; }

    const list = document.createElement('ul');
    headings.forEach((h) => {
      const li = document.createElement('li');
      if (h.tagName === 'H3') li.className = 'sub';
      const a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent.trim();
      li.appendChild(a);
      list.appendChild(li);
    });

    const label = document.createElement('div');
    label.className = 'toc-label';
    label.textContent = 'On this page';
    toc.appendChild(label);
    toc.appendChild(list);

    const links = new Map();
    toc.querySelectorAll('a').forEach((a) => links.set(a.getAttribute('href').slice(1), a));

    // Highlight the heading nearest the top of the viewport. rootMargin pins
    // the trigger line just under the sticky header rather than at the very
    // top, so the active item matches what the reader is actually looking at.
    const seen = new Set();
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) seen.add(entry.target.id);
        else seen.delete(entry.target.id);
      });

      let activeId = null;
      for (const h of headings) {
        if (seen.has(h.id)) { activeId = h.id; break; }
      }
      if (!activeId) return;

      links.forEach((a, id) => a.classList.toggle('active', id === activeId));
    }, { rootMargin: '-80px 0px -70% 0px', threshold: 0 });

    headings.forEach((h) => observer.observe(h));
  }

  /* --- heading anchors -------------------------------------------------- */

  function initAnchors() {
    document.querySelectorAll('main h2[id], main h3[id]').forEach((h) => {
      const a = document.createElement('a');
      a.className = 'anchor';
      a.href = '#' + h.id;
      a.textContent = '#';
      a.setAttribute('aria-label', 'Link to this section');
      h.appendChild(a);
    });
  }

  /* --- boot ------------------------------------------------------------- */

  function boot() {
    initTheme();
    initNav();
    initToc();       // read heading text before initAnchors() appends its '#'
    initAnchors();
    initTabs();
    initCopy();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
