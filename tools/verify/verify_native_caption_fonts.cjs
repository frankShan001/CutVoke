// Call against an already running packaged backend; no runtime/test hooks added.
// This checks frozen CLI export, not GUI title-preview/IME acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFileSync} = require('node:child_process');
const hash = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const FIXTURE_FONT_SIZE = 144; // Domain uses 1080p units: 24px at the 180p fixture.
const SOURCE = 'd68bafcb48a2707749396aa12bbbd833cb70401f3a9a689fd2902c7e0d295964';
function validateOrigin(origin) {
  const u = new URL(origin);
  assert.equal(u.protocol, 'http:');
  assert.ok(['127.0.0.1', '[::1]'].includes(u.hostname), 'literal loopback required');
  assert.ok(!u.username && !u.password && !u.search && !u.hash && u.pathname === '/', 'origin only');
  return u.origin;
}
function verifyRuntime(runtime, engine, dataPath) {
  assert.equal(fs.realpathSync(runtime.pythonExecutable), fs.realpathSync(engine), 'wrong packaged backend');
  assert.equal(fs.realpathSync(runtime.dataPath), fs.realpathSync(dataPath), 'wrong project database');
  assert.ok(Number.isSafeInteger(runtime.processId) && runtime.processId > 0);
}
function verifyAssets(assets) {
  const dir = path.join(assets, 'caption_fonts');
  const m = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  assert.equal(m.recipe, 1); assert.equal(m.source_sha256, SOURCE);
  assert.equal(hash(fs.readFileSync(path.join(assets, 'fonts/NotoSansSC-VF.ttf'))), SOURCE);
  const license = fs.readFileSync(path.join(dir, 'OFL-Sans.txt'));
  assert.equal(hash(license), m.license_sha256);
  assert.deepEqual(license, fs.readFileSync(path.join(assets, 'fonts/OFL-Sans.txt')));
  for (const weight of [400,700]) {
    assert.equal(m.instances[weight].filename, `sans-${weight}-v1.ttf`);
    assert.equal(hash(fs.readFileSync(path.join(dir, `sans-${weight}-v1.ttf`))), m.instances[weight].sha256);
  }
  return m;
}
function verifyCache(cache, manifest, weight) {
  const digest = manifest.instances[weight].sha256;
  const name = `${manifest.source_sha256}-${weight}-v2-${digest}.ttf`;
  assert.equal(hash(fs.readFileSync(path.join(cache, name))), digest);
  assert.ok(!fs.readdirSync(cache).some(n => n.endsWith('-v1.ttf')), 'runtime fallback used');
  return {weight, sha256:digest, cache:name};
}
function verifyPixels(pixels, frames) {
  assert.equal(pixels.length, frames * 320 * 180 * 3);
  const frame = pixels.subarray(7 * 320 * 180 * 3, 8 * 320 * 180 * 3);
  const bright = [...frame].filter(v => v > 180).length;
  assert.ok(bright > 100 && bright < frame.length / 2, `missing caption or invalid background: brightChannels=${bright}`);
  return {frames, brightChannels:bright};
}
async function verifyNativeCaptionFonts({origin, engine, ffmpeg, out, env, dataPath, assets}) {
  origin = validateOrigin(origin);
  const relative = path.relative(path.dirname(fs.realpathSync(engine)), fs.realpathSync(assets));
  assert.ok(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'assets must belong to extracted engine');
  const databaseRelative = path.relative(fs.realpathSync(out), fs.realpathSync(dataPath));
  assert.ok(databaseRelative && !databaseRelative.startsWith('..') && !path.isAbsolute(databaseRelative), 'use only runner-owned acceptance database');
  const manifest = verifyAssets(assets);
  const work = fs.mkdtempSync(path.join(out, 'caption-font-'));
  const api = async (route, body) => {
    const r = await fetch(origin+'/api/v1/'+route, { ...(body === undefined ? {} : {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)}), signal:AbortSignal.timeout(30_000),redirect:'error'});
    const value = await r.json(); assert.ok(r.ok, JSON.stringify(value)); return value;
  };
  const runtime = await api('runtime');
  verifyRuntime(runtime, engine, dataPath); // Before the first project write.
  const results = [];
  for (const weight of [400,700]) {
    const id = 'native-font-'+crypto.randomUUID();
    const source = path.join(work, `black-${weight}.mp4`);
    execFileSync(ffmpeg, ['-v','error','-nostdin','-y','-f','lavfi','-i','color=c=black:s=320x180:r=15:d=1','-an','-c:v','libx264','-pix_fmt','yuv420p',source], {env,timeout:30_000});
    await api('projects', {projectId:id,name:id,width:320,height:180,fps:15});
    const command = async(type,payload) => { const p=await api(`projects/${id}`); return api(`projects/${id}/commands`, {
      type,payload,expectedRevision:p.revision,commandId:crypto.randomUUID(),actor:{kind:'human',id:'native-font-acceptance'}}); };
    await command('clip.insert', {trackId:'v',clipId:'black',createTrackKind:'video',sourcePath:source,
      timelineStart:{num:0,den:1},timelineEnd:{num:1,den:1}});
    await command('caption.add', {captionId:'c',text:'字幕 Aa 123',start:{num:0,den:1},end:{num:1,den:1},
      fontFamily:'Noto Sans SC',bold:weight===700,fontSize:FIXTURE_FONT_SIZE,color:'#FFFFFF'});
    const directory = await api(`projects/${id}/directory`);
    const target = path.join(directory.exportDirectory, 'font.mp4');
    const cache = fs.mkdtempSync(path.join(work, `cold-${weight}-`));
    const childEnv = {...env, CUTVOKE_FONT_CACHE:cache}; delete childEnv.CUTVOKE_FONT;
    const start = performance.now();
    execFileSync(engine, ['export','--project',id,'--data',dataPath,'--out',target], {env:childEnv,timeout:45_000,maxBuffer:10*1024*1024});
    const seconds = (performance.now()-start)/1000;
    const evidence = verifyCache(cache,manifest,weight);
    fs.writeFileSync(path.join(work, `cache-${weight}.json`), JSON.stringify({...evidence,seconds},null,2)+'\n','utf8');
    const pixels = execFileSync(ffmpeg,['-v','error','-nostdin','-i',target,'-pix_fmt','rgb24','-f','rawvideo','-'], {env,timeout:30_000,maxBuffer:4*1024*1024});
    results.push({...evidence,seconds,...verifyPixels(pixels,15)});
  }
  const result = {passed:true,scope:'packaged frozen CLI cold caption export; pixel smoke only, not glyph fidelity or GUI preview/IME',source:SOURCE,results};
  fs.writeFileSync(path.join(work,'verification.json'),JSON.stringify(result,null,2)+'\n','utf8');
  return result;
}
module.exports = {verifyNativeCaptionFonts,verifyAssets,verifyCache,verifyPixels,validateOrigin,verifyRuntime,FIXTURE_FONT_SIZE};
