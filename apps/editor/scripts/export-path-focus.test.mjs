import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import { create, act } from 'react-test-renderer';
import ts from 'typescript';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
async function harness(draft = null, deferJobs = false) {
  let resolveJobs;
  const jobs = new Promise(resolve => { resolveJobs = resolve; });
  if (!deferJobs) resolveJobs([]);
  let resolveDirectory;
  const directory = new Promise(resolve => { resolveDirectory = resolve; });
  const state = { currentId: 'p', project: { sequence: { tracks: [] } } };
  const dispatch = () => {};
  const text = s => s;
  const node = tag => props => React.createElement(tag, props);
  const dependencies = {
    react: React, 'react/jsx-runtime': jsx,
    '../i18n': { useLocale() {}, tr: text, msg: text },
    'lucide-react': new Proxy({}, { get: () => () => null }),
    './ui': { Button: node('button'), Field: node('div'), Select: node('select'), TextInput: node('input'), Badge: node('span') },
    '../store/editor': { useEditor: () => ({ state, dispatch }), showError() {} },
    '../store/actions': { getLatestState: () => state },
    '../lib/api': { listExportJobs: () => jobs },
    '../lib/assetStore': { useSessionAssets() {} }, '../lib/preflight': {},
    '../lib/rational': { rationalToSecs: () => 0 },
    '../lib/projectDirectories': { getProjectDirectory: () => directory },
  };
  const source = readFileSync(new URL('../src/components/ExportVideoSection.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => { assert.ok(dependencies[name], name); return dependencies[name]; },
    window: {}, localStorage: { getItem: () => JSON.stringify(draft), setItem() {} }, setTimeout, clearTimeout, setInterval, clearInterval });
  let tree;
  await act(async () => { tree = create(React.createElement(exports.ExportVideoSection, { onClose() {} })); });
  const input = () => tree.root.findAllByType('input').find(n => n.props['aria-label'] === '输出文件路径');
  return { input,
    restore: job => act(async () => resolveJobs([job])),
    focus: () => act(async () => input().props.onFocus?.()),
    change: value => act(async () => input().props.onChange({ target: { value } })),
    resolve: () => act(async () => resolveDirectory({ exportDirectory: '/project/exports' })),
    close: () => act(async () => tree.unmount()),
  };
}

test('late directory metadata cannot insert a default between focus and first input', async () => {
  const h = await harness();
  try {
    await h.focus(); await h.resolve();
    assert.equal(h.input().props.value, '');
    await h.change('/project/exports/authored.mp4');
    assert.equal(h.input().props.value, '/project/exports/authored.mp4');
  } finally { await h.close(); }
});
test('untouched input still receives its default', async () => {
  const h = await harness();
  try { await h.resolve(); assert.equal(h.input().props.value, '/project/exports/video.mp4'); }
  finally { await h.close(); }
});
for (const value of ['', '/authored.mp4']) test(`late metadata preserves authored value ${JSON.stringify(value)}`, async () => {
  const h = await harness();
  try { await h.change(value); await h.resolve(); assert.equal(h.input().props.value, value); }
  finally { await h.close(); }
});
test('restored draft is preserved', async () => {
  const h = await harness({ outPath: '/saved.mp4' });
  try { await h.resolve(); assert.equal(h.input().props.value, '/saved.mp4'); }
  finally { await h.close(); }
});

test('directory arriving before focus keeps the normal default', async () => {
  const h = await harness();
  try { await h.resolve(); await h.focus(); assert.equal(h.input().props.value, '/project/exports/video.mp4'); }
  finally { await h.close(); }
});
test('focus then blur without typing allows the deferred default', async () => {
  const h = await harness();
  try {
    await h.focus(); await act(async () => h.input().props.onBlur?.()); await h.resolve();
    assert.equal(h.input().props.value, '/project/exports/video.mp4');
    assert.equal(h.input().props.placeholder, '/project/exports/video.mp4');
  } finally { await h.close(); }
});

test('blur after metadata arrives restores the untouched default', async () => {
  const h = await harness();
  try {
    await h.focus(); await h.resolve(); assert.equal(h.input().props.value, '');
    await act(async () => h.input().props.onBlur());
    assert.equal(h.input().props.value, '/project/exports/video.mp4');
  } finally { await h.close(); }
});
test('explicitly cleared input stays empty after blur', async () => {
  const h = await harness();
  try {
    await h.focus(); await h.change(''); await h.resolve();
    await act(async () => h.input().props.onBlur());
    assert.equal(h.input().props.value, '');
  } finally { await h.close(); }
});

const exportJob = status => ({ jobId: 'job-one', status, outPath: '/project/exports/history.mp4',
  quality: 'low', createdAt: Date.now() / 1000,
  result: status === 'succeeded' ? { output_path: '/project/exports/history.mp4', duration: 2, width: 320, height: 180 } : null });

test('late completed job cannot replace focused input before its first change', async () => {
  const h = await harness(null, true);
  try {
    await h.focus(); await h.restore(exportJob('succeeded')); await h.resolve();
    assert.equal(h.input().props.value, '');
    assert.equal(h.input().props.disabled, false);
    // This untouched new draft uses the directory default on blur, not a
    // previous completed job's destination. The old result remains separate.
    await act(async () => h.input().props.onBlur());
    assert.equal(h.input().props.value, '/project/exports/video.mp4');
  } finally { await h.close(); }
});
test('late completed job preserves an authored path', async () => {
  const h = await harness(null, true);
  try {
    await h.focus(); await h.change('/project/exports/authored.mp4');
    await h.restore(exportJob('succeeded')); await h.resolve();
    assert.equal(h.input().props.value, '/project/exports/authored.mp4');
  } finally { await h.close(); }
});
test('completed job still restores its path before input focus', async () => {
  const h = await harness(null, true);
  try {
    await h.restore(exportJob('succeeded')); await h.resolve(); await h.focus();
    assert.equal(h.input().props.value, '/project/exports/history.mp4');
  } finally { await h.close(); }
});
for (const status of ['queued', 'running']) test(`late ${status} job remains authoritative and locks the path`, async () => {
  const h = await harness({ outPath: '/saved-draft.mp4' }, true);
  try {
    await h.focus(); await h.change('/new-draft.mp4'); await h.restore(exportJob(status)); await h.resolve();
    assert.equal(h.input().props.value, '/project/exports/history.mp4');
    assert.equal(h.input().props.disabled, true);
  } finally { await h.close(); }
});
