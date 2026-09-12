'use strict';
/**
 * The trace bus: an in-memory, human-readable audit log of every protocol
 * decision a service makes.
 *
 * This is the simulator's teaching surface. Real authorization servers log
 * the same events; here they are also streamed to the browser over SSE so
 * you can watch the protocol run while you click through it.
 */
const MAX_EVENTS = 400;

function createTracer(service) {
  const events = [];
  const listeners = new Set();
  let seq = 0;

  function emit(level, message, data = {}) {
    seq += 1;
    const event = {
      seq,
      at: new Date().toISOString(),
      service,
      level,
      message,
      data,
    };
    events.push(event);
    if (events.length > MAX_EVENTS) events.shift();

    const stamp = event.at.slice(11, 23);
    const tag = { info: 'INFO', ok: ' OK ', warn: 'WARN', error: 'FAIL', step: 'STEP' }[level] || level.toUpperCase();
    const colour = { ok: '\x1b[32m', warn: '\x1b[33m', error: '\x1b[31m', step: '\x1b[36m', info: '\x1b[90m' }[level] || '';
    const detail = Object.keys(data).length ? ` ${JSON.stringify(data)}` : '';
    // eslint-disable-next-line no-console
    console.log(`${colour}${stamp} [${service}] ${tag}\x1b[0m ${message}${detail}`);

    for (const listener of listeners) {
      try { listener(event); } catch { listeners.delete(listener); }
    }
    return event;
  }

  return {
    service,
    info: (m, d) => emit('info', m, d),
    ok: (m, d) => emit('ok', m, d),
    warn: (m, d) => emit('warn', m, d),
    error: (m, d) => emit('error', m, d),
    step: (m, d) => emit('step', m, d),
    log: (level, m, d) => emit(level, m, d),
    all: (since = 0) => events.filter((e) => e.seq > since),
    clear: () => { events.length = 0; seq = 0; },
    subscribe: (fn) => { listeners.add(fn); return () => listeners.delete(fn); },
  };
}

module.exports = { createTracer };
