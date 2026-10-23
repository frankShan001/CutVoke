import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";
const { outputText } = ts.transpileModule(readFileSync(new URL("../src/lib/projectSync.ts", import.meta.url), "utf8"),
  { compilerOptions: {target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022} });
const { expandProject, reuseProjectStructure } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const sequence = id => ({id,width:1920,height:1080,fps:{num:"30",den:"1"},tracks:[
  {id:"v",kind:"video",clips:[{id:"a",timelineStart:{num:"0",den:"1"}},{id:"b",timelineStart:{num:"5",den:"1"}}]},
  {id:"audio",kind:"audio",clips:[]}],captions:[]});
const raw = {schemaVersion:"1",projectId:"test",revision:"1",activeSequenceId:"s1",sequences:[sequence("s1"),sequence("s2")]};

test("compact decoding preserves every sequence and aliases the selected sequence", () => {
  const project = expandProject(structuredClone(raw));
  assert.equal(project.sequence,project.sequences[0]);
  const switched = expandProject({...raw,activeSequenceId:"s2"});
  assert.equal(switched.sequence,switched.sequences[1]);
  assert.equal(switched.sequences.length,2);
  assert.throws(()=>expandProject({...raw,activeSequenceId:"missing"}));
});
test("a single changed clip retains other clips, tracks and inactive sequence identities", () => {
  const previous=expandProject(structuredClone(raw)), updated=structuredClone(raw);
  updated.revision="2";
  updated.sequences[0].tracks[0].clips[0].timelineStart.num="1";
  const next=reuseProjectStructure(previous,expandProject(updated));
  assert.notEqual(next.sequence,previous.sequence);
  assert.notEqual(next.sequence.tracks[0].clips[0],previous.sequence.tracks[0].clips[0]);
  assert.equal(next.sequence.tracks[0].clips[1],previous.sequence.tracks[0].clips[1]);
  assert.equal(next.sequence.tracks[1],previous.sequence.tracks[1]);
  assert.equal(next.sequences[1],previous.sequences[1]);
  assert.equal(next.sequence,next.sequences[0]);
  assert.equal(previous.sequence.tracks[0].clips[0].timelineStart.num,"0");
});
