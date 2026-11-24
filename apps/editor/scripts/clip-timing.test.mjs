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
const { forwardLeftTrim } = await import(moduleUrl(readFileSync(new URL("../src/components/timeline/util.ts", import.meta.url), "utf8").replace('"../../lib/rational"', JSON.stringify(rational))));
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

test("forward left trim conserves source out at normal and fast speeds", () => {
  for (const speed of [1, 2]) {
    const clip = { ...base, speed: r(speed) };
    const next = forwardLeftTrim(clip, 5.5, r(30));
    assert.deepEqual(next, { timelineStart: r(11, 2), sourceStart: speed === 1 ? r(5, 2) : r(3) });
    assert.equal(Number(next.sourceStart.num) / Number(next.sourceStart.den) + 1.5 * speed, 2 + 2 * speed);
  }
});

test("forward left trim clamps on-grid inward from source zero and an off-grid right edge", () => {
  const clip = { ...base, timelineStart: r(1001, 1000), timelineEnd: r(2), sourceStart: r(1, 10), speed: r(3, 2) };
  const fps = r(30000, 1001);
  assert.deepEqual(forwardLeftTrim(clip, -100, fps), { timelineStart: r(29029, 30000), sourceStart: r(999, 20000) });
  assert.deepEqual(forwardLeftTrim(clip, 100, fps), { timelineStart: r(29029, 15000), sourceStart: r(7507, 5000) });
  assert.deepEqual(clip.timelineEnd, r(2));
});
