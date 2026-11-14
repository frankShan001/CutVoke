import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

// Exercise the actual action/reducer modules with controlled transport delivery.
// No React scheduler or wall-clock timing decides which response wins.
function moduleAt(path, dependencies, extra = "") {
  const source = readFileSync(new URL(path, import.meta.url), "utf8") + extra;
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => dependencies[name] || {}, Date, BigInt,
    console, structuredClone, setTimeout, clearTimeout });
  return exports;
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const sync = moduleAt("../src/lib/projectSync.ts", {});
const project = revision => ({ projectId: "p", revision, sequence: { tracks: [], captions: [] } });
function harness() {
  const response = deferred(), actions = [];
  const mod = moduleAt("../src/store/actions.ts", {
    "../lib/api": { getProject: () => response.promise },
    "../i18n": { msg: value => value },
    "../lib/projectSync": sync,
    "./editor": { showError: (dispatch, error) => {
      dispatch({ type: "ERROR_SET", error });
      dispatch({ type: "STATUS_SET", severity: "err", text: error.message });
    } },
  });
  const state = { currentId: "p", revision: "5", project: project("5"), status: { text: "edited" } };
  mod.bindLatestState(state);
  return { mod, response, actions, state, dispatch: action => actions.push(action) };
}
for (const outcome of ["success", "failure"]) {
  test(`an aborted reconciliation ${outcome} cannot publish state or feedback`, async () => {
    const h = harness(), controller = new AbortController();
    const pending = h.mod.refreshProject(h.dispatch, "p", { quiet: true, signal: controller.signal });
    controller.abort();
    if (outcome === "success") h.response.resolve(project("6")); else h.response.reject(new Error("obsolete failure"));
    assert.equal(await pending, false);
    assert.equal(h.actions.length, 0);
  });
}
test("an invalidated scope cannot publish even when its transport succeeds", async () => {
  const h = harness(); let current = true;
  const pending = h.mod.refreshProject(h.dispatch, "p", { quiet: true, isCurrent: () => current });
  current = false; h.response.resolve(project("6"));
  assert.equal(await pending, false); assert.equal(h.actions.length, 0);
});
test("a response captured before a newer same-project commit cannot roll it back", async () => {
  const h = harness();
  const pending = h.mod.refreshProject(h.dispatch, "p");
  h.mod.bindLatestState({ ...h.state, revision: "7", project: project("6") }); // command ACK precedes snapshot
  h.response.resolve(project("6"));
  assert.equal(await pending, false); assert.equal(h.actions.length, 0);
});
test("a superseded quiet refresh failure preserves newer edit feedback", async () => {
  const h = harness();
  const pending = h.mod.refreshProject(h.dispatch, "p", { quiet: true });
  h.mod.bindLatestState({ ...h.state, revision: "6", status: { text: "new edit succeeded" } });
  h.response.reject(new Error("older refresh failed"));
  assert.equal(await pending, false); assert.equal(h.actions.length, 0);
});
test("a live quiet refresh still reports genuine transport failure", async () => {
  const h = harness();
  const pending = h.mod.refreshProject(h.dispatch, "p", { quiet: true });
  h.response.reject(new Error("current failure"));
  assert.equal(await pending, false);
  assert.equal(h.actions.find(action => action.type === "ERROR_SET").error.message, "current failure");
});
test("a live quiet success publishes its snapshot without replacing edit feedback", async () => {
  const h = harness();
  const pending = h.mod.refreshProject(h.dispatch, "p", { quiet: true });
  h.response.resolve(project("6")); assert.equal(await pending, true);
  assert.equal(h.actions.find(action => action.type === "PROJECT_LOADED").project.revision, "6");
  assert.equal(h.actions.some(action => action.type === "STATUS_SET"), false);
});
test("the reducer also rejects old snapshots between command ACK and snapshot delivery", () => {
  const { reducer, initialState } = moduleAt("../src/store/editor.tsx", {
    react: { createContext: () => ({}) },
    "../lib/projectSync": sync,
  }, "\nexport { reducer, initialState };\n");
  const state = { ...initialState, currentId: "p", project: project("5"), revision: "6", syncState: "saving" };
  assert.equal(reducer(state, { type: "PROJECT_LOADED", project: project("5") }), state);
  const next = reducer(state, { type: "PROJECT_LOADED", project: project("6") });
  assert.equal(next.revision, "6"); assert.equal(next.syncState, "saved");
});
for (const outcome of ["success", "failure"]) {
  test(`a reviewed command ${outcome} cannot publish into an invalidated editor`, async () => {
    const response = deferred(), actions = []; let current = true;
    class Failure extends Error { constructor(data) { super(data.message); Object.assign(this, data); } }
    const mod = moduleAt("../src/store/actions.ts", {
      "../lib/api": { sendCommand: () => response.promise, newCommandId: () => "reviewed", ApiFailure: Failure },
      "../lib/projectSync": sync,
    });
    const pending = mod.runCommand(action => actions.push(action), { currentId: "p", revision: "5" },
      "transcript.delete", { captionIds: ["c"] }, { retryConflict: false, isCurrent: () => current });
    current = false;
    if (outcome === "success") response.resolve({ revision: "6" }); else response.reject(new Failure({ code: "NETWORK", message: "lost response" }));
    assert.equal((await pending).ok, outcome === "success");
    assert.equal(actions.length, 1); assert.equal(actions[0].type, "SYNC_STATE_SET");
  });
}
test("a reviewed deletion never retries a conflict at a different revision", async () => {
  let calls = 0;
  class Failure extends Error { constructor(data) { super(data.message); Object.assign(this, data); } }
  const mod = moduleAt("../src/store/actions.ts", {
    "../lib/api": { sendCommand: async () => { calls++; throw new Failure({ code: "REVISION_CONFLICT", retryable: true, message: "changed" }); },
      newCommandId: () => "reviewed", ApiFailure: Failure },
    "../lib/projectSync": sync,
  });
  const result = await mod.runCommand(() => {}, { currentId: "p", revision: "5" }, "transcript.delete", { captionIds: ["c"] },
    { retryConflict: false, autoShow: false });
  assert.equal(result.ok, false); assert.equal(calls, 1); assert.equal(result.error.code, "REVISION_CONFLICT");
});

test("clock and user-seek bookkeeping preserve editing-only subscriptions, while edits refresh them", () => {
  let state, cursor = 0; const slots = [], dispatch = () => {};
  const { EditorProvider, initialState } = moduleAt("../src/store/editor.tsx", {
    "../i18n": { tr: value => value, useLocale: () => "zh-CN" },
    "react/jsx-runtime": { jsx: (type, props) => ({ type, props }) },
    react: { createContext: () => ({ Provider: {} }), useReducer: () => [state, dispatch], useEffect: () => {},
      useRef: value => { const index = cursor++; return slots[index] ||= { current: value }; },
      useMemo: (create, dependencies) => {
        const index = cursor++, prior = slots[index];
        if (prior && dependencies.every((value, at) => Object.is(value, prior.dependencies[at]))) return prior.value;
        const value = create(); slots[index] = { dependencies, value }; return value;
      } },
  }, "\nexport { initialState };\n");
  state = initialState;
  const render = () => { cursor = 0; return EditorProvider({ children: null }).props.children.props.value; };
  const before = render();
  for (let tick = 1; tick <= 60; tick++) {
    state = { ...state, playhead: tick / 60, playheadSeekVersion: tick, lastSync: tick };
    assert.equal(render(), before); // A ruler drag must not invalidate every media/clip row.
  }
  state = { ...state, revision: "1" };
  assert.notEqual(render(), before);
});

test("refresh exposes accepted server revision before React binds its snapshot", async () => {
  const h = harness(); let observed;
  const pending = h.mod.refreshProject(h.dispatch, "p", { quiet: true, onRefreshedRevision: revision => { observed = revision; } });
  h.response.resolve(project("6"));
  assert.equal(await pending, true);
  assert.equal(observed, "6");
  assert.equal(h.mod.getLatestState().project.revision, "5");
});
test("superseded refresh does not expose a revision for retry", async () => {
  const h = harness(); let observed;
  const pending = h.mod.refreshProject(h.dispatch, "p", { onRefreshedRevision: revision => { observed = revision; } });
  h.mod.bindLatestState({ ...h.state, revision: "7" });
  h.response.resolve(project("6"));
  assert.equal(await pending, false);
  assert.equal(observed, undefined);
});
