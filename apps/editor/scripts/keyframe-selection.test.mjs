import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import { create, act } from 'react-test-renderer';
import ts from 'typescript';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const rationalToSecs = value => Number(value.num) / Number(value.den);
const r = value => ({ num: String(value), den: '1' });
const key = (id, time = 1) => ({ id, time: r(time), value: .5, interpolation: 'linear' });
const clip = (keys, id = 'clip-one') => ({ id, timelineStart: r(0), timelineEnd: r(5), effects: [], keyframes: { opacity: keys } });

// Run the actual component and React effects; isolate only the store/transport
// and numerical graph dependencies. This does not replace browser acceptance.
async function harness(initial) {
  const calls = [], listeners = new Map();
  const state = { project: { sequence: { width: 160, height: 90, tracks: [] } }, editLock: null, playhead: 0 };
  const dependencies = {
    react: React, 'react/jsx-runtime': jsx,
    '../../i18n': { useLocale() {}, tr: (s, ...args) => s.replace(/\{(\d+)\}/g, (_, i) => args[i]) },
    'lucide-react': { Plus: () => null, Trash2: () => null },
    '../../store/editor': { useEditor: () => ({ state, dispatch() {} }) },
    '../../store/actions': { getLatestState: () => state, runCommand: async (...args) => { calls.push(args[3]); return { ok: true }; } },
    '../../store/clipEdit': { addKeyframe: async () => {}, removeKeyframe: async () => {} },
    '../../lib/rational': { rationalToSecs, secsToRational: r },
    '../../lib/keyframes': { evaluateKeyframes: () => .5, keyframeVelocity: () => 0 },
  };
  const source = readFileSync(new URL('../src/components/inspector/KeyframeSection.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => { assert.ok(dependencies[name], name); return dependencies[name]; },
    window: { addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) } });
  let tree;
  await act(async () => { tree = create(React.createElement(exports.KeyframeSection, { clip: initial })); });
  return {
    calls, tree,
    update: async value => act(async () => tree.update(React.createElement(exports.KeyframeSection, { clip: value }))),
    select: async id => act(async () => tree.root.findByProps({ 'aria-label': `选择关键帧 ${id}` }).props.onChange()),
    button: label => tree.root.findAllByType('button').find(node => node.children.includes(label)),
    checked: id => tree.root.findByProps({ 'aria-label': `选择关键帧 ${id}` }).props.checked,
    close: async () => act(async () => tree.unmount()),
  };
}

for (const reason of ['delete selected key', 'undo key creation']) {
  test(`${reason} removes invalid action targets after authoritative refresh`, async () => {
    const h = await harness(clip([key('a')]));
    try {
      await h.select('a'); assert.equal(h.checked('a'), true);
      await h.update(clip([]));
      assert.equal(Boolean(h.button('更新节点')), false);
      assert.equal(h.button('线性').props.disabled, true);
      await h.update(clip([key('a')])); // Redo must not resurrect obsolete selection.
      assert.equal(h.checked('a'), false);
      assert.equal(h.calls.length, 0);
    } finally { await h.close(); }
  });
}

test('partial deletion retains surviving selection and submits only that ID', async () => {
  const h = await harness(clip([key('a'), key('b', 2)]));
  try {
    await h.select('a'); await h.select('b'); await h.update(clip([key('b', 2)]));
    assert.equal(h.checked('b'), true);
    await act(async () => h.button('线性').props.onClick());
    assert.deepEqual(Array.from(h.calls[0].keyframeIds), ['b']);
  } finally { await h.close(); }
});

test('retime undo redo and an unrelated refresh preserve stable selected IDs', async () => {
  const h = await harness(clip([key('a'), key('b', 2)]));
  try {
    await h.select('a');
    for (const time of [3, 1, 3]) { await h.update(clip([key('a', time), key('b', 2)])); assert.equal(h.checked('a'), true); }
    await h.select('b'); // newer user intent before a delayed unrelated refresh
    await h.update(clip([key('a', 3), key('b', 2)]));
    assert.equal(h.checked('a'), true); assert.equal(h.checked('b'), true);
  } finally { await h.close(); }
});

test('switching clips clears selection even when key IDs overlap', async () => {
  const h = await harness(clip([key('a')]));
  try { await h.select('a'); await h.update(clip([key('a')], 'clip-two')); assert.equal(h.checked('a'), false); }
  finally { await h.close(); }
});

test('switching parameter channels clears selection', async () => {
  const h = await harness({ ...clip([key('a')]), keyframes: { opacity: [key('a')], volume: [key('a')] } });
  try {
    await h.select('a');
    await act(async () => h.tree.root.findByProps({ 'aria-label': '关键帧参数' }).props.onChange({ target: { value: 'volume' } }));
    assert.equal(h.checked('a'), false);
  } finally { await h.close(); }
});
