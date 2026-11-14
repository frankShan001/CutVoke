const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const crypto=require('node:crypto');
const {verifyCache,verifyPixels,verifyAssets}=require('./verify_native_caption_fonts.cjs');
test('cache requires exact v2 bytes and rejects fallback',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cv-font-gate-'));
 try {
 const bytes=Buffer.from('test-only'),sha=crypto.createHash('sha256').update(bytes).digest('hex');
 const m={source_sha256:'source',instances:{400:{sha256:sha}}};
 const file=path.join(dir,`source-400-v2-${sha}.ttf`);
 assert.throws(()=>verifyCache(dir,m,400));
 fs.writeFileSync(file,bytes); assert.equal(verifyCache(dir,m,400).sha256,sha);
 fs.writeFileSync(file,'corrupt'); assert.throws(()=>verifyCache(dir,m,400));
 fs.writeFileSync(file,bytes);fs.writeFileSync(path.join(dir,'fallback-v1.ttf'),bytes);
 assert.throws(()=>verifyCache(dir,m,400),/fallback/);
 } finally {fs.rmSync(dir,{recursive:true});}
});
test('decode rejects black, all white, truncated; accepts finite text-like patch',()=>{
 const b=Buffer.alloc(15*320*180*3);
 assert.throws(()=>verifyPixels(b,15),/caption/);
 assert.throws(()=>verifyPixels(b.subarray(1),15));
 b.fill(255);assert.throws(()=>verifyPixels(b,15),/caption/);
 b.fill(0);b.fill(255,7*320*180*3,7*320*180*3+300);
 assert.equal(verifyPixels(b,15).brightChannels,300);
});
test('absent packaged assets fail rather than falling back',()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cv-font-assets-'));
 try{assert.throws(()=>verifyAssets(dir));}finally{fs.rmSync(dir,{recursive:true});}
});
test('origin only permits literal loopback HTTP and never credentials or redirect destinations',()=>{
 const {validateOrigin}=require('./verify_native_caption_fonts.cjs');
 for(const s of ['http://example.com','http://localhost:80','https://127.0.0.1','http://127.0.0.1/x','http://a@127.0.0.1','http://127.0.0.1?x'])assert.throws(()=>validateOrigin(s));
 assert.equal(validateOrigin('http://127.0.0.1:1234'),'http://127.0.0.1:1234');
 assert.equal(validateOrigin('http://[::1]:1234'),'http://[::1]:1234');
});
test('runtime executable and database identities must both match',()=>{
 const {verifyRuntime}=require('./verify_native_caption_fonts.cjs');
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'cv-font-identity-'));
 try{
 const e=path.join(dir,'engine'),d=path.join(dir,'database'),other=path.join(dir,'other');
 for(const f of [e,d,other])fs.writeFileSync(f,'');
 const r={pythonExecutable:e,dataPath:d,processId:123};
 verifyRuntime(r,e,d);
 assert.throws(()=>verifyRuntime({...r,pythonExecutable:other},e,d));
 assert.throws(()=>verifyRuntime({...r,dataPath:other},e,d));
 }finally{fs.rmSync(dir,{recursive:true});}
});

test('smoke fixture uses visible 24px text after domain 1080p scaling',()=>{
 const {FIXTURE_FONT_SIZE}=require('./verify_native_caption_fonts.cjs');
 assert.equal(FIXTURE_FONT_SIZE*180/1080,24);
});
