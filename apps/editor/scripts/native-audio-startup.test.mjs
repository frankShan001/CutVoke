import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return { promise, resolve, reject }; };
const flush = async () => { for(let i=0;i<20;i++) await Promise.resolve(); };
function bindPlayerSeek(context) {
  // Extracted Player callbacks do not execute Player's imports. Run the same
  // setter helper with QA off, rather than replacing a native seek in the test.
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/previewSeekEvidence.ts', import.meta.url), 'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText,
    { exports, window: {} });
  context.seekPreviewVideo = exports.seekPreviewVideo;
  context.qaSeekReads = 0;
  context.readSeekEvidence = () => { context.qaSeekReads++; return {}; };
  return context;
}
function harness(diagnostics = false) {
  const timers = new Map(); let timer=0;
  const exports = {};
  runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/nativeAudioStartup.ts', import.meta.url),'utf8'),
    { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText,
    { exports, AbortController, setTimeout: f => { timers.set(++timer,f); return timer; }, clearTimeout: id => timers.delete(id) });
  let commits=0, failures=0, current=true;
  const resume=deferred();
  const context={ state:'suspended', resume:()=>resume.promise };
  const plays=[deferred(),deferred()];
  const elements=plays.map(play => Object.assign(new EventTarget(), { paused:true, muted:false, currentTime:3, seeking:false,
    pause(){this.paused=true;}, play(){return play.promise.then(()=>{this.paused=false;});} }));
  const voices=elements.map(element=>({element,time:3}));
  const start=new exports.NativeAudioStartup(diagnostics);
  const request={key:'a',voices,context,current:()=>current,commit:()=>{commits++;return true;},failed:()=>failures++};
  return {start,request,context,resume,plays,elements,timers,commit:()=>commits,failures:()=>failures,setCurrent:v=>current=v,
    unlock:async()=>{context.state='running';resume.resolve();await flush();}};
}
test('suspended context and delayed play keep transport uncommitted and all voices muted',async()=>{
  const h=harness();h.start.start(h.request);await flush();assert.equal(h.commit(),0);
  await h.unlock();h.plays[0].resolve();await flush();h.elements[0].currentTime=3.3;
  assert.equal(h.commit(),0);assert.ok(h.elements.every(e=>e.muted));
  h.plays[1].resolve();await flush();assert.equal(h.commit(),1);
  assert.ok(h.elements.every(e=>!e.muted && e.currentTime===3));assert.equal(h.timers.size,0);
});
test('partial success then rejection rolls every voice back and latches failure',async()=>{
  const h=harness();h.start.start(h.request);await h.unlock();h.plays[0].resolve();await flush();h.plays[1].reject();await flush();
  assert.equal(h.commit(),0);assert.equal(h.failures(),1);assert.ok(h.elements.every(e=>e.paused&&e.muted));
  assert.equal(h.start.start(h.request),'failed');assert.equal(h.timers.size,0);
});
test('pause or scope change before late play resolution never commits or leaves stale voice running',async()=>{
  for(const cancel of [false,true]){
    const h=harness();h.start.start(h.request);await h.unlock();h.setCurrent(false);if(cancel)h.start.cancel();
    h.plays.forEach(p=>p.resolve());await flush();assert.equal(h.commit(),0);assert.equal(h.failures(),0);assert.ok(h.elements.every(e=>e.paused&&e.muted));
  }
});
test('a stale resolution cannot pause voices now owned by a new generation',async()=>{
  const h=harness();h.start.start(h.request);await h.unlock();h.start.cancel();
  const old=h.plays.slice();const newer=[deferred(),deferred()];
  h.elements.forEach((e,i)=>e.play=()=>newer[i].promise.then(()=>{e.paused=false;}));
  h.start.start({...h.request,key:'b'});await flush();newer.forEach(p=>p.resolve());await flush();assert.equal(h.commit(),1);
  old.forEach(p=>p.resolve());await flush();assert.equal(h.commit(),1);assert.ok(h.elements.every(e=>!e.paused));
});
test('seek completion is a barrier before playback and after asynchronous preparation',async()=>{
  const h=harness();h.elements.forEach(e=>e.seeking=true);h.start.start(h.request);await h.unlock();
  h.plays.forEach(p=>p.resolve());await flush();assert.equal(h.commit(),0);
  h.elements.forEach(e=>e.dispatchEvent(new Event('seeked')));await flush();assert.equal(h.commit(),0);
  h.elements.forEach(e=>{e.seeking=false;e.dispatchEvent(new Event('seeked'));});await flush();assert.equal(h.commit(),1);
});
test('resume rejection and non-running context fail closed without transport',async()=>{
  for(const rejection of [true,false]){const h=harness();h.start.start(h.request);if(rejection)h.resume.reject();else h.resume.resolve();await flush();assert.equal(h.commit(),0);assert.equal(h.failures(),1);assert.ok(h.elements.every(e=>e.paused&&e.muted));}
});
test('bounded startup timeout rolls back and late completion cannot revive it',async()=>{
  const h=harness();h.start.start(h.request);await h.unlock();[...h.timers.values()][0]();await flush();
  h.plays.forEach(p=>p.resolve());await flush();assert.equal(h.commit(),0);assert.equal(h.failures(),1);assert.ok(h.elements.every(e=>e.paused&&e.muted));
});
test('cancelled seek listeners cannot commit when old seeked arrives',async()=>{
  const h=harness();h.elements.forEach(e=>e.seeking=true);h.start.start(h.request);await h.unlock();h.start.cancel();
  h.elements.forEach(e=>e.dispatchEvent(new Event('seeked')));await flush();assert.equal(h.commit(),0);assert.equal(h.failures(),0);assert.equal(h.timers.size,0);
});
test('a voice advancing too far during another seek fails closed rather than skipping the start',async()=>{
  const h=harness();h.start.start(h.request);await h.unlock();h.elements.forEach(e=>e.seeking=true);h.plays.forEach(p=>p.resolve());await flush();
  h.elements[0].seeking=false;h.elements[0].dispatchEvent(new Event('seeked'));h.elements[0].currentTime=3.2;
  h.elements[1].seeking=false;h.elements[1].dispatchEvent(new Event('seeked'));await flush();
  assert.equal(h.commit(),0);assert.equal(h.failures(),1);assert.ok(h.elements.every(e=>e.paused&&e.muted));
});
test('context interruption after startup rolls the mix back without a second commit',async()=>{
  const h=harness();h.start.start(h.request);await h.unlock();h.plays.forEach(p=>p.resolve());await flush();assert.equal(h.commit(),1);
  h.context.state='suspended';assert.equal(h.start.start(h.request),'failed');assert.equal(h.commit(),1);assert.equal(h.failures(),1);assert.ok(h.elements.every(e=>e.paused&&e.muted));
});
test('commit refusal leaves no audible native voice',async()=>{
  const h=harness();h.request.commit=()=>false;h.start.start(h.request);await h.unlock();h.plays.forEach(p=>p.resolve());await flush();
  assert.equal(h.failures(),1);assert.ok(h.elements.every(e=>e.paused&&e.muted));
});

function updateHarness(imageMode = false) {
  const host = harness(), source = readFileSync(new URL('../src/components/InteractiveAudio.tsx',import.meta.url),'utf8');
  const file=ts.createSourceFile('InteractiveAudio.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
  let update,generation;
  const visit=node=>{if(ts.isVariableDeclaration(node)&&node.name.getText(file)==='update')update=node.initializer.getText(file);
    if(ts.isFunctionDeclaration(node)&&node.name?.text==='nativeAudioGeneration')generation=node.getText(file).replace('export ','');ts.forEachChild(node,visit);};visit(file);
  const stats={pause:0,play:0,reset:0,commit:0,seek:0,gains:[]};let active=false;
  const clip={id:'a',assetRef:{sourcePath:'a.wav'},timelineStart:0,timelineEnd:6,sourceStart:0,speed:1,volume:1};
  const element=Object.assign(new EventTarget(),{paused:true,muted:false,readyState:4,duration:6,seeking:false,
    pause(){stats.pause++;this.paused=true;},play(){stats.play++;this.paused=false;return Promise.resolve();}});
  let cursor=3;Object.defineProperty(element,'currentTime',{get:()=>cursor,set:v=>{cursor=v;stats.seek++;}});
  const voice={element,prepared:false,ready:true,hasAudio:true,signature:JSON.stringify(['p',clip.assetRef,[],undefined,undefined,null,null]),
    controller:new AbortController(),gain:{disconnect:()=>{},gain:{setValueAtTime:v=>stats.gains.push(v)}}};
  const voices=new Map([['a',voice]]);
  const latest={current:{project:{projectId:'p',revision:1,sequence:{id:'s',tracks:[{id:'t',kind:'audio',clips:[clip]}]}},scope:'p',seek:0,time:3,playing:true,scrubbing:false,transportMode:imageMode?'image':'video',
    onReady:()=>{}, playbackClock:(initialize,reset)=>{if(reset){stats.reset++;active=false;}if(initialize){stats.commit++;active=true;}return imageMode?null:{time:3,playing:active};}}};
  const live={exports:{},positionGeneration:{current:null},latest,voices:{current:voices},startup:{current:host.start},context:{current:{state:'running',currentTime:10,resume:()=>Promise.resolve()}},
    playbackEndSecs:tracks=>Math.max(...tracks.flatMap(t=>t.clips.map(c=>c.timelineEnd))),meter:{current:null},disposed:false,frame:0,requestAnimationFrame:()=>1,AUDIO_FX:new Set(['audio-effect']),rationalToSecs:v=>v||0,sourceTime:(_clip,time)=>time};
  runInNewContext(ts.transpileModule(`${generation}\nconst update=${update}; exports.update=update; exports.generation=nativeAudioGeneration`,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,live);
  let positionSource;
  const findPosition=node=>{if(ts.isCallExpression(node)&&node.expression.getText(file)==='latest.current.onPositionSource'&&ts.isArrowFunction(node.arguments[0]))positionSource=node.arguments[0].getText(file);ts.forEachChild(node,findPosition);};findPosition(file);
  live.nativeAudioPosition=()=>element.currentTime;
  runInNewContext(ts.transpileModule(`exports.position=${positionSource}`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,live);
  return {position:live.exports.position,update:live.exports.update,generation:live.exports.generation,stats,latest,element,clip};
}
test('real InteractiveAudio update preserves playback across live gain drag and unrelated revisions',async()=>{
  const h=updateHarness();h.update();await flush();assert.equal(h.stats.commit,1);const before={...h.stats};
  for(const volume of [.8,.5,.2]){
    h.latest.current.project=structuredClone(h.latest.current.project);h.latest.current.project.revision++;
    h.latest.current.project.sequence.tracks[0].clips[0].volume=volume;h.update();await flush();
  }
  for(const key of ['pause','play','reset','commit','seek'])assert.equal(h.stats[key],before[key],key);
  assert.deepEqual(h.stats.gains.slice(-3),[.8,.5,.2]);assert.equal(h.element.paused,false);
});
test('semantic audio generation ignores visual edits but invalidates source mapping, mute and mix changes',()=>{
  const h=updateHarness(),original=h.latest.current.project,base=h.generation(original);
  for(const mutate of [p=>p.revision++,p=>p.sequence.tracks[0].clips[0].volume=.3,p=>p.sequence.tracks[0].clips[0].fadeIn=.2,
    p=>p.sequence.tracks[0].clips[0].effects=[{effectId:'visual',enabled:true}]]){const p=structuredClone(original);mutate(p);assert.equal(h.generation(p),base);}
  for(const mutate of [p=>p.sequence.tracks[0].muted=true,p=>p.sequence.tracks[0].clips[0].sourceStart=1,
    p=>p.sequence.tracks[0].clips[0].speed=2,p=>p.sequence.tracks[0].clips[0].assetRef.sourcePath='b.wav',p=>p.sequence.masterAudio={volume:2}]){
    const p=structuredClone(original);mutate(p);assert.notEqual(h.generation(p),base);
  }
});
test('image fallback with no native transport clock can still start audio',async()=>{
  const h=updateHarness(true);h.update();await flush();assert.equal(h.stats.commit,1);assert.equal(h.element.paused,false);assert.equal(h.element.muted,false);
});
test('failed startup recovers after explicit pause then replay of the same scope',async()=>{
  const h=harness();h.start.start(h.request);await h.unlock();h.plays[0].reject();await flush();assert.equal(h.start.start(h.request),'failed');
  h.start.cancel();h.elements.forEach(e=>e.play=()=>{e.paused=false;return Promise.resolve();});
  h.start.start(h.request);await flush();assert.equal(h.commit(),1);assert.ok(h.elements.every(e=>!e.paused&&!e.muted));
});
test('real update pause and replay explicitly rearm a failed native attempt',async()=>{
  const h=updateHarness();const play=h.element.play;h.element.play=()=>Promise.reject(new Error('blocked'));
  h.update();await flush();assert.equal(h.stats.commit,0);assert.equal(h.element.paused,true);
  h.element.play=play;h.update();await flush();assert.equal(h.stats.commit,0); // Failure stays latched, no RAF retry storm.
  h.latest.current.playing=false;h.update();h.latest.current.playing=true;h.update();await flush();
  assert.equal(h.stats.commit,1);assert.equal(h.element.paused,false);assert.equal(h.element.muted,false);
});

test('real Player clock and audio update restart transport on image to video handoff without seek or pause intent',async()=>{
  const h=updateHarness(true);
  const source=readFileSync(new URL('../src/components/Player.tsx',import.meta.url),'utf8');
  const file=ts.createSourceFile('Player.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let callback;
  const visit=node=>{if(ts.isVariableDeclaration(node)&&node.name.getText(file)==='audioPlaybackClock')callback=node.initializer.arguments[0].getText(file);ts.forEachChild(node,visit);};visit(file);
  let deviceTime=10;
  const transport={current:{active:false,time:3,started:0,clock:null}};
  const globals={exports:{},mode:'image',mediaEndSecs:6,audioTransportRef:transport,playheadRef:{current:3},
    auditionBoundary:{current:()=>null},nativeAudioRef:{current:true},playingRef:{current:true},scrubbingRef:{current:false},
    nativeAudioPositionRef: { current: null }, nativeAudioClockRef:{current:()=>deviceTime},performance:{now:()=>deviceTime*1000}};
  runInNewContext(ts.transpileModule(`exports.clock=${callback}`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,globals);
  h.latest.current.playbackClock=globals.exports.clock;
  h.update();await flush();assert.equal(transport.current.active,false);assert.equal(h.element.paused,false);
  // Same project, same source, same seekVersion, and playing remains true.
  globals.mode='video';h.latest.current.transportMode='video';h.update();await flush();
  assert.equal(transport.current.active,true);assert.equal(h.element.paused,false);assert.equal(h.element.muted,false);
  deviceTime+=.1;assert.ok(Math.abs(globals.exports.clock().time-3.1)<.00001);
  assert.equal(globals.exports.clock().playing,true);
  const plays=h.stats.play;h.update();await flush();assert.equal(h.stats.play,plays); // No rearm every frame.
});

test('native cursor boundary uses the fastest unmuted voice with source speed and prepared mapping',()=>{
  const source=readFileSync(new URL('../src/components/InteractiveAudio.tsx',import.meta.url),'utf8');
  const file=ts.createSourceFile('InteractiveAudio.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let fn;
  const visit=node=>{if(ts.isFunctionDeclaration(node)&&node.name?.text==='nativeAudioPosition')fn=node.getText(file).replace('export ','');ts.forEachChild(node,visit);};visit(file);
  const exports={};runInNewContext(ts.transpileModule(`${fn}; exports.position=nativeAudioPosition`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,{exports,rationalToSecs:v=>v||0});
  const project={sequence:{tracks:[{clips:[{id:'a',sourceStart:10,timelineStart:3,speed:2},{id:'b',timelineStart:3,speed:1}]}]}};
  const voices=new Map([['a',{ready:true,prepared:false,element:{currentTime:11.2,readyState:4,paused:false,muted:false}}],
    ['b',{ready:true,prepared:true,element:{currentTime:.55,readyState:4,paused:false,muted:false}}]]);
  assert.ok(Math.abs(exports.position(project,voices)-3.6)<.00001);
  for (const patch of [{seeking:true}, {muted:true}]) {
    Object.assign(voices.get('a').element, patch); assert.equal(exports.position(project,voices),3.55);
    Object.assign(voices.get('a').element, {seeking:false,muted:false});
  }
  voices.get('a').gain={gain:{value:0}};assert.equal(exports.position(project,voices),3.55);
  voices.get('a').gain.gain.value=1;voices.get('a').disposed=true;assert.equal(exports.position(project,voices),3.55);
  voices.get('a').element.muted=true;assert.equal(exports.position(project,voices),3.55);
  voices.get('b').element.paused=true;assert.equal(exports.position(project,voices),null);
});
test('real Player deadline stops at the native cursor even when graph transport is behind, without a RAF',()=>{
  const source=readFileSync(new URL('../src/components/Player.tsx',import.meta.url),'utf8');
  const file=ts.createSourceFile('Player.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let callback;
  const visit=node=>{if(ts.isVariableDeclaration(node)&&node.name.getText(file)==='audioPlaybackClock')callback=node.initializer.arguments[0].getText(file);ts.forEachChild(node,visit);};visit(file);
  for(const mode of ['video','image']) {
    let cursor=null;const seen=[];
    const clock=()=>10.47,transport={active:true,time:3,started:10,clock};
    const globals={exports:{},mode,mediaEndSecs:6,audioTransportRef:{current:transport},playheadRef:{current:3},
      auditionBoundary:{current:time=>{seen.push(time);return time>=3.5?3.5:null;}},nativeAudioRef:{current:true},playingRef:{current:true},scrubbingRef:{current:false},
      nativeAudioPositionRef:{current:()=>cursor},nativeAudioClockRef:{current:clock},performance:{now:()=>10470}};
    runInNewContext(ts.transpileModule(`exports.clock=${callback}`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,globals);
    if(mode==='video')assert.ok(Math.abs(globals.exports.clock(false).time-3.47)<.00001);
    else assert.equal(globals.exports.clock(false),null);
    cursor=3.599546;const stopped=globals.exports.clock(false);
    assert.equal(stopped.time,3.5);assert.equal(stopped.playing,false);assert.ok(seen.includes(cursor));
  }
});


test('registered native position rejects a stale scope, source mapping or seek before the next audio update',async()=>{
  for(const mutate of [h=>h.latest.current.scope='other',h=>h.latest.current.seek++,h=>h.latest.current.project.sequence.tracks[0].clips[0].sourceStart=1]) {
    const h=updateHarness();assert.equal(h.position(),null);
    h.update();await flush();assert.equal(h.position(),3);
    mutate(h);assert.equal(h.position(),null); // No RAF update / replacement voice yet.
  }
});


test('last voice cleanup leaves the endpoint clock running for Player finalization',async()=>{
  const h=updateHarness(); h.update();await flush();const resets=h.stats.reset;
  h.latest.current.playbackClock=(initialize,reset)=>{if(reset)h.stats.reset++;return {time:6,playing:true};};
  h.element.removeAttribute=()=>{}; h.element.load=()=>{};
  h.update();assert.equal(h.stats.reset,resets);assert.equal(h.element.paused,true);
});
test('real play handler honors the timeline seek while decoder still reports its former end',()=>{
  const source=readFileSync(new URL('../src/components/Player.tsx',import.meta.url),'utf8');
  const file=ts.createSourceFile('Player.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let callback;
  const visit=node=>{if(ts.isVariableDeclaration(node)&&node.name.getText(file)==='togglePlay')callback=node.initializer.getText(file);ts.forEachChild(node,visit);};visit(file);
  for(const playhead of [14.8,15]){
    const actions=[];let plays=0;
    const globals={exports:{},currentId:'p',project:{},hasRenderable:true,playingRef:{current:false},audioTransportRef:{current:{}},
      videoRef:{current:{paused:true,currentTime:15}},mode:'video',mediaEndSecs:15,playhead,windowStart:0,
      reportedVideoTimeRef:{current:14.8},setVideoTime:()=>{},setPlaying:()=>{},requestVideoPlay:()=>plays++,dispatch:a=>actions.push(a)};
    runInNewContext(ts.transpileModule(`exports.toggle=${callback}`,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText,bindPlayerSeek(globals));
    globals.exports.toggle();assert.equal(plays,1);
    assert.equal(globals.videoRef.current.currentTime, playhead === 15 ? 0 : 15);
    assert.equal(globals.qaSeekReads, 0);
    assert.equal(actions.some(a=>a.type==='PLAYHEAD_SET'&&a.t===0),playhead===15);
  }
});

// Execute Player's actual callback: a video timeupdate may arrive before the
// delayed native-audio RAF starts/commits the already-ready mix.
function playerTimeupdate({ native = true, active = false, playing = true } = {}) {
  const source = readFileSync(new URL('../src/components/Player.tsx', import.meta.url), 'utf8');
  const file = ts.createSourceFile('Player.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback;
  const visit = node => {
    if (ts.isVariableDeclaration(node) && node.name.getText(file) === 'onTimeUpdate') callback = node.initializer.arguments[0].getText(file);
    ts.forEachChild(node, visit);
  }; visit(file);
  const video = { currentTime: 3.217, paused: !playing }, actions = [], times = [];
  const context = { exports: {}, videoRef: { current: video }, scrubbingRef: { current: false },
    nativeAudioRef: { current: native }, audioTransportRef: { current: { active } }, playingRef: { current: playing },
    windowStart: 0, loopRef: { current: { enabled: false, in: null, out: null } }, mode: 'video',
    usePrepared: true, mediaEndSecs: 6, setVideoTime: t => times.push(t), playheadRef: { current: 3 },
    reportedVideoTimeRef: { current: 3 }, suppressSeek: { current: false }, dispatchRef: { current: action => actions.push(action) } };
  runInNewContext(ts.transpileModule(`exports.callback = ${callback}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, bindPlayerSeek(context));
  context.exports.callback({ currentTarget: video });
  assert.equal(context.qaSeekReads, 0);
  return { actions, times };
}
test('ready native mix owns start position before asynchronous startup commits', () => {
  const result = playerTimeupdate();
  assert.equal(result.actions.length, 0, 'preview video must not advance the selected audio start');
  assert.equal(result.times.length, 0);
});
test('video remains the fallback clock without a ready native mix', () => {
  const result = playerTimeupdate({ native: false });
  assert.equal(result.actions[0].t, 3.217);
});

test('primed voices stay paused throughout a delayed final seek, then resume together',async()=>{
  const h=harness();h.start.start(h.request);await h.unlock();
  h.plays[0].resolve();await flush();assert.equal(h.elements[0].paused,true,'early decoder must wait paused');
  h.elements.forEach(e=>e.seeking=true);
  h.plays[1].resolve();await flush();
  assert.ok(h.elements.every(e=>e.paused&&e.muted));assert.equal(h.commit(),0);
  h.elements[0].seeking=false;h.elements[0].dispatchEvent(new Event('seeked'));await flush();
  // Model 80ms of native playback advancement while the other seek is pending.
  // A paused element must not consume this part of the selected sentence.
  for(const e of h.elements)if(!e.paused)e.currentTime+=.08;
  assert.ok(h.elements.every(e=>e.currentTime===3));assert.equal(h.commit(),0);
  h.elements[1].seeking=false;h.elements[1].dispatchEvent(new Event('seeked'));await flush();
  assert.equal(h.commit(),1);assert.equal(h.failures(),0);
  assert.ok(h.elements.every(e=>!e.paused&&!e.muted&&e.currentTime===3));
});
test('cancellation during the final resume cannot revive primed voices',async()=>{
  const h=harness(), resumed=[deferred(),deferred()];
  h.elements.forEach((e,i)=>{let calls=0;e.play=()=> (++calls===1?h.plays[i].promise:resumed[i].promise).then(()=>{e.paused=false;});});
  h.start.start(h.request);await h.unlock();h.plays.forEach(p=>p.resolve());await flush();
  assert.equal(h.commit(),0);h.setCurrent(false);h.start.cancel();
  resumed.forEach(p=>p.resolve());await flush();
  assert.equal(h.commit(),0);assert.equal(h.failures(),0);assert.ok(h.elements.every(e=>e.paused&&e.muted));
});

const lastDiagnostic = h => h.start.diagnostics().events.at(-1);
test('startup diagnostics are opt-in, bounded, detached and contain only local enums/ids', async () => {
  const off = harness(); off.start.start(off.request); off.start.cancel();
  assert.equal(off.start.diagnostics().events.length, 0);
  const h = harness(true);
  h.request.key = '/private/media.mp4?secret=abc';
  for (let i = 0; i < 100; i++) { h.start.start(h.request); h.start.cancel(); }
  const snapshot = h.start.diagnostics();
  assert.equal(snapshot.events.length, 64); assert.equal(snapshot.dropped, 136);
  assert.equal(snapshot.events[0].attempt, 1); assert.equal(snapshot.events.at(-1).attempt, 100);
  assert.deepEqual(Object.keys(snapshot.events[0]), ['attempt', 'phase', 'reason']);
  assert.ok(!JSON.stringify(snapshot).includes('private'));
  snapshot.events[0].reason = 'tampered'; snapshot.events.pop();
  assert.equal(h.start.diagnostics().events.length, 64);
  assert.equal(h.start.diagnostics().events[0].reason, 'started');
});
test('diagnostics distinguish context, seek and play failures without exception text', async () => {
  const context = harness(true); context.start.start(context.request); context.resume.reject(new Error('/secret/url')); await flush();
  assert.equal(lastDiagnostic(context).reason, 'context-failed'); assert.equal(lastDiagnostic(context).phase, 'context');
  const seek = harness(true); Object.defineProperty(seek.elements[0], 'currentTime', { get: () => 3, set: () => { throw new Error('/secret/path'); } });
  seek.start.start(seek.request); await seek.unlock();
  assert.equal(lastDiagnostic(seek).reason, 'seek-failed'); assert.equal(lastDiagnostic(seek).phase, 'initial-seek');
  const play = harness(true); play.start.start(play.request); await play.unlock(); play.plays[0].reject(new Error('/secret/play')); await flush();
  assert.equal(lastDiagnostic(play).reason, 'play-failed'); assert.equal(lastDiagnostic(play).phase, 'prime-play');
  for (const h of [context, seek, play]) { assert.equal(h.failures(), 1); assert.ok(!JSON.stringify(h.start.diagnostics()).includes('secret')); }
});
test('diagnostics distinguish timeout, stale, explicit cancellation and supersession', async () => {
  const timeout = harness(true); timeout.start.start(timeout.request); [...timeout.timers.values()][0]();
  assert.equal(lastDiagnostic(timeout).reason, 'timeout'); assert.equal(timeout.failures(), 1);
  const stale = harness(true); stale.start.start(stale.request); stale.setCurrent(false); await stale.unlock();
  assert.equal(lastDiagnostic(stale).reason, 'stale'); assert.equal(stale.failures(), 0);
  for (const reason of ['cancelled', 'audition-stop', 'disabled', 'transport-stopped', 'scrubbing', 'disposed']) {
    const h = harness(true); h.start.start(h.request); h.start.cancel(reason); await h.unlock();
    assert.equal(lastDiagnostic(h).reason, reason); assert.equal(h.failures(), 0); assert.equal(h.commit(), 0);
  }
  const h = harness(true); h.start.start(h.request); h.start.start({...h.request, key:'replacement'});
  assert.equal(h.start.diagnostics().events.at(-2).reason, 'superseded');
  assert.equal(lastDiagnostic(h).attempt, 2);
});
test('diagnostics distinguish commit refusal, running context interruption and success', async () => {
  for (const accepted of [true, false]) {
    const h = harness(true); h.request.commit = () => accepted;
    h.start.start(h.request); await h.unlock(); h.plays.forEach(p => p.resolve()); await flush();
    assert.equal(lastDiagnostic(h).reason, accepted ? 'committed' : 'commit-refused');
    assert.equal(lastDiagnostic(h).phase, accepted ? 'running' : 'commit');
    if (accepted) { h.context.state = 'suspended'; h.start.start(h.request); assert.equal(lastDiagnostic(h).reason, 'context-interrupted'); assert.equal(h.failures(), 1); }
    else assert.equal(h.failures(), 1);
  }
});
test('diagnostic buffer failures cannot change commit, rollback or callback counts', async () => {
  for (const fail of [false, true]) {
    const h = harness(true); Object.freeze(h.start.evidence);
    h.start.start(h.request); await h.unlock();
    if (fail) h.plays[0].reject(); else h.plays.forEach(p => p.resolve());
    await flush(); assert.equal(h.commit(), fail ? 0 : 1); assert.equal(h.failures(), fail ? 1 : 0);
    h.start.cancel(); assert.ok(h.elements.every(e => e.paused && e.muted));
  }
});
test('diagnostics classify final seek and resume drift separately', async () => {
  const seek = harness(true); seek.start.start(seek.request); await seek.unlock();
  seek.elements.forEach(e => e.seeking = true); seek.plays.forEach(p => p.resolve()); await flush();
  seek.elements[0].seeking = false; seek.elements[0].dispatchEvent(new Event('seeked')); seek.elements[0].currentTime = 3.2;
  seek.elements[1].seeking = false; seek.elements[1].dispatchEvent(new Event('seeked')); await flush();
  assert.equal(lastDiagnostic(seek).reason, 'seek-drift'); assert.equal(lastDiagnostic(seek).phase, 'final-seek');
  const resume = harness(true);
  resume.elements.forEach(e => { let calls = 0; e.play = async () => { e.paused = false; if (++calls === 2) e.currentTime += .1; }; });
  resume.start.start(resume.request); await resume.unlock(); await flush();
  assert.equal(lastDiagnostic(resume).reason, 'resume-drift'); assert.equal(lastDiagnostic(resume).phase, 'resume-play');
  for (const h of [seek, resume]) { assert.equal(h.failures(), 1); assert.equal(h.commit(), 0); }
});
test('keyboard evidence snapshot and logging failures do not escape the listener', () => {
  const source = readFileSync(new URL('../e2e/transcript-flow.spec.ts', import.meta.url), 'utf8');
  const file = ts.createSourceFile('transcript-flow.spec.ts', source, ts.ScriptTarget.Latest, true);
  let snapshot, emit;
  const visit = node => {
    if (ts.isBinaryExpression(node) && node.left.getText(file) === '(window as any).__keyboardEvidence') snapshot = node.right.getText(file);
    if (ts.isVariableDeclaration(node) && node.name.getText(file) === 'emitEvidence') emit = node.initializer.getText(file);
    ts.forEachChild(node, visit);
  };
  visit(file); assert.ok(snapshot && emit);
  for (const getter of [false, true]) {
    const window = {}, samples = [{time: 1.2, playing: true}];
    if (getter) Object.defineProperty(window, '__cutvokeNativeStartupEvidence', {get: () => {throw Error('private');}});
    else window.__cutvokeNativeStartupEvidence = () => {throw Error('private');};
    const exports = {};
    runInNewContext(ts.transpileModule(`window.__keyboardEvidence = ${snapshot}; exports.emit = ${emit}`, {compilerOptions: {target: ts.ScriptTarget.ES2022}}).outputText,
      {window, samples, dropped: 3, exports, console: {debug: () => {throw Error('logger');}}});
    assert.equal(window.__keyboardEvidence().samples, samples); assert.equal(window.__keyboardEvidence().dropped, 3);
    assert.equal(window.__keyboardEvidence().startup, undefined);
    assert.doesNotThrow(() => exports.emit());
  }
});
