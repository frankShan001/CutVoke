import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const source=name=>readFileSync(new URL(`../src/lib/${name}.ts`,import.meta.url),'utf8');
const url=text=>`data:text/javascript;base64,${Buffer.from(ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText).toString('base64')}`;
const {rankQuickApply}=await import(url(source('quickApply')));
const {filterTimelineClips,publishTimelineSelection,clearTimelineSelection,visibleTimelineSelection}=await import(url(source('timelineFilter')));
const rational=url(source('rational').replace('import { tr } from "../i18n";','const tr = text => text;'));
const {evaluateKeyframes,keyframeVelocity}=await import(url(source('keyframes').replace('"./rational"',JSON.stringify(rational))));
const {modifiedProperties}=await import(url(source('modifiedProperties')));
const r=value=>({num:String(value),den:'1'});

test('quick apply scopes use complete tokens, rank names and avoid unrelated loose-letter matches',()=>{
  const entries=[{id:'lens.blur',name:'Camera Lens Blur',kind:'effect'},
    {id:'control.menu',name:'Menu Control',kind:'effect'},
    {id:'preset.menu',name:'Menu',kind:'preset'},
    {id:'menu.export',name:'Export',kind:'command'}];
  assert.deepEqual(rankQuickApply(entries,'e: menu').map(item=>item.id),['control.menu']);
  assert.deepEqual(rankQuickApply(entries,'p: menu').map(item=>item.id),['preset.menu']);
  assert.deepEqual(rankQuickApply(entries,'m: export').map(item=>item.id),['menu.export']);
  assert.equal(rankQuickApply(entries,'lens nonexistent').length,0);
  assert.equal(rankQuickApply(entries,'camera lens')[0].id,'lens.blur');
});
test('timeline filters intersect without changing track data and selection views are project-scoped',()=>{
  const tracks=[{id:'v',kind:'video',clips:[{id:'c1',assetRef:{sourcePath:'E:/media/RED.mov'},effects:[]},
    {id:'c2',assetRef:{sourcePath:'E:/media/green.mov'},effects:[{effectId:'glow',params:{}}],keyframes:{opacity:[{}]}}]}];
  const frozen=structuredClone(tracks),names=new Map([['glow','Soft light']]);
  assert.deepEqual([...filterTimelineClips(tracks,'red','all',[],names)],['c1']);
  assert.deepEqual([...filterTimelineClips(tracks,'soft light','animated',[],names)],['c2']);
  assert.deepEqual([...filterTimelineClips(tracks,'red','selected',['c2'],names)],[]);
  assert.deepEqual(tracks,frozen);
  const owner=Symbol(),other=Symbol();publishTimelineSelection('p',['c1'],owner);
  assert.deepEqual(visibleTimelineSelection('p'),['c1']);assert.equal(visibleTimelineSelection('other'),undefined);
  clearTimelineSelection(other);assert.deepEqual(visibleTimelineSelection('p'),['c1']);clearTimelineSelection(owner);
  assert.equal(visibleTimelineSelection('p'),undefined);
});
test('hold switches exactly on the key and rate graphs share scalar easing semantics',()=>{
  const lane=[{id:'a',time:r(0),value:0,interpolation:'hold'},{id:'b',time:r(1),value:1,interpolation:'linear'}];
  assert.equal(evaluateKeyframes(lane,.99999,0),0);assert.equal(evaluateKeyframes(lane,1,0),1);
  assert.equal(keyframeVelocity(lane,.99,0),0);
  lane[0].interpolation='linear';assert.ok(Math.abs(keyframeVelocity(lane,.5,0)-1)<1e-8);
  lane[0].interpolation='cubic-bezier';lane[0].bezier=[1/3,0,2/3,1];
  assert.ok(keyframeVelocity(lane,.05,0)<keyframeVelocity(lane,.5,0));
  assert.ok(Math.abs(evaluateKeyframes(lane,.5,0)-.5)<1e-8);
});

test('modified properties include static authoring, animation, matte, binding and instance overrides without mutating the clip',()=>{
  const clip={id:'c',sourceStart:r(1),speed:r(2),volume:r(1),effects:[{effectId:'blur',instanceId:'a',params:{sigma:5},compositing:{opacity:.5}},{effectId:'blur',instanceId:'b',params:{sigma:8},enabled:false}],
    keyframes:{x:[{time:r(0),value:2}]},compositing:{matte:{clipId:'other'}},propertyLinks:{rotation:{source:{type:'time'}}},motionPath:{points:[{},{}]},propertyOverrides:{headline:'Hello'}};
  const saved=structuredClone(clip);
  const rows=modifiedProperties(clip,[{effectId:'blur',name:'Blur',defaults:{sigma:5}}]);
  assert.equal(rows.filter(row=>row.key==='effect:a:sigma').length,0);
  assert.equal(rows.find(row=>row.key==='effect:b:sigma').value,8);
  for(const key of ['sourceStart','speed','keys:x','mix:a','bypass:b','compositing','link:rotation','motionPath','override:headline'])assert.ok(rows.some(row=>row.key===key),key);
  assert.deepEqual(rows.filter(row=>row.animated).map(row=>row.key),['keys:x','link:rotation','motionPath']);
  assert.deepEqual(clip,saved);
});
