import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

function moduleAt(path, dependencies, extra = "", globals = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8") + extra;
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => dependencies[name] || {}, Date, BigInt,
    console, structuredClone, setTimeout, clearTimeout, ...globals });
  return exports;
}
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const sync = moduleAt("../src/lib/projectSync.ts", {});
const { reducer, initialState } = moduleAt("../src/store/editor.tsx", {
  react: { createContext: () => ({}) }, "../lib/projectSync": sync,
}, "\nexport { reducer, initialState };\n");
const r = value => ({ num: String(value), den: "1" });
const makeProject = (id = "p", revision = "1") => ({ projectId: id, revision,
  sequence: { tracks: [{ id: "v", kind: "video", clips: [0, 1].map(index => ({ id: `c${index}`,
    assetRef: { sourcePath: `/fixture/${index}.mp4` }, timelineStart: r(index * 2), timelineEnd: r(index * 2 + 2), effects: [] })) }], captions: [] } });

async function harness(interact, { target = "c0", removeTarget = false, selectedEffect = false } = {}) {
  const refreshed = deferred(), refreshStarted = deferred(), actions = [], events = [];
  let state = { ...initialState, currentId: "p", revision: "1", project: makeProject(),
    selection: { trackId: "v", clipId: "c0" }, selectedClipIds: ["c0"] };
  if (selectedEffect) {
    state.project.sequence.tracks[0].clips[0].effects.push({ effectId: "old.effect", params: {} });
    state.selection.effectId = "old.effect";
  }
  class ApiFailure extends Error { constructor(data) { super(data.message); Object.assign(this, data); } }
  const actionsModule = moduleAt("../src/store/actions.ts", {
    "../lib/api": { sendCommand: async () => ({ revision: "2" }), newCommandId: () => "resource-command", ApiFailure,
      getProject: () => { refreshStarted.resolve(); return refreshed.promise; } },
    "../i18n": { msg: value => value }, "../lib/projectSync": sync,
  });
  const dispatch = action => { actions.push(action); state = reducer(state, action); actionsModule.bindLatestState(state); };
  actionsModule.bindLatestState(state);
  const mod = moduleAt("../src/store/resourceEdit.ts", {
    "./actions": actionsModule,
    "../i18n": { msg: value => value }, "../lib/rational": { rationalToSecs: value => Number(value.num) / Number(value.den) },
    "../lib/effectDrag": { clipAcceptsEffect: () => true },
  }, "", { window: { dispatchEvent: event => events.push(event) }, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } } });
  const pending = mod.applyResourceToClip(dispatch, state,
    { effectId: "cutvoke.fx.edge", presetId: "edge-style", category: "filter" },
    { effectId: "cutvoke.fx.edge", name: "Edge", category: "filter" }, target);
  await refreshStarted.promise; // The real command ACK has landed; its refresh is held.
  interact?.(dispatch, () => state);
  const beforeCompletion = actions.length;
  const project = makeProject("p", "2");
  project.sequence.tracks[0].clips[0].effects.push({ effectId: "cutvoke.fx.edge", params: {} });
  if (removeTarget) project.sequence.tracks[0].clips = project.sequence.tracks[0].clips.filter(clip => clip.id !== target);
  refreshed.resolve(project);
  assert.equal((await pending).ok, true); // Superseding UI intent must not discard the saved command.
  return { state, events, completionActions: actions.slice(beforeCompletion) };
}

test("a current resource completion still selects its target and opens its editor", async () => {
  const h = await harness(null, { target: "c1" });
  assert.equal(h.state.selection.clipId, "c1"); assert.equal(h.state.playhead, 2);
  assert.equal(h.events.length, 1); assert.equal(h.events[0].detail.clipId, "c1");
});
test("reconciling an effect replaced by the saved preset still permits its current reveal", async () => {
  const h = await harness(null, { selectedEffect: true });
  assert.equal(h.state.selection.clipId, "c0"); assert.equal(h.events.length, 1);
  assert.equal(h.state.selection.effectId, undefined);
});

const interactions = {
  "new clip selection": dispatch => dispatch({ type: "CLIP_SELECTION_SET", clipIds: ["c1"], primaryId: "c1" }),
  "selection away and back": dispatch => {
    dispatch({ type: "CLIP_SELECTION_SET", clipIds: ["c1"], primaryId: "c1" });
    dispatch({ type: "CLIP_SELECTION_SET", clipIds: ["c0"], primaryId: "c0" });
  },
  "explicit deselection": dispatch => dispatch({ type: "RESET_SELECTION_FOR" }),
  "caption selection and clearing": dispatch => {
    dispatch({ type: "CAPTION_SELECTION_SET", captionId: "caption" });
    dispatch({ type: "CAPTION_SELECTION_SET", captionId: null });
  },
  "user seek": dispatch => dispatch({ type: "PLAYHEAD_SET", t: 1 }),
  "seek away and back": dispatch => {
    dispatch({ type: "PLAYHEAD_SET", t: 1 }); dispatch({ type: "PLAYHEAD_SET", t: 0 });
  },
  "leave and reopen the same project": dispatch => {
    dispatch({ type: "PROJECT_CLOSED" }); dispatch({ type: "PROJECT_SELECTED", projectId: "p" });
  },
  "switch to another project": dispatch => dispatch({ type: "PROJECT_SELECTED", projectId: "other" }),
};
for (const [name, interact] of Object.entries(interactions)) {
  test(`a resource completion after ${name} cannot select, seek or emit edit-effect`, async () => {
    const h = await harness(interact);
    assert.equal(h.events.length, 0);
    assert.equal(h.completionActions.some(action => ["SELECTION_SET", "PLAYHEAD_SET"].includes(action.type)), false);
    assert.equal(h.completionActions.some(action => action.type === "STATUS_SET" && action.text.startsWith("已应用")), false);
  });
}
test("deleting the target during its refresh prevents a stale reveal", async () => {
  const h = await harness(null, { removeTarget: true });
  assert.equal(h.events.length, 0); assert.equal(h.state.selection, null);
});
test("normal playback ticks do not cancel a current resource reveal", async () => {
  const h = await harness(dispatch => dispatch({ type: "PLAYHEAD_SET", t: .5, source: "transport" }));
  assert.equal(h.events.length, 1); assert.equal(h.state.selection.clipId, "c0");
});
