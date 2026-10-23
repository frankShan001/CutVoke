import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";
const { outputText }=ts.transpileModule(readFileSync(new URL("../src/lib/timelineMediaCache.ts",import.meta.url),"utf8"),
  {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}});
const {acquireTimelineMedia}=await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test("visible strips share work, limit concurrent requests and drop unseen queued work",async()=>{
  const original=globalThis.fetch, calls=[];
  globalThis.fetch=url=>new Promise(resolve=>calls.push({url,finish:()=>resolve(new Response(new Blob(["png"]),{headers:{"Content-Type":"image/png"}}))}));
  const leases=[];
  try{
    const one=acquireTimelineMedia("one"),same=acquireTimelineMedia("one"),two=acquireTimelineMedia("two");
    const unseen=acquireTimelineMedia("offscreen"),three=acquireTimelineMedia("three");
    leases.push(one,same,two,unseen,three);
    assert.deepEqual(calls.map(call=>call.url),["one","two"]);
    unseen.release();assert.equal(await unseen.promise,null);
    one.release();same.release(); // Running decodes finish; scrolling must not spawn extra server work.
    assert.equal(calls.length,2);
    calls[0].finish();await one.promise;await flush();
    assert.deepEqual(calls.map(call=>call.url),["one","two","three"]);
    calls[1].finish();calls[2].finish();await Promise.all([two.promise,three.promise]);await flush();
    const cached=acquireTimelineMedia("one");leases.push(cached);assert.equal(await cached.promise,await one.promise);
    assert.equal(calls.length,3);
  }finally{leases.forEach(lease=>lease.release());globalThis.fetch=original;}
});
