import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import * as jsx from 'react/jsx-runtime';
import { create, act } from 'react-test-renderer';
import ts from 'typescript';
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
async function harness() {
 const geometry = x => ({ position: { x, y: 1 }, scale: 1, rotation: 0 });
 const project = x => ({ sequence: { width: 160, height: 90, tracks: [{ id: 't', kind: 'video', clips: [{ id: 'c', timelineStart: 0, timelineEnd: 5, effects: [{ effectId: 'transform', params: geometry(x) }] }] }] } });
 const state = { project: project(12), selection: { trackId: 't', clipId: 'c' }, playhead: 0, editLock: false };
 let optimistic = project(17); const staged = [], released = [];
 const deps = { react: React, 'react/jsx-runtime': jsx, '../i18n': { useLocale() {}, tr: x => x },
 '../store/editor': { useEditor: () => ({ state, dispatch() {} }) },
 '../store/interactive': { useInteractionProject: () => optimistic, stageTransform: (...args) => staged.push(args[2]), releaseEdit: key => released.push(key) },
 '../lib/effects': { BUILTIN_TRANSFORM_ID: 'transform' }, '../lib/rational': { rationalToSecs: x => x }, '../lib/keyframes': { evaluateKeyframes: (_,t,x) => x } };
 const source = readFileSync(new URL('../src/components/TransformCanvasOverlay.tsx', import.meta.url), 'utf8');
 const exports = {}; const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
 runInNewContext(outputText, { exports, require: x => { assert.ok(deps[x],x); return deps[x]; }, DOMPoint: class { constructor(x,y) { this.x=x;this.y=y; } matrixTransform() { return this; } } });
 let tree; await act(async () => { tree = create(React.createElement(exports.TransformCanvasOverlay), { createNodeMock: () => ({ getScreenCTM: () => ({ inverse() {} }), setPointerCapture() {} }) }); });
 const svg = () => tree.root.findByType('svg');
 const ev = (x,y) => ({ pointerId: 1, clientX: x, clientY: y, preventDefault() {}, stopPropagation() {} });
 return { staged, released,
 begin: () => act(async () => tree.root.findAllByType('circle').find(x => x.props['aria-label']==='调整画面大小').props.onPointerDown(ev(17,1))),
 move: () => act(async () => svg().props.onPointerMove(ev(-43,-44))),
 finish: () => act(async () => svg().props.onPointerUp(ev(-43,-44))),
 update: changes => act(async () => { Object.assign(state,changes); tree.update(React.createElement(exports.TransformCanvasOverlay)); }),
 ack: x => act(async () => { state.project=project(x);tree.update(React.createElement(exports.TransformCanvasOverlay)); }),
 close: () => act(async () => tree.unmount()) };
}
test('late acknowledgement of prior move does not cancel the next resize', async () => {
 const h=await harness(); try { await h.begin(); await h.ack(17); await h.move(); await h.finish(); assert.ok(h.staged.at(-1)?.scale>1.1); assert.equal(h.released.length,1); } finally { await h.close(); }
});
test('conflicting external geometry still cancels resize', async () => {
 const h=await harness(); try { await h.begin(); await h.ack(40); await h.move(); await h.finish(); assert.equal(h.staged.length,0); } finally { await h.close(); }
});
for(const changes of [{playhead:1},{editLock:true},{selection:{trackId:'t',clipId:'other'}}]) test(`context change cancels resize ${JSON.stringify(changes)}`,async()=>{
 const h=await harness();try {await h.begin();await h.update(changes); if(!changes.selection){await h.move();await h.finish();} assert.equal(h.staged.length,0);}finally{await h.close();}
});

test('late acknowledgement preserves an already moving resize', async () => {
 const h=await harness(); try { await h.begin(); await h.move(); const prior=h.staged.at(-1).scale; await h.ack(17); await h.move(); await h.finish(); assert.equal(h.staged.at(-1).scale,prior); assert.ok(prior>1.1); assert.equal(h.released.length,1); } finally { await h.close(); }
});
