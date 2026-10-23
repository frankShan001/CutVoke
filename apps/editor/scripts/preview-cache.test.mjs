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
