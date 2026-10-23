import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";

const moduleUrl = source => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
  return `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`;
};
const load = name => readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), "utf8");
const rational = moduleUrl(load("rational").replace('import { tr } from "../i18n";', 'const tr = text => text;'));
const curve = moduleUrl(load("speedCurve").replace('"./rational"', JSON.stringify(rational)));
const timing = load("clipTiming").replace('"./rational"', JSON.stringify(rational)).replace('"./speedCurve"', JSON.stringify(curve));
const { clipSourceRange, parseTimingInput, timingPayload } = await import(moduleUrl(timing));
const r = (num, den=1) => ({num: String(num), den: String(den)});
const base = {id: "clip", sourceStart:r(2),timelineStart:r(5),timelineEnd:r(7),speed:r(2)};

test("source out reflects speed, reverse, ramps and freeze", () => {
  assert.deepEqual(clipSourceRange(base), {start:2,end:6});
  assert.deepEqual(clipSourceRange({...base,sourceStart:r(2),speed:r(-2)}), {start:6,end:2});
  assert.deepEqual(clipSourceRange({...base,speedCurve:{sourceDuration:r(8),points:[]}}), {start:2,end:10});
  assert.deepEqual(clipSourceRange({...base,freezeAt:r(3)}), {start:3,end:3});
});
test("seconds, non-drop timecode and frames snap to exact fractional-fps grid", () => {
  const fps=r(30000,1001);
  assert.equal(parseTimingInput("00:00:01:15",fps),45*1001/30000);
  assert.equal(parseTimingInput("45f",fps),45*1001/30000);
  assert.deepEqual(timingPayload(base,"duration",1.001,fps).timelineEnd,r(180180,30000));
  for(const value of ["", "NaN", "-1", "00:00:00:30", "00:60:00:00"]) assert.throws(()=>parseTimingInput(value,fps));
});
test("numeric left trim preserves source mapping in both playback directions", () => {
  assert.deepEqual(timingPayload(base,"start",5.5,r(30)),{clipId:"clip",timelineStart:r(165,30),sourceStart:r(3000000,1000000)});
  const reverse={...base,sourceStart:r(2),speed:r(-2)};
  assert.deepEqual(timingPayload(reverse,"sourceIn",5,r(30)),{clipId:"clip",timelineStart:r(165,30)});
  assert.deepEqual(timingPayload(reverse,"sourceOut",3,r(30)).timelineEnd,r(195,30));
  assert.deepEqual(timingPayload(reverse,"sourceOut",3,r(30)).sourceStart,r(3000000,1000000));
  assert.throws(()=>timingPayload(base,"duration",0,r(30)));
});
