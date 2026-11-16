import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import { create, act } from 'react-test-renderer';
import ts from 'typescript';
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
async function harness({ status = 'legacy', deferred = false, failure = false } = {}) {
  const calls = [];
  let resolveConvert;
  const done = new Promise(resolve => { resolveConvert = resolve; });
  const inspected = { status, sourceVersion: 1, targetVersion: 2, token: 'plan', projectId: 'old', name: 'Old' };
  const dependencies = {
    react: React, 'react/jsx-runtime': jsx,
    '../i18n': { useLocale() {}, tr: s => s, msg: s => s },
    '../i18n/locale': { getLocale: () => 'en' },
    'lucide-react': new Proxy({}, { get: () => () => null }),
    '../store/editor': { useEditor: () => ({ state: { projects: [{ id: 'old', name: 'Old' }] }, dispatch: a => calls.push(a) }), showError: (_, e) => calls.push(['error', String(e)]) },
    '../store/actions': { loadProjects: async () => {}, selectProject: (...a) => calls.push(['select', a[1]]), createProject() {}, renameProject() {} },
    '../lib/api': { invalidateProject() {}, openPackage() {} },
    '../lib/projectDirectories': {
      inspectProjectFormat: async source => { calls.push(['inspect', source]); return inspected; },
      openProjectDirectory: async () => { calls.push(['open']); return inspected; },
      convertProjectCopy: async (source, token) => { calls.push(['convert', source, token]); if (failure) throw Error('stale'); if (deferred) await done; return { projectId: 'copy', name: 'Copy' }; },
    },
    './CacheSettings': { CacheSettings: () => null }, './LanguageSelect': { LanguageSelect: () => null },
  };
  const source = readFileSync(new URL('../src/components/HomeView.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const exports = {};
  runInNewContext(outputText, { exports, require: name => { assert.ok(dependencies[name], name); return dependencies[name]; }, window: {}, Date });
  let tree;
  await act(async () => { tree = create(React.createElement(exports.HomeView), { createNodeMock: node => node.type === 'dialog' ? { open: false, showModal() { this.open = true; }, close() { this.open = false; } } : null }); });
  const button = label => tree.root.findAllByType('button').find(b => b.children.includes(label));
  return { calls, tree,
    recent: () => act(async () => tree.root.findByProps({ className: 'home__item-main' }).props.onClick()),
    directory: async () => {
      await act(async () => tree.root.findByProps({ 'aria-label': '工程目录路径' }).props.onChange({ target: { value: '/old/project' } }));
      await act(async () => button('打开工程目录').props.onClick());
    },
    close: () => act(async () => button('关闭').props.onClick()),
    convert: () => act(async () => button('转换副本并打开').props.onClick()),
    doubleConvert: () => act(async () => { const b = button('转换副本并打开'); b.props.onClick(); b.props.onClick(); }),
    resolve: () => act(async () => resolveConvert()),
    unmount: () => act(async () => tree.unmount()),
  };
}
test('legacy opening only inspects; Close never converts or selects', async () => {
  const h = await harness(); await h.recent();
  assert.equal(h.calls.filter(x => x[0] === 'inspect').length, 1);
  await h.close();
  assert.equal(h.calls.filter(x => ['convert','open','select'].includes(x[0])).length, 0);
  await h.unmount();
});
test('explicit conversion opens returned copy, never original', async () => {
  const h = await harness(); await h.recent(); await h.convert();
  assert.equal(h.calls.filter(x => x[0] === 'convert').length, 1);
  assert.deepEqual(h.calls.filter(x => x[0] === 'select'), [['select','copy']]); await h.unmount();
});
test('same-tick double click submits once; unmount suppresses late opening', async () => {
  const h = await harness({ deferred: true }); await h.recent(); await h.doubleConvert();
  assert.equal(h.calls.filter(x => x[0] === 'convert').length, 1);
  await h.unmount(); await h.resolve();
  assert.equal(h.calls.filter(x => x[0] === 'select').length, 0);
});
test('conversion failure does not select or silently retry', async () => {
  const h = await harness({ failure: true }); await h.recent(); await h.convert();
  assert.equal(h.calls.filter(x => x[0] === 'convert').length, 1);
  assert.equal(h.calls.filter(x => x[0] === 'select').length, 0);
  assert.equal(h.tree.root.findAllByType('dialog').length, 0); await h.unmount();
});
test('current recent project can open without conversion prompt', async () => {
  const h = await harness({ status: 'current' }); await h.recent();
  assert.deepEqual(h.calls.filter(x => x[0] === 'select'), [['select','old']]);
  assert.equal(h.tree.root.findAllByType('dialog').length, 0); await h.unmount();
});

test('legacy directory is inspected but not registered before confirmation', async () => {
  const h = await harness(); await h.directory();
  assert.equal(h.calls.filter(x => x[0] === 'open').length, 0);
  await h.close();
  assert.equal(h.calls.filter(x => x[0] === 'convert').length, 0);
  await h.unmount();
});
test('directory conversion binds the inspected path and token', async () => {
  const h = await harness(); await h.directory(); await h.convert();
  const call = h.calls.find(x => x[0] === 'convert');
  assert.equal(call[1].path, '/old/project'); assert.equal(call[2], 'plan');
  assert.equal(h.calls.filter(x => x[0] === 'open').length, 0);
  await h.unmount();
});
