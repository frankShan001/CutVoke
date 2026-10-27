import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const moduleUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const scope = moduleUrl(ts.transpileModule(readFileSync(new URL("../src/lib/projectScope.ts", import.meta.url), "utf8"),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText);
const { setActiveProject } = await import(scope);
const mediaApi = moduleUrl(`
  export async function uploadAsset(file, options = {}) {
    globalThis.__mediaUploadCalls.push(file.name);
    options.onProgress?.(64, file.size);
    if (options.signal?.aborted) return {kind: "error", message: "cancelled"};
    return {kind: "ok", data: {assetId: file.name, name: file.name, path: file.name, size: file.size}};
  }
  export async function probeMedia() {
    return {kind: "ok", data: {duration: globalThis.__mediaProbeDuration, has_video: false, has_audio: true}};
  }
`);
const assetStore = moduleUrl(`
  export function addSessionAsset(asset) { globalThis.__mediaStoredAssets.push(asset); }
  export function inferKind() { return "audio"; }
`);
const source = readFileSync(new URL("../src/lib/importMedia.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { uploadOneFile, uploadFiles } = await import(moduleUrl(compiled
  .replace('"./mediaApi"', JSON.stringify(mediaApi)).replace('"./assetStore"', JSON.stringify(assetStore))
  .replace('"./projectScope"', JSON.stringify(scope))));

test("a 1.860-second probe result reaches session metadata without tenth-second rounding", async () => {
  globalThis.__mediaProbeDuration = 1.86;
  globalThis.__mediaUploadCalls = [];
  globalThis.__mediaStoredAssets = [];
  const result = await uploadOneFile({ name: "precise.wav", size: 100 });
  assert.equal(result.ok, true);
  assert.equal(result.media.duration, 1.86);
  assert.equal(globalThis.__mediaStoredAssets[0].duration, 1.86);
  assert.notEqual(result.media.duration, 1.9);
});

test("switching projects during a batch never uploads remaining files into the next project", async () => {
  setActiveProject("one");
  globalThis.__mediaProbeDuration = 1;
  globalThis.__mediaUploadCalls = [];
  globalThis.__mediaStoredAssets = [];
  const count = await uploadFiles([{ name: "one.wav", size: 128 }, { name: "two.wav", size: 128 }],
    () => setActiveProject("two"));
  assert.equal(count, 1);
  assert.deepEqual(globalThis.__mediaUploadCalls, ["one.wav"]);
});

test("switching projects during an in-flight upload never inserts it into the next timeline", async () => {
  setActiveProject("before");
  globalThis.__mediaUploadCalls = [];
  globalThis.__mediaStoredAssets = [];
  let inserted = 0;
  const count = await uploadFiles([{ name: "in-flight.wav", size: 128 }], () => inserted++, undefined,
    progress => { if (progress.loaded) setActiveProject("after"); });
  assert.equal(count, 0);
  assert.equal(inserted, 0);
  assert.deepEqual(globalThis.__mediaStoredAssets, []);
});

test("cancelling an upload stops the remaining queue while preserving actual byte progress and filename", async () => {
  globalThis.__mediaProbeDuration = 1.86;
  globalThis.__mediaUploadCalls = [];
  globalThis.__mediaStoredAssets = [];
  const controller = new AbortController();
  const observed = [], errors = [];
  const count = await uploadFiles([{ name: "first.wav", size: 128 }, { name: "never.wav", size: 128 }], () => {},
    (...error) => errors.push(error), progress => { observed.push(progress); if (progress.loaded === 64) controller.abort(); },
    { signal: controller.signal });
  assert.equal(count, 0);
  assert.deepEqual(globalThis.__mediaUploadCalls, ["first.wav"]);
  assert.deepEqual(errors, []);
  assert.equal(observed[0].name, "first.wav");
  assert.equal(observed[0].loaded, 0);
  assert.equal(observed.at(-1).loaded, 64);
  assert.equal(observed.at(-1).totalBytes, 128);
});
