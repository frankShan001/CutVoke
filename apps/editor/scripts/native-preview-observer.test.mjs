import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import ts from "typescript";
import { createRequire } from "node:module";
const { verifyNativePreview } = createRequire(import.meta.url)("../../../tools/verify/verify_native_preview.cjs");

// Deterministically model compositor submission while Play's automation RPC is
// still pending. The helper under test (not a duplicate observer) runs in a DOM
// sandbox. Real browser/native tests separately cover decoding and rendering.
function fixture({ black = false, failClick = false, submit = true, handoff = false, snapshotThrows = false, recordThrows = false, beforeEmit = () => {}, metadata = { mediaTime: .5, presentedFrames: 2 }, priorEvidence, mediaAccessor = false, mediaConfigurable = true } = {}) {
  const polls = new Set(), timers = new Set(), events = [];
  let id = 0, drawn = null, pixelReads = 0;
  const listeners = new Map();
  const listen = (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); };
  const unlisten = (type, fn) => listeners.get(type)?.delete(fn);
  const dispatch = (type, value = {}) => { for (const fn of listeners.get(type) || []) fn(Object.defineProperties({ type }, Object.getOwnPropertyDescriptors(value))); };
  const window = Object.defineProperties({}, {
    addEventListener: { value: listen }, removeEventListener: { value: unlisten },
  });
  if (priorEvidence) Object.defineProperty(window, "__cutvokePreviewStopEvidence", priorEvidence);
  const makeVideo = source => ({
    paused: true, seeking: false, readyState: 4, currentTime: 0, duration: 3,
    matches: () => true,
    videoWidth: 1, videoHeight: 1, currentSrc: source, callbacks: new Map(),
    requestVideoFrameCallback(callback) { this.callbacks.set(++id, callback); return id; },
    cancelVideoFrameCallback(handle) { this.callbacks.delete(handle); },
  });
  const first = makeVideo("https://private.example/secret-media?token=SECRET"), second = makeVideo("/Users/private/media-secret.mp4");
  const mediaValues = new WeakMap(), mediaWrites = [], nativeSeekError = new Error("native test setter refused");
  const Media = class {};
  let mediaDescriptor;
  if (mediaAccessor) {
    Object.defineProperty(window, "HTMLMediaElement", { value: Media });
    Object.defineProperty(Media.prototype, "currentTime", {
      configurable: mediaConfigurable, enumerable: true,
      get() { return mediaValues.get(this); },
      set(value) { mediaWrites.push([this, value]); if (value === 999) throw nativeSeekError; mediaValues.set(this, value); },
    });
    mediaDescriptor = Object.getOwnPropertyDescriptor(Media.prototype, "currentTime");
    for (const v of [first, second]) {
      delete v.currentTime; Object.setPrototypeOf(v, Media.prototype); mediaValues.set(v, 0);
    }
  }
  const advanceDecoder = (video, value) => {
    if (mediaAccessor) mediaValues.set(video, value);
    else video.currentTime = value;
  };
  let active = first;
  const context = vm.createContext({
    window,
    document: {
      addEventListener: listen, removeEventListener: unlisten,
      querySelector: () => active,
      createElement: () => ({ getContext: () => ({
        drawImage: video => { drawn = video; },
        getImageData: () => { pixelReads++; return { data: [black ? 0 : 255, 0, 0, 255] }; },
      }) }),
    },
    setInterval: callback => { polls.add(callback); return callback; },
    clearInterval: handle => polls.delete(handle),
    setTimeout: (callback, delay) => { const handle = setTimeout(callback, delay); timers.add(handle); return handle; },
    clearTimeout: handle => { clearTimeout(handle); timers.delete(handle); },
  });
  const emit = video => {
    for (const [handle, callback] of [...video.callbacks]) {
      video.callbacks.delete(handle);
      callback(0, metadata);
    }
  };
  const video = { evaluate: callback => callback(active) };
  const ruler = { getAttribute: async () => String(active.currentTime), focus: async () => {}, press: async () => {} };
  const page = {
    locator: () => video,
    evaluate: async (callback, arg) => { context.arg = arg;
      if (snapshotThrows && callback.toString().includes("state.snapshot()")) {
        context.window[arg].snapshot = () => { throw new Error("diagnostic failure"); };
      }
      return vm.runInContext(`(${callback.toString()})(arg)`, context); },
    getByRole: (role, { name }) => role === "slider" ? ruler : { click: async () => {
      if (name === "暂停") { active.paused = true; events.push("pause"); return; }
      if (recordThrows) {
        for (const state of Object.values(context.window)) {
          if (!state?.diagnostic) continue;
          state.diagnostic.events.push = () => { throw new Error("diagnostic write failure"); };
        }
      }
      if (failClick) throw new Error("Play action failed");
      active.paused = false; advanceDecoder(active, .5);
      for (const poll of polls) poll();
      if (handoff) {
        active = second; active.paused = false; advanceDecoder(active, .5);
        emit(first); // A queued callback from the former pool source is invalid.
        assert.equal(pixelReads, 0);
        for (const poll of polls) poll();
      }
      beforeEmit({ active, polls, emit, dispatch, window, first, second, nativeSeekError, mediaDescriptor, setActive: v => { active = v; } });
      if (submit) emit(active);
      events.push("click-return");
    } },
  };
  const expect = value => ({
    toBeVisible: async () => { events.push("visible"); },
    toBeGreaterThan: minimum => assert.ok(value > minimum, `${value} must exceed ${minimum}`),
  });
  expect.poll = read => ({
    toBeGreaterThanOrEqual: async minimum => assert.ok(await read() >= minimum),
    toBeGreaterThan: async minimum => { assert.ok(await read() > minimum); events.push("clock-checked"); },
  });
  return { page, expect, events, first, second, mediaWrites, get drawn() { return drawn; }, get pixelReads() { return pixelReads; },
    assertClean(expectedDescriptor = mediaDescriptor) { assert.equal(polls.size, 0); assert.equal(timers.size, 0); assert.deepEqual(Object.keys(context.window), []); assert.equal([...listeners.values()].reduce((n, s) => n + s.size, 0), 0);
      assert.deepEqual(Object.getOwnPropertyDescriptor(window, "__cutvokePreviewStopEvidence"), priorEvidence);
      assert.deepEqual(Object.getOwnPropertyDescriptor(Media.prototype, "currentTime"), expectedDescriptor);
      assert.equal(first.callbacks.size + second.callbacks.size, 0); } };
}

test("native acceptance captures submitted pixels before slow Play RPC returns", async () => {
  const f = fixture();
  const frames = await verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100 });
  assert.equal(frames[0].energy, 255);
  assert.equal(f.pixelReads, 1);
  assert.equal(f.events.filter(event => event === "clock-checked").length, 2);
  assert.equal(f.events.filter(event => event === "visible").length, 2);
  assert.equal(f.events.at(-1), "pause");
  f.assertClean();
});
test("native acceptance rejects first submitted black frame without sampling again", async () => {
  const f = fixture({ black: true });
  await assert.rejects(verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100 }), /0 must exceed 0/);
  assert.equal(f.pixelReads, 1); f.assertClean();
});
test("native acceptance follows active source and ignores stale submitted callbacks", async () => {
  const f = fixture({ handoff: true });
  await verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100 });
  assert.equal(f.drawn, f.second); assert.equal(f.pixelReads, 1); f.assertClean();
});
test("native acceptance cleans observer when Play fails", async () => {
  const f = fixture({ failClick: true });
  await assert.rejects(verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100 }), /Play action failed/);
  f.assertClean();
});
test("native acceptance keeps deadline when no compositor frame arrives", async () => {
  const f = fixture({ submit: false });
  await assert.rejects(verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 20 }), /No submitted playing preview frame/);
  assert.equal(f.pixelReads, 0); f.assertClean();
});

async function failureDiagnostic(options) {
  const f = fixture(options), diagnostics = [];
  await assert.rejects(verifyNativePreview(f.page, f.expect, {
    cycles: 1, timeout: 20, onDiagnostic: d => diagnostics.push(d),
  }), /No submitted playing preview frame/);
  assert.equal(diagnostics.length, 1);
  const d = diagnostics[0];
  assert.equal(d.counts.deadline, 1);
  assert.equal(d.events.at(-1).event, "snapshot");
  assert.ok(d.events.length <= 64);
  assert.doesNotMatch(JSON.stringify(d), /private|SECRET|https:|Users|\.mp4/);
  f.assertClean();
  return d;
}
test("diagnostics distinguish no callback from paused/ended and seeking callbacks", async () => {
  const absent = await failureDiagnostic({ submit: false });
  assert.equal(absent.counts.arm, 1); assert.equal(absent.counts.accepted, undefined);
  for (const reason of ["paused", "seeking"]) {
    const d = await failureDiagnostic({ beforeEmit: ({ active }) => {
      active[reason] = true; active.ended = reason === "paused";
    } });
    assert.equal(d.counts[reason], 1);
    assert.equal(d.events.at(-1)[reason], true);
    assert.equal(d.events.at(-1).ended, reason === "paused");
  }
});
test("diagnostics retain same-node source changes without retaining the source", async () => {
  const d = await failureDiagnostic({ beforeEmit: ({ active }) => { active.currentSrc = "/Users/private/new-secret.mp4"; } });
  assert.equal(d.counts["changed-source"], 1);
  const change = d.events.find(e => e.event === "changed-source");
  assert.equal(change.node, 1); assert.equal(change.sourceGeneration, 2);
});
test("diagnostics preserve inactive-node rejection across pool handoff", async () => {
  const f = fixture({ handoff: true }), diagnostics = [];
  await verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100, onDiagnostic: d => diagnostics.push(d) });
  assert.equal(diagnostics[0].counts["inactive-node"], 1);
  assert.equal(diagnostics[0].counts.accepted, 1);
  assert.equal(diagnostics[0].events.find(e => e.event === "accepted").node, 2);
  f.assertClean();
});
test("diagnostic event history is bounded and timeout retains final state", async () => {
  const d = await failureDiagnostic({ submit: false, beforeEmit: ({ active, polls }) => {
    for (let n = 0; n < 200; n++) { active.seeking = !active.seeking; for (const poll of polls) poll(); }
    active.paused = true; active.currentTime = .905578;
  } });
  assert.equal(d.events.length, 64); assert.ok(d.dropped > 0);
  assert.equal(d.events.at(-1).time, .905578); assert.equal(d.events.at(-1).paused, true);
});
test("early callback diagnosis preserves strict mediaTime gate and finite numeric schema", async () => {
  const d = await failureDiagnostic({ metadata: { mediaTime: .25, presentedFrames: Infinity, private: "SECRET" } });
  assert.equal(d.counts["early-frame"], 1);
  assert.equal(d.events.find(e => e.event === "early-frame").presentedFrames, null);
});
test("diagnostic consumer failure cannot alter preview acceptance", async () => {
  const f = fixture();
  const frames = await verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100, onDiagnostic: () => { throw new Error("consumer"); } });
  assert.equal(frames[0].energy, 255); f.assertClean();
});

test("failed diagnostic snapshot preserves successful acceptance and cleanup", async () => {
  const f = fixture({ snapshotThrows: true });
  const frames = await verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100 });
  assert.equal(frames[0].energy, 255); f.assertClean();
});
test("failed diagnostic snapshot preserves original Play error and cleanup", async () => {
  const f = fixture({ snapshotThrows: true, failClick: true });
  await assert.rejects(verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100 }), /Play action failed/);
  f.assertClean();
});

test("failed diagnostic writes cannot interrupt arm, callback or acceptance cleanup", async () => {
  const f = fixture({ recordThrows: true });
  const frames = await verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 100 });
  assert.equal(frames[0].energy, 255); f.assertClean();
});
test("failed diagnostic writes cannot interrupt the original deadline rejection", async () => {
  const f = fixture({ recordThrows: true, submit: false });
  await assert.rejects(verifyNativePreview(f.page, f.expect, { cycles: 1, timeout: 20 }), /No submitted playing preview frame/);
  f.assertClean();
});

// Replay evidence orderings, not the product's unknown Intel root cause. Both
// runs must still fail the original compositor criterion; only diagnosis differs.
test("diagnostics distinguish audio reaching end with lagging video from an early end", async () => {
  for (const observedTime of [3, .696598]) {
    const d = await failureDiagnostic({ submit: false, beforeEmit: ({ active, polls, dispatch }) => {
      active.currentTime = .696598; active.seeking = true;
      if (observedTime < 3) active.duration = .696598;
      dispatch("seeking", { target: active });
      dispatch("cutvoke:transport-clock", { detail: { time: observedTime, end: 3, playing: true, url: "SECRET" } });
      dispatch("cutvoke:preview-stop", { detail: { reason: observedTime === 3 ? "audio-clock-end" : "video-ended", observedTime, decisionEnd: 3, end: 3,
        windowStart: 0, windowEnd: 3, audioActive: true, nativeAudioReady: true, playing: true, prepared: true,
        private: "SECRET" } });
      active.paused = true; dispatch("pause", { target: active });
      active.seeking = false; dispatch("seeked", { target: active });
      for (const poll of polls) poll();
    } });
    assert.equal(d.stops.length, 1); assert.equal(d.stops[0].observedTime, observedTime);
    assert.equal(d.clocks[0].time, observedTime); assert.equal(d.stops[0].end, 3);
    assert.equal(d.media.find(e => e.event === "stop-snapshot").time, .696598);
    assert.equal(d.media.find(e => e.event === "media-seeked").paused, true);
    assert.equal(d.media[0].duration, observedTime === 3 ? 3 : .696598);
  }
});
test("media handoff diagnostics identify inactive node and retain clocks independently", async () => {
  const d = await failureDiagnostic({ submit: false, beforeEmit: ({ first, second, setActive, dispatch, polls }) => {
    first.currentTime = .29; setActive(second); second.currentTime = .696598;
    dispatch("pause", { target: first });
    dispatch("loadedmetadata", { target: second });
    for (let i = 0; i < 200; i++) dispatch("cutvoke:transport-clock", { detail: { time: i / 4, end: 3, playing: true } });
    for (const poll of polls) poll();
  } });
  assert.equal(d.media.find(e => e.event === "media-pause").active, false);
  assert.equal(d.media.find(e => e.event === "media-loadedmetadata").active, true);
  assert.equal(d.clocks.length, 64); assert.equal(d.clocksDropped, 136);
  assert.equal(d.clocks[0].time, 0); assert.equal(d.clocks.at(-1).time, 49.75);
  assert.equal(d.media.length, 2); assert.equal(d.mediaDropped, 0);
});
test("diagnostics ignore unknown reasons and hostile event getters, restore prior setting", async () => {
  const priorEvidence = { configurable: true, enumerable: false, writable: true, value: false };
  const f = fixture({ priorEvidence, beforeEmit: ({ dispatch, window }) => {
    assert.equal(window.__cutvokePreviewStopEvidence, true);
    dispatch("cutvoke:preview-stop", { detail: { reason: "SECRET" } });
    dispatch("cutvoke:preview-stop", { get detail() { throw new Error("SECRET"); } });
    dispatch("cutvoke:transport-clock", { detail: { get time() { throw new Error("SECRET"); } } });
  } });
  await verifyNativePreview(f.page, f.expect, { cycles: 1 }); f.assertClean();
});

function stopHook(window) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(new URL("../src/lib/previewStopEvidence.ts", import.meta.url), "utf8"),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText,
    { exports, window, CustomEvent: class { constructor(type, { detail }) { this.type = type; this.detail = detail; } } });
  return exports.recordPreviewStop;
}
test("product stop hook is disabled by default and never evaluates snapshot or clocks", () => {
  let reads = 0; const record = stopHook({ dispatchEvent: () => { throw new Error("must not dispatch"); } });
  record("audio-clock-end", () => { reads++; throw new Error("clock"); }); assert.equal(reads, 0);
});
test("product stop hook has a finite scalar allowlist and cannot throw into playback", () => {
  const events = [], window = { __cutvokePreviewStopEvidence: true, dispatchEvent: e => events.push(e) };
  const record = stopHook(window);
  record("audio-clock-end", () => ({ observedTime: 3, end: 3, playhead: .69, reportedVideoTime: .69,
    windowStart: 0, windowEnd: 3, audioActive: true, nativeAudioReady: true, audioAnchorTime: Infinity,
    playing: true, prepared: true, path: "SECRET", projectId: "SECRET", raw: {} }));
  assert.equal(events.length, 1); assert.equal(events[0].detail.audioAnchorTime, null);
  assert.doesNotMatch(JSON.stringify(events), /SECRET|path|projectId|raw/);
  record("SECRET", () => { throw new Error("must not snapshot"); });
  record("video-ended", () => ({ get end() { throw new Error("hostile"); } }));
  window.dispatchEvent = () => { throw new Error("listener"); };
  assert.doesNotThrow(() => record("image-end", () => ({})));
  Object.defineProperty(window, "__cutvokePreviewStopEvidence", { get() { throw new Error("hostile opt-in"); } });
  assert.doesNotThrow(() => record("image-end", () => ({})));
});

test("observer does not invoke or replace an occupied opt-in accessor", async () => {
  const priorEvidence = { configurable: false, enumerable: false, set: undefined, get() { throw new Error("foreign getter"); } };
  const f = fixture({ priorEvidence });
  await verifyNativePreview(f.page, f.expect, { cycles: 1 }); f.assertClean();
});
test("bounded media and stop channels retain first and final records without raw objects", async () => {
  const d = await failureDiagnostic({ submit: false, beforeEmit: ({ active, dispatch }) => {
    for (let i = 0; i < 100; i++) {
      active.currentTime = i; dispatch("seeked", { target: active });
      dispatch("cutvoke:preview-stop", { detail: { reason: "audio-clock-end", observedTime: i,
        decisionEnd: 3, end: 4, decisionWindowStart: 0, decisionWindowEnd: 3, windowStart: 2, windowEnd: 4,
        path: "SECRET", raw: active } });
    }
  } });
  assert.equal(d.stops.length, 64); assert.equal(d.stopsDropped, 36);
  assert.equal(d.stops[0].observedTime, 0); assert.equal(d.stops.at(-1).observedTime, 99);
  assert.equal(d.stops.at(-1).decisionEnd, 3); assert.equal(d.stops.at(-1).end, 4);
  assert.equal(d.media.length, 64); assert.equal(d.mediaDropped, 136);
});

test("opt-in seek observation preserves the native setter and correlates only active video writes", async () => {
  const diagnostics = [];
  const f = fixture({ mediaAccessor: true, handoff: true, beforeEmit: ({ first, second, dispatch, window, mediaDescriptor, nativeSeekError }) => {
    const installed = Object.getOwnPropertyDescriptor(window.HTMLMediaElement.prototype, "currentTime");
    assert.equal(installed.get, mediaDescriptor.get); assert.notEqual(installed.set, mediaDescriptor.set);
    const audio = new window.HTMLMediaElement(); audio.currentTime = .7;
    first.currentTime = .8;
    second.seeking = true;
    second.currentTime = .65;
    assert.throws(() => { second.currentTime = 999; }, error => error === nativeSeekError);
    dispatch("seeked", { target: second });
    second.seeking = false;
  } });
  const samples = await verifyNativePreview(f.page, f.expect, { cycles: 1, traceSeekWrites: true, onDiagnostic: d => diagnostics.push(d) });
  assert.equal(samples[0].energy, 255);
  assert.deepEqual(f.mediaWrites.map(([,value]) => value), [.7,.8,.65,999]);
  const d = diagnostics[0], seeked = d.media.find(entry => entry.event === "media-seeked"), accepted = d.events.find(entry => entry.event === "accepted");
  assert.equal(d.seekCoverage.status, "observed"); assert.equal(d.seekCoverage.absenceEvidence,true);
  assert.equal(d.seekWrites.length, 2);
  assert.equal(d.seekWrites[0].node,accepted.node); assert.equal(d.seekWrites[0].sourceGeneration,accepted.sourceGeneration);
  assert.equal(d.seekWrites[0].requestedTime,.65); assert.equal(d.seekWrites[0].time,.5);
  assert.equal(d.seekWrites[0].seeking,true); assert.equal(d.seekWrites[1].time,.65);
  assert.ok(d.seekCoverage.startOrdinal < d.seekWrites[0].ordinal);
  assert.ok(d.seekWrites[1].ordinal < seeked.ordinal && seeked.ordinal < accepted.ordinal && accepted.ordinal < d.seekCoverage.endOrdinal);
  assert.doesNotMatch(JSON.stringify(d), /private|SECRET|\.mp4|https:/);
  f.assertClean();
});
test("seek observation is opt-in and unavailable instrumentation cannot imply zero writes", async () => {
  for (const traceSeekWrites of [false, true]) {
    const diagnostics = [];
    const f = fixture({ mediaAccessor:true, mediaConfigurable:false, beforeEmit: ({active}) => {active.currentTime=.7;} });
    await verifyNativePreview(f.page, f.expect, {cycles:1, traceSeekWrites, onDiagnostic:d=>diagnostics.push(d)});
    assert.equal(f.mediaWrites.length,1);
    const d=diagnostics[0];
    if (traceSeekWrites) {
      assert.equal(d.seekCoverage.status,"unavailable"); assert.equal(d.seekCoverage.absenceEvidence,false);
      assert.equal(d.seekWrites.length,0); assert.equal(d.seekCoverage.startOrdinal,null);
    } else {
      assert.equal(d.seekCoverage,undefined); assert.equal(d.events[0].ordinal,undefined);
    }
    f.assertClean();
  }
});
test("capped seek trace cannot imply absence and still uses the original frame deadline", async () => {
  const diagnostics = [];
  const f=fixture({mediaAccessor:true, submit:false, beforeEmit:({active})=>{
    for(let i=0;i<70;i++)active.currentTime=.5+i/100;
  }});
  await assert.rejects(verifyNativePreview(f.page,f.expect,{cycles:1,timeout:20,traceSeekWrites:true,onDiagnostic:d=>diagnostics.push(d)}),/No submitted playing preview frame/);
  const d=diagnostics[0];
  assert.equal(d.seekWrites.length,64); assert.equal(d.seekWritesDropped,6); assert.equal(d.seekWrites[0].requestedTime,.5);
  assert.equal(d.seekCoverage.status,"incomplete"); assert.equal(d.seekCoverage.absenceEvidence,false);
  assert.equal(f.mediaWrites.length,70); assert.equal(f.pixelReads,0); f.assertClean();
});
test("seek observer restores its descriptor after black pixels, failed Play, and zero-write success", async () => {
  for (const [options, reason] of [[{black:true},/0 must exceed 0/],[{failClick:true},/Play action failed/],[{},null]]) {
    const diagnostics=[], f=fixture({mediaAccessor:true,...options});
    const run=verifyNativePreview(f.page,f.expect,{cycles:1,traceSeekWrites:true,onDiagnostic:d=>diagnostics.push(d)});
    if(reason) await assert.rejects(run,reason); else await run;
    assert.equal(diagnostics[0].seekWrites.length,0);
    assert.equal(diagnostics[0].seekCoverage.status,"observed");
    assert.equal(diagnostics[0].seekCoverage.absenceEvidence,true);
    f.assertClean();
  }
});

test("foreign descriptor changes and non-numeric active attempts invalidate absence evidence", async () => {
  for (const takeover of [false,true]) {
    const diagnostics=[];
    let foreign;
    const f=fixture({mediaAccessor:true, beforeEmit:({active,window})=>{
      const proto=window.HTMLMediaElement.prototype;
      if(takeover) {
        const installed=Object.getOwnPropertyDescriptor(proto,"currentTime");
        foreign={...installed,enumerable:false};
        Object.defineProperty(proto,"currentTime",foreign);
        active.currentTime=.6;
      } else {
        const unknown={valueOf(){throw new Error("observer must not coerce");}};
        // The fake native setter stores its original argument; restore a valid
        // media clock afterward, exactly as a real decoder would do.
        active.currentTime=unknown;
        active.currentTime=.6;
      }
    }});
    await verifyNativePreview(f.page,f.expect,{cycles:1,traceSeekWrites:true,onDiagnostic:d=>diagnostics.push(d)});
    const d=diagnostics[0];
    assert.equal(d.seekCoverage.status,"incomplete"); assert.equal(d.seekCoverage.absenceEvidence,false);
    assert.equal(d.seekWrites.length,1);
    if(takeover) {
      f.first.currentTime=.7; // surviving wrapper is inert after cleanup
      assert.equal(d.seekWrites.length,1);
      f.assertClean(foreign);
    } else {
      assert.equal(typeof f.mediaWrites[0][1],"object");
      f.assertClean();
    }
  }
});
