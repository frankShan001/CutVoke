import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

// Run the real hook effects with controlled server/client ordering, not timers.
async function prepare({ outcome = "cancelled", revision = "3", leave = false } = {}) {
  const effects = [], actions = [], values = [], refreshes = [];
  const project = { revision: "2", sequence: { tracks: [{ kind: "video", clips: [{}] }] } };
  const state = { currentId: "p", project, view: "editor", opening: false };
  let latest = state;
  class ApiFailure extends Error { constructor(data) { super(data.message); Object.assign(this, data); } }
  const deps = {
    react: {
      useEffect: fn => effects.push(fn), useMemo: fn => fn(), useRef: current => ({ current }),
      useState: initial => { const i = values.push(initial) - 1; return [initial, value => { values[i] = typeof value === "function" ? value(values[i]) : value; }]; },
    },
    "../i18n": { tr: value => value },
    "../store/editor": { useEditor: () => ({ state, dispatch: action => actions.push(action) }) },
    "../store/actions": {
      getLatestState: () => latest,
      refreshProject: async (_dispatch, id, options) => {
        refreshes.push({ id, options, clientRevision: latest.project.revision });
        options.onRefreshedRevision(revision);
        // React has not committed PROJECT_LOADED or rebound latest state yet.
        latest = { ...latest, currentId: leave ? "other" : "p" };
        return true;
      },
    },
    "../lib/api": { ApiFailure },
    "../lib/previewBudget": { usePreviewBudget: () => ({ enabled: true, continuous: true, delayMs: 0, wallSeconds: 100 }) },
    "../lib/previewPreparation": {
      previewContentKey: () => "content",
      // Server has already cancelled revision 2; the command ACK is still held,
      // so the client revision is also 2 when the terminal job is observed.
      previewRequest: async () => ({ jobId: "old", state: outcome, phase: "rendering", progress: 10, error: "render cancelled before completion" }),
    },
  };
  const source = readFileSync(new URL("../src/components/ProjectLoading.tsx", import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => deps[name] || {}, AbortController, DOMException, Error,
    clearTimeout: () => {}, window: { setTimeout: () => 1, addEventListener: () => {}, removeEventListener: () => {} } });
  exports.useProjectPreparation();
  const cleanup = effects.map(effect => effect());
  await new Promise(resolve => setImmediate(resolve));
  for (const fn of cleanup) fn?.();
  return { actions, values, refreshes };
}
test("server cancellation before command ACK reconciles quietly and retries the advanced revision", async () => {
  const h = await prepare();
  assert.equal(h.refreshes.length, 1);
  assert.equal(h.refreshes[0].clientRevision, "2");
  assert.equal(h.refreshes[0].options.quiet, true);
  assert.equal(h.values[0], 1);
  assert.equal(h.actions.some(a => a.type === "STATUS_SET"), false);
});
test("genuine cancellation at the same revision remains visible", async () => {
  const h = await prepare({ revision: "2" });
  assert.equal(h.refreshes.length, 1);
  assert.equal(h.values[0], 0);
  assert.equal(h.actions.find(a => a.type === "STATUS_SET")?.text, "render cancelled before completion");
});
test("real render failures remain visible without reconciliation", async () => {
  const h = await prepare({ outcome: "failed" });
  assert.equal(h.refreshes.length, 0);
  assert.equal(h.actions.find(a => a.type === "STATUS_SET")?.severity, "warn");
});
test("leaving the project during cancellation reconciliation publishes no stale feedback or retry", async () => {
  const h = await prepare({ leave: true });
  assert.equal(h.refreshes.length, 1);
  assert.equal(h.values[0], 0);
  assert.equal(h.actions.some(a => a.type === "STATUS_SET"), false);
});
