import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import { create, act } from 'react-test-renderer';
import ts from 'typescript';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
function loadModule(relative, dependencies, globals = {}) {
  const source = readFileSync(new URL(relative, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => {
    assert.ok(dependencies[name], name);
    return dependencies[name];
  }, ...globals });
  return exports;
}

async function harness(draft = null, deferJobs = false, selectedRange = null, { withDialog = false, flush, shareDismissed = null } = {}) {
  let resolveJobs;
  const jobs = new Promise(resolve => { resolveJobs = resolve; });
  if (!deferJobs) resolveJobs([]);
  let resolveOtherJobs;
  const otherJobs = new Promise(resolve => { resolveOtherJobs = resolve; });
  let resolveDirectory;
  const directory = new Promise(resolve => { resolveDirectory = resolve; });
  let state = { currentId: 'p', revision: '7', project: { sequence: { tracks: [{ clips: [{ timelineEnd: 8 }] }] } } };
  const dispatch = () => {};
  const text = s => s;
  const node = tag => props => React.createElement(tag, props);
  const queued = [];
  const dependencies = {
    react: React, 'react/jsx-runtime': jsx,
    '../i18n': { useLocale() {}, tr: text, msg: text },
    'lucide-react': new Proxy({}, { get: () => () => null }),
    './ui': { Button: node('button'), Field: node('div'), Select: node('select'), TextInput: node('input'), Badge: node('span') },
    '../store/editor': { useEditor: () => ({ state, dispatch }), showError() {} },
    '../store/actions': { getLatestState: () => state },
    '../lib/api': {
      listExportJobs: id => id === 'p' ? jobs : otherJobs,
      preflightProject: async () => ({ readyToRender: true, warnings: [], errors: [] }),
      sendCommand: async (id, type) => { queued.push([id, type]); return { changedEntities: [{ type: 'export_enqueued', jobId: 'fixture' }] }; },
      newCommandId: () => 'fixture-command',
    },
    '../lib/assetStore': { useSessionAssets() {} }, '../lib/preflight': {},
    '../lib/rational': { rationalToSecs: n => Number(n) },
    '../lib/projectDirectories': { getProjectDirectory: () => directory },
    '../store/selectedExportRange': { selectedExportRange: () => selectedRange },
  };
  if (flush) {
    const actions = loadModule('../src/store/actions.ts', {
      ...dependencies, './editor': dependencies['../store/editor'],
      '../lib/pendingEdits': { flushPendingEdits: flush },
      '../lib/projectScope': {}, '../lib/projectSync': {},
    });
    dependencies['../store/actions'].doExport = actions.doExport;
  }
  const copied = [];
  const memory = { shareDismissed };
  const storage = {
    getItem: key => key === 'cutvoke.sharePrompt.dismissed' ? memory.shareDismissed : JSON.stringify(draft),
    setItem(key, value) { if (key === 'cutvoke.sharePrompt.dismissed') memory.shareDismissed = value; },
  };
  const globals = { window: { addEventListener() {}, removeEventListener() {} },
    localStorage: storage,
    navigator: { clipboard: { writeText: async text => { copied.push(text); } } },
    setTimeout, clearTimeout, setInterval, clearInterval };
  dependencies['../lib/sharePrompt'] = loadModule('../src/lib/sharePrompt.ts', {}, globals);
  const { ExportVideoSection } = loadModule('../src/components/ExportVideoSection.tsx', dependencies, globals);
  const View = withDialog ? loadModule('../src/components/ExportDialog.tsx', {
    ...dependencies, './ExportVideoSection': { ExportVideoSection },
  }, globals).ExportDialog : ExportVideoSection;
  const view = () => React.createElement(View, { onClose() {} });
  let tree;
  await act(async () => { tree = create(view()); });
  const input = () => tree.root.findAllByType('input').find(n => n.props['aria-label'] === '输出文件路径');
  const find = label => tree.root.findAllByType('input').find(n => n.props['aria-label'] === label);
  const prefill = () => tree.root.findAllByType('button').find(n => n.props.children === '使用所选片段的时间范围');
  const submit = () => tree.root.findAllByType('button').find(n => n.props['aria-label'] === '开始导出');
  const button = label => tree.root.findAllByType('button').find(n => n.props.children === label);
  const revise = (next, id = state.currentId) => act(async () => {
    selectedRange = next;
    state = { ...state, currentId: id };
    tree.update(view());
  });
  return { input, find, prefill, submit, button, revise, queued, copied, memory,
    restore: job => act(async () => resolveJobs([job])),
    restoreOther: job => act(async () => resolveOtherJobs([job])),
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

test('selected range stays disabled while restoring and during an active export', async () => {
  const preset = { projectId: 'p', revision: '7', clipId: 'a', start: 2.25, end: 3.5 };
  const h = await harness({ outPath: '/draft.mp4', useRange: true, rangeStart: '1', rangeEnd: '2' }, true, preset);
  try {
    assert.equal(h.prefill().props.disabled, true);
    await h.restore({ ...exportJob('running'), request: { range: { start: 4, end: 5 } } });
    assert.equal(h.prefill().props.disabled, true);
    assert.equal(h.find('导出开始时间').props.value, '4');
    assert.equal(h.find('导出结束时间').props.value, '5');
  } finally { await h.close(); }
});

test('using a selected range updates only the range form, without submitting', async () => {
  const preset = { projectId: 'p', revision: '7', clipId: 'a', start: 2.25, end: 3.5 };
  const h = await harness({ outPath: '/draft.mp4' }, false, preset);
  try {
    assert.equal(h.prefill().props.disabled, false);
    await act(async () => h.prefill().props.onClick());
    assert.equal(h.find('导出开始时间').props.value, '2.25');
    assert.equal(h.find('导出结束时间').props.value, '3.5');
    assert.equal(h.input().props.value, '/draft.mp4');
    await act(async () => h.find('导出结束时间').props.onChange({ target: { value: '3.75' } }));
    assert.equal(h.find('导出开始时间').props.value, '2.25');
    assert.equal(h.find('导出结束时间').props.value, '3.75');
    await h.revise(null);
    assert.equal(h.prefill().props.disabled, true);
    assert.equal(h.submit().props.disabled, false); // explicit manual range no longer follows selection
  } finally { await h.close(); }
});

test('changed selection or revision cannot submit a previously selected range', async () => {
  const original = { projectId: 'p', revision: '7', clipId: 'a', start: 2.25, end: 3.5 };
  const h = await harness(null, false, original);
  try {
    await act(async () => h.prefill().props.onClick());
    await h.revise({ ...original, revision: '8', end: 4 });
    assert.equal(h.submit().props.disabled, true);
    assert.equal(h.find('导出结束时间').props.disabled, true);
    await act(async () => h.prefill().props.onClick());
    assert.equal(h.find('导出结束时间').props.value, '4');
    assert.equal(h.submit().props.disabled, false);
  } finally { await h.close(); }
});

for (const change of ['selection', 'project']) test(`derived range fails closed after deferred edit flush: ${change}`, async () => {
  const original = { projectId: 'p', revision: '7', clipId: 'a', start: 2.25, end: 3.5 };
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  let entered;
  const flushStarted = new Promise(resolve => { entered = resolve; });
  const h = await harness({ outPath: '/draft.mp4' }, false, original, {
    withDialog: true, flush: () => { entered(); return pending; },
  });
  let exporting;
  try {
    await act(async () => h.prefill().props.onClick());
    await act(async () => { exporting = h.submit().props.onClick(); await flushStarted; });
    await h.revise(null, change === 'project' ? 'next' : 'p');
    await act(async () => { release(true); await exporting; });
    assert.deepEqual(h.queued, []);
  } finally { release(true); await h.close(); }
});

test('switching projects in an open dialog cannot mask an active job range', async () => {
  const original = { projectId: 'p', revision: '7', clipId: 'a', start: 2.25, end: 3.5 };
  const h = await harness(null, false, original, { withDialog: true });
  try {
    await act(async () => h.prefill().props.onClick());
    assert.equal(h.find('导出开始时间').props.value, '2.25');
    await h.revise({ ...original, projectId: 'next' }, 'next');
    assert.equal(h.prefill().props.disabled, true);
    assert.equal(h.find('导出开始时间'), undefined);
    await h.restoreOther({ ...exportJob('running'), request: { range: { start: 4, end: 5 } } });
    assert.equal(h.prefill().props.disabled, true);
    assert.equal(h.find('导出开始时间').props.value, '4');
    assert.equal(h.find('导出结束时间').props.value, '5');
  } finally { await h.close(); }
});

test('export SRT download uses the engine-provided filename with a safe job fallback', async () => {
  let disposition;
  let requested;
  const { downloadExportSrt } = loadModule('../src/lib/api.ts', {
    '../i18n': { tr: text => text },
    '../i18n/locale': { getLocale: () => 'en' },
    './projectSync': {},
  }, {
    fetch: async path => {
      requested = path;
      return new Response('1\n00:00:00,125 --> 00:00:00,375\nReview\n', {
        headers: disposition ? { 'Content-Disposition': disposition } : {},
      });
    },
  });
  const cases = [
    ["attachment; filename=\"__ caf__v2.srt\"; filename*=UTF-8''%E5%89%AA%E8%BE%91%20caf%C3%A9_v2.srt", '剪辑 café_v2.srt'],
    ["attachment; filename=\"take.srt\"; filename*=UTF-8''take.srt", 'take.srt'],
    ["attachment; filename*=UTF-8''%2Fhidden%2Fname.srt", 'captions-job.srt'],
    ["attachment; filename*=UTF-8''bad%0D%0Avalue.srt", 'captions-job.srt'],
    ["attachment; filename*=UTF-8''bad%FF.srt", 'captions-job.srt'],
    ['attachment; filename="captions.srt"', 'captions-job.srt'],
    [undefined, 'captions-job.srt'],
  ];
  for (const [header, expected] of cases) {
    disposition = header;
    const { blob, filename } = await downloadExportSrt('review/one', 'job');
    assert.equal(filename, expected);
    assert.equal(requested, '/api/v1/projects/review%2Fone/exports/job/captions.srt');
    assert.match(await blob.text(), /Review/);
  }
});

const shareText = '刚用 CutVoke 做了个视频：开源、本地，AI Agent 和我改同一条时间线。https://github.com/frankShan001/CutVoke @shansjl';

test('share prompt dismissal persists only its flag and survives a denied store', () => {
  const data = new Map();
  const api = loadModule('../src/lib/sharePrompt.ts', {}, {
    localStorage: { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) },
  });
  assert.equal(api.sharePromptDismissed(), false);
  api.dismissSharePrompt();
  assert.deepEqual([...data.entries()], [['cutvoke.sharePrompt.dismissed', '1']]);
  assert.equal(api.sharePromptDismissed(), true);
  const denied = loadModule('../src/lib/sharePrompt.ts', {}, {
    localStorage: { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } },
  });
  assert.equal(denied.sharePromptDismissed(), false);
  assert.doesNotThrow(() => denied.dismissSharePrompt());
});

test('a clean export offers the share line, copy, and dismissal', async () => {
  const h = await harness(null, true);
  try {
    await h.restore(exportJob('succeeded'));
    assert.equal(h.button('复制分享文案').props.variant, 'ghost');
    assert.equal(h.button('不再显示').props.size, 'sm');
    await act(async () => { h.button('复制分享文案').props.onClick(); });
    assert.deepEqual(h.copied, [shareText]);
    await act(async () => { h.button('不再显示').props.onClick(); });
    assert.equal(h.button('复制分享文案'), undefined);
    assert.equal(h.memory.shareDismissed, '1');
  } finally { await h.close(); }
});

test('warnings and a stored dismissal omit the share line', async () => {
  const warned = exportJob('succeeded');
  warned.result = { ...warned.result, warnings: ['audio drifted'] };
  const withWarnings = await harness(null, true);
  try {
    await withWarnings.restore(warned);
    assert.equal(withWarnings.button('复制分享文案'), undefined);
    assert.equal(withWarnings.button('不再显示'), undefined);
  } finally { await withWarnings.close(); }
  const dismissed = await harness(null, true, null, { shareDismissed: '1' });
  try {
    await dismissed.restore(exportJob('succeeded'));
    assert.equal(dismissed.button('复制分享文案'), undefined);
    assert.equal(dismissed.button('不再显示'), undefined);
  } finally { await dismissed.close(); }
});
