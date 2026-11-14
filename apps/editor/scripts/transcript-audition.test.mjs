import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';

function moduleAt(relative, dependencies, globals = {}, extra = '') {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8') + extra;
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => dependencies[name] || {}, Date, BigInt, ...globals });
  return exports;
}
const events = { AUDITION_RANGE_EVENT: 'audition', CANCEL_AUDITION_EVENT: 'cancel', STOP_AUDITION_AUDIO_EVENT: 'audio-stop' };
function harness() {
  const window = new EventTarget(), document = Object.assign(new EventTarget(), { hidden: false });
  let cursor = 0, serial = 0, pending = [];
  const slots = [], frames = new Map(), timers = new Map();
  let wall = 0;
  window.setTimeout = (callback, delay) => { timers.set(++serial, { callback, at: wall + delay }); return serial; };
  window.clearTimeout = id => timers.delete(id);
  const useEffect = (callback, dependencies) => {
    const index = cursor++, previous = slots[index];
    if (previous && dependencies.every((value, offset) => Object.is(value, previous.dependencies[offset]))) return;
    pending.push(() => { previous?.cleanup?.(); slots[index] = { dependencies, cleanup: callback() }; });
  };
  const { useTranscriptAudition } = moduleAt('../src/components/useTranscriptAudition.ts', {
    react: { useEffect, useLayoutEffect: useEffect, useRef: value => {
      const index = cursor++; return slots[index] ||= { current: value };
    } }, '../lib/transcript': events,
  }, { window, document, Event, CustomEvent, requestAnimationFrame: callback => { frames.set(++serial, callback); return serial; },
    cancelAnimationFrame: id => frames.delete(id) });
  let running = false, time = 3, pauses = 0, audioStops = 0, ended = 0;
  let boundary = () => null;
  const seeks = [];
  window.addEventListener(events.STOP_AUDITION_AUDIO_EVENT, () => audioStops++);
  let options = { projectId: 'p', sequenceId: 's', revision: '5', end: 5, playhead: 3, seekVersion: 0, scrubbing: false,
    // This mutable ref reproduces the native transport leading React's render.
    // It remains here to demonstrate that the previous implementation fails.
    reportedTime: { current: 3 }, video: { current: null }, windowStart: 2,
    clock: () => ({ time, playing: running }), play: () => { running = true; },
    pause: () => { pauses++; running = false; }, seek: value => seeks.push(value), onEnd: () => ended++ };
  const render = patch => {
    options = { ...options, ...patch }; cursor = 0; pending = [];
    boundary = useTranscriptAudition(options) || (() => null); for (const commit of pending) commit();
  };
  const frame = next => {
    time = next; const callbacks = [...frames.values()]; frames.clear();
    for (const callback of callbacks) callback(next * 1000);
  };
  const begin = () => window.dispatchEvent(new CustomEvent(events.AUDITION_RANGE_EVENT,
    { detail: { projectId: 'p', sequenceId: 's', revision: '5', start: 3, end: 3.5 } }));
  render({});
  const advanceTimers = next => {
    time = next; wall = (next - 3) * 1000;
    const due = [...timers.entries()].filter(([, value]) => value.at <= wall);
    for (const [id, value] of due) { timers.delete(id); value.callback(); }
  };
  return { render, frame, begin, advanceTimers, boundary: next => boundary(next), timerCount: () => timers.size, options: () => options, running: () => running, pauses: () => pauses,
    seeks, audioStops: () => audioStops, ended: () => ended, window, document };
}

test('normal transport ahead of a delayed React render does not cancel a bounded audition', () => {
  const h = harness(); h.begin(); h.frame(3); h.frame(3.412154195);
  assert.equal(h.running(), true);
  h.options().reportedTime.current = 3.412154195;
  h.render({ playhead: 3.31 }); // a normal, stale transport render; no user intent
  assert.equal(h.running(), true); assert.equal(h.pauses(), 1);
  h.frame(3.51);
  assert.equal(h.running(), false); assert.equal(h.seeks.at(-1), 3.5);
  assert.equal(h.audioStops(), 1); assert.equal(h.ended(), 1);
});
test('an explicit sub-frame seek cancels audition even within its current range', () => {
  const h = harness(); h.begin(); h.frame(3); h.frame(3.1);
  h.render({ playhead: 3.101, seekVersion: 1 });
  assert.equal(h.running(), false); assert.equal(h.audioStops(), 1); assert.equal(h.ended(), 0);
  h.frame(3.6); assert.deepEqual(h.seeks, [3]); // cancellation preserves the requested seek
});
test('scrubbing and hidden-tab cancellation synchronously stop native voices', () => {
  for (const cancel of ['scrub', 'hidden']) {
    const h = harness(); h.begin(); h.frame(3); h.frame(3.1);
    if (cancel === 'scrub') h.render({ scrubbing: true });
    else { h.document.hidden = true; h.document.dispatchEvent(new Event('visibilitychange')); }
    assert.equal(h.running(), false); assert.equal(h.audioStops(), 1);
  }
});
test('the actual editor reducer distinguishes transport ticks from explicit user seek intent', () => {
  const { reducer, initialState } = moduleAt('../src/store/editor.tsx', {
    react: { createContext: () => ({}) },
  }, {}, '\nexport { reducer, initialState };\n');
  let state = initialState;
  for (const time of [3, 3.1, 3.31, 3.412154195]) state = reducer(state, { type: 'PLAYHEAD_SET', t: time, source: 'transport' });
  assert.equal(state.playhead, 3.412154195); assert.equal(state.playheadSeekVersion, 0);
  state = reducer(state, { type: 'PLAYHEAD_SET', t: state.playhead + .001 });
  assert.equal(state.playheadSeekVersion, 1);
  state = reducer(state, { type: 'PLAYHEAD_SET', t: state.playhead });
  assert.equal(state.playheadSeekVersion, 2); // same-position intent is still an explicit seek
});

test('scope cleanup stops audio without publishing a natural-end status', () => {
  const h = harness(); h.begin(); h.frame(3); h.frame(3.1);
  h.render({ revision: '6' });
  assert.equal(h.running(), false); assert.equal(h.audioStops(), 1); assert.equal(h.ended(), 0);
});

for (const input of ['seek', 'scrub']) {
  test(`a ${input} before the first animation frame cancels pending audition startup`, () => {
    const h = harness(); h.begin();
    h.render(input === 'seek' ? { playhead: 3.2, seekVersion: 1 } : { scrubbing: true });
    h.frame(3.2); h.frame(3.3);
    assert.equal(h.running(), false); assert.equal(h.audioStops(), 1);
    assert.deepEqual(h.seeks, [3]); assert.equal(h.ended(), 0);
  });
}
for (const key of ['projectId', 'sequenceId', 'revision']) {
  test(`a changed ${key} before passive cleanup cannot start or pause the new scope`, () => {
    const h = harness(); h.begin();
    h.options()[key] = 'new-scope'; // Render delivered; passive cleanup has not run yet.
    h.frame(3.2); h.frame(3.3);
    assert.equal(h.running(), false); assert.equal(h.pauses(), 1); // Only begin's original pause.
    assert.equal(h.audioStops(), 0); assert.deepEqual(h.seeks, [3]); assert.equal(h.ended(), 0);
  });
}
test('a tab hidden before pending startup cannot begin playback without a visibility event', () => {
  const h = harness(); h.begin(); h.document.hidden = true;
  h.frame(3.2); h.frame(3.3);
  assert.equal(h.running(), false); assert.equal(h.audioStops(), 1);
  assert.deepEqual(h.seeks, [3]); assert.equal(h.ended(), 0);
});

test('a stale frame delivered before scope effect cleanup cannot seek or publish end feedback into the new editor', () => {
  for (const changed of ['projectId', 'sequenceId', 'revision']) {
    const h = harness(); h.begin(); h.frame(3); h.frame(3.1);
    // React updated the latest-options ref, but passive cleanup has not run yet.
    h.options()[changed] = 'different'; h.frame(4);
    assert.equal(h.running(), false); assert.equal(h.audioStops(), 1); assert.equal(h.ended(), 0);
    assert.deepEqual(h.seeks, [3]);
  }
});


test('an independent media-clock deadline stops native audio when animation frames do not arrive', () => {
  const h = harness(); h.begin(); h.frame(3);
  assert.equal(h.running(), true);
  h.advanceTimers(3.501); // No RAF callbacks after startup.
  assert.equal(h.running(), false);
  assert.equal(h.audioStops(), 1); assert.equal(h.seeks.at(-1), 3.5);
  assert.equal(h.timerCount(), 0);
});

test('the shared transport checks the audition boundary before publishing a delayed clock sample', () => {
  const h = harness(); h.begin(); h.frame(3);
  assert.equal(h.boundary(3.65), 3.5);
  assert.equal(h.running(), false); assert.equal(h.audioStops(), 1);
  assert.equal(h.boundary(3.7), null); assert.equal(h.ended(), 1);
});

test('cancellation clears an armed deadline and cannot later seek or pause a new audition', () => {
  const h = harness(); h.begin(); h.frame(3);
  h.window.dispatchEvent(new Event(events.CANCEL_AUDITION_EVENT));
  assert.equal(h.timerCount(), 0);
  const pauses = h.pauses(); h.advanceTimers(4);
  assert.equal(h.pauses(), pauses); assert.deepEqual(h.seeks, [3]);
});

test('deadline observation cannot activate a device clock before the normal transport driver', () => {
  const h = harness(); let activations = 0;
  h.render({ clock: (initialize = true) => {
    if (initialize) activations++;
    return { time: 3, playing: false };
  } });
  h.begin(); h.frame(3);
  h.advanceTimers(3.1);
  assert.equal(activations, 0);
  h.frame(3.11);
  assert.ok(activations > 0); // Ordinary transport work is allowed to start it.
  h.window.dispatchEvent(new Event(events.CANCEL_AUDITION_EVENT));
  assert.equal(h.timerCount(), 0);
});
