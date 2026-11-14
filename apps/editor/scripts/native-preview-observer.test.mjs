import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { createRequire } from "node:module";
const { verifyNativePreview } = createRequire(import.meta.url)("../../../tools/verify/verify_native_preview.cjs");

// Deterministically model compositor submission while Play's automation RPC is
// still pending. The helper under test (not a duplicate observer) runs in a DOM
// sandbox. Real browser/native tests separately cover decoding and rendering.
function fixture({ black = false, failClick = false, submit = true, handoff = false } = {}) {
  const polls = new Set(), timers = new Set(), events = [];
  let id = 0, drawn = null, pixelReads = 0;
  const makeVideo = source => ({
    paused: true, seeking: false, readyState: 4, currentTime: 0,
    videoWidth: 1, videoHeight: 1, currentSrc: source, callbacks: new Map(),
    requestVideoFrameCallback(callback) { this.callbacks.set(++id, callback); return id; },
    cancelVideoFrameCallback(handle) { this.callbacks.delete(handle); },
  });
  const first = makeVideo("window"), second = makeVideo("prepared");
  let active = first;
  const context = vm.createContext({
    window: {},
    document: {
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
      callback(0, { mediaTime: .5, presentedFrames: 2 });
    }
  };
  const video = { evaluate: callback => callback(active) };
  const ruler = { getAttribute: async () => String(active.currentTime), focus: async () => {}, press: async () => {} };
  const page = {
    locator: () => video,
    evaluate: async (callback, arg) => { context.arg = arg; return vm.runInContext(`(${callback.toString()})(arg)`, context); },
    getByRole: (role, { name }) => role === "slider" ? ruler : { click: async () => {
      if (name === "暂停") { active.paused = true; events.push("pause"); return; }
      if (failClick) throw new Error("Play action failed");
      active.paused = false; active.currentTime = .5;
      for (const poll of polls) poll();
      if (handoff) {
        active = second; active.paused = false; active.currentTime = .5;
        emit(first); // A queued callback from the former pool source is invalid.
        assert.equal(pixelReads, 0);
        for (const poll of polls) poll();
      }
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
  return { page, expect, events, first, second, get drawn() { return drawn; }, get pixelReads() { return pixelReads; },
    assertClean() { assert.equal(polls.size, 0); assert.equal(timers.size, 0); assert.deepEqual(Object.keys(context.window), []); assert.equal(first.callbacks.size + second.callbacks.size, 0); } };
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
