import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return { promise, resolve, reject }; };
const flush = async () => { for(let i=0;i<20;i++) await Promise.resolve(); };
function harness() {
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
  const start=new exports.NativeAudioStartup();
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
    gain:{gain:{setValueAtTime:v=>stats.gains.push(v)}}};
  const voices=new Map([['a',voice]]);
  const latest={current:{project:{projectId:'p',revision:1,sequence:{id:'s',tracks:[{id:'t',kind:'audio',clips:[clip]}]}},scope:'p',seek:0,time:3,playing:true,scrubbing:false,transportMode:imageMode?'image':'video',
    onReady:()=>{}, playbackClock:(initialize,reset)=>{if(reset){stats.reset++;active=false;}if(initialize){stats.commit++;active=true;}return imageMode?null:{time:3,playing:active};}}};
  const live={exports:{},latest,voices:{current:voices},startup:{current:host.start},context:{current:{state:'running',currentTime:10,resume:()=>Promise.resolve()}},
    meter:{current:null},disposed:false,frame:0,requestAnimationFrame:()=>1,AUDIO_FX:new Set(['audio-effect']),rationalToSecs:v=>v||0,sourceTime:(_clip,time)=>time};
  runInNewContext(ts.transpileModule(`${generation}\nconst update=${update}; exports.update=update; exports.generation=nativeAudioGeneration`,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText,live);
  return {update:live.exports.update,generation:live.exports.generation,stats,latest,element,clip};
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
    nativeAudioClockRef:{current:()=>deviceTime},performance:{now:()=>deviceTime*1000}};
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
