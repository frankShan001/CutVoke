import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const source = readFileSync(new URL("../src/lib/previewSegmentCache.ts", import.meta.url), "utf8")
  .replace('import { tr } from "../i18n";', 'const tr = (message) => message;');
const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
const { PreviewSegmentCache } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const flush = () => new Promise(resolve => setImmediate(resolve));

test("a foreground seek preempts lookahead and an immediate return remains retryable", async () => {
  const previousFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = (url, { signal }) => new Promise((resolve, reject) => {
    const entry = { url, signal, finish: () => resolve(new Response(new Blob(["encoded media"]))) };
    requests.push(entry);
    signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
  });
  const cache = new PreviewSegmentCache();
  const leases = [];
  try {
    const speculative = cache.acquire("/preview-window?index=1", true); leases.push(speculative);
    const oldResult = speculative.promise.catch(error => error);
    const current = cache.acquire("/preview-window?index=0"); leases.push(current);
    const seek = cache.acquire("/preview-window?index=7"); leases.push(seek);
    const returned = cache.acquire("/preview-window?index=1"); leases.push(returned);
    assert.equal(requests[0].signal.aborted, true);
    await flush();
    assert.equal((await oldResult).name, "AbortError");
    requests[1].finish(); await current.promise; await flush();
    const fresh = requests.find(item => item.url === "/preview-window?index=1");
    assert.ok(fresh, "returning to the preempted position starts a fresh demand request");
    fresh.finish(); await returned.promise; await flush();
    const far = requests.find(item => item.url === "/preview-window?index=7");
    assert.ok(far); far.finish(); await seek.promise;
    assert.ok(cache.peek("/preview-window?index=1"));
    assert.ok(cache.peek("/preview-window?index=7"));
  } finally {
    for (const lease of leases) { lease.promise.catch(() => {}); lease.release(); }
    cache.dispose(); globalThis.fetch = previousFetch;
  }
});

const cancelledResponse = () => new Response(JSON.stringify({ error: { code: "PREVIEW_CANCELLED", message: "new snapshot available" } }), { status: 409 });
const validResponse = () => new Response(new Blob(["valid media"]));
test("current same-source cancellation retries a fresh snapshot without poisoning the cache", async () => {
  const oldFetch = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? cancelledResponse() : validResponse();
  const cache = new PreviewSegmentCache(), lease = cache.acquire("/same-gain-source");
  try { await lease.promise; assert.equal(calls, 2); assert.ok(cache.peek("/same-gain-source")); }
  finally { lease.release(); cache.dispose(); globalThis.fetch = oldFetch; }
});
test("upgrading a cancelled in-flight prefetch retries as foreground", async () => {
  const oldFetch = globalThis.fetch, urls = []; let finish;
  globalThis.fetch = async url => { urls.push(url); return urls.length === 1 ? new Promise(resolve => { finish = resolve; }) : validResponse(); };
  const cache = new PreviewSegmentCache(), background = cache.acquire("/preview-window", true), foreground = cache.acquire("/preview-window");
  try { finish(cancelledResponse()); await foreground.promise; assert.equal(urls.length, 2); assert.match(urls[0], /prefetch=1/); assert.equal(urls[1], "/preview-window"); }
  finally { background.release(); foreground.release(); cache.dispose(); globalThis.fetch = oldFetch; }
});
for (const release of ["release", "dispose"]) test(`${release} during cancelled retry prevents another request`, async () => {
  const oldFetch = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; return cancelledResponse(); };
  const cache = new PreviewSegmentCache(), lease = cache.acquire("/old");
  const result = lease.promise.catch(e => e);
  try { await flush(); if (release === "release") lease.release(); else cache.dispose(); assert.equal((await result).name, "AbortError"); assert.equal(calls, 1); }
  finally { lease.release(); cache.dispose(); globalThis.fetch = oldFetch; }
});
test("repeated cancellation is bounded and exposes exhaustion instead of hanging", async () => {
  const oldFetch = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; return cancelledResponse(); };
  const cache = new PreviewSegmentCache(), lease = cache.acquire("/live");
  try { const error = await lease.promise.catch(e => e); assert.equal(error.name, "Error"); assert.equal(calls, 3); assert.equal(cache.peek("/live"), null); }
  finally { lease.release(); cache.dispose(); globalThis.fetch = oldFetch; }
});
for (const [status, code] of [[422, "PREVIEW_FAILED"], [409, "REVISION_CONFLICT"], [422, "PREVIEW_CANCELLED"]]) {
  test(`real failure is not classified from its message: ${status}/${code}`, async () => {
    const oldFetch = globalThis.fetch; let calls = 0;
    globalThis.fetch = async () => { calls++; return new Response(JSON.stringify({ error: { code, message: "preview superseded by a newer edit" } }), { status }); };
    const cache = new PreviewSegmentCache(), lease = cache.acquire("/broken");
    try { assert.equal((await lease.promise.catch(e => e)).name, "Error"); assert.equal(calls, 1); }
    finally { lease.release(); cache.dispose(); globalThis.fetch = oldFetch; }
  });
}

// Exercise the real hook so old failures cannot leak into a same-source retry.
const React = await import("react");
const { create, act } = await import("react-test-renderer");
const { runInNewContext } = await import("node:vm");
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const hookSource = readFileSync(new URL("../src/components/usePreviewSegments.ts", import.meta.url), "utf8");
const hookCode = ts.transpileModule(hookSource, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
const hookExports = {};
runInNewContext(hookCode, { exports: hookExports, DOMException, Error, queueMicrotask,
  require: name => ({ react: React, "../i18n": { tr: s => s }, "../lib/previewSegmentCache": { PreviewSegmentCache } })[name] });
test("same-source hook retry clears old error, retains cancellation silence and reaches ready", async () => {
  const oldFetch = globalThis.fetch, requests = [], results = [], consumedErrors = [];
  globalThis.fetch = (url, { signal }) => new Promise((resolve, reject) => {
    requests.push({ url, resolve });
    signal.addEventListener("abort", () => reject(new DOMException("closed", "AbortError")), { once: true });
  });
  let tree;
  function Hook({ enabled }) {
    const result = hookExports.usePreviewSegments("scope", "/same", enabled);
    results.push(result);
    React.useEffect(() => { if (result.error) consumedErrors.push(result.error); }, [result.error]);
    return null;
  }
  try {
    await act(async () => { tree = create(React.createElement(Hook, { enabled: true })); });
    await act(async () => { requests[0].resolve(new Response(JSON.stringify({ error: { code: "PREVIEW_FAILED", message: "real failure" } }), { status: 422 })); await flush(); });
    assert.equal(results.at(-1).error, "real failure");
    assert.deepEqual(consumedErrors, ["real failure"]);
    await act(async () => { tree.update(React.createElement(Hook, { enabled: false })); });
    await act(async () => { tree.update(React.createElement(Hook, { enabled: true })); });
    assert.equal(results.at(-1).error, null);
    await act(async () => { requests[1].resolve(cancelledResponse()); await new Promise(resolve => setTimeout(resolve, 225)); });
    assert.equal(results.at(-1).error, null);
    assert.equal(requests.length, 3);
    await act(async () => { requests[2].resolve(validResponse()); await flush(); });
    assert.ok(results.at(-1).media); assert.equal(results.at(-1).error, null);
    assert.deepEqual(consumedErrors, ["real failure"], "consumer never sees the old failure again after re-enabling");
  } finally { if (tree) await act(async () => tree.unmount()); globalThis.fetch = oldFetch; }
});
