import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import ts from "typescript";
const { outputText } = ts.transpileModule(readFileSync(new URL("../e2e/projectEquivalence.ts", import.meta.url), "utf8"),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } });
const { structuralSignature } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const project = (first, second) => ({ sequence: { width: 320, height: 180, fps: { num: "30", den: "1" }, captions: [],
  tracks: [{ id: "v", kind: "video", clips: [{ id: "clip", assetRef: { sourcePath: "test.mp4" },
    effects: [first, second].map((instanceId, index) => ({ effectId: "cutvoke.color", instanceId, params: { brightness: index } })),
    keyframes: { [`effect:${second}:brightness`]: [{ id: "key", time: { num: "0", den: "1" }, value: .3 }] } }] }] } });
test("independent commands compare fresh IDs through their exact effect reference graph", () => {
  assert.equal(structuralSignature(project("a", "b")), structuralSignature(project("c", "d")));
});
test("normalization preserves parameter values, order, keyframe targets and timing", () => {
  const source = project("a", "b");
  for (const mutate of [
    clip => { clip.effects[0].params.brightness = .1; },
    clip => { clip.effects.reverse(); },
    clip => { clip.keyframes["effect:a:brightness"] = clip.keyframes["effect:b:brightness"]; delete clip.keyframes["effect:b:brightness"]; },
    clip => { clip.keyframes["effect:b:brightness"][0].time.num = "1"; },
  ]) {
    const different = structuredClone(source); mutate(different.sequence.tracks[0].clips[0]);
    assert.notEqual(structuralSignature(different), structuralSignature(source));
  }
});
test("broken and duplicate instance references fail instead of disappearing", () => {
  assert.throws(() => structuralSignature(project("a", "a")), /Duplicate/);
  const broken = project("a", "b"); delete broken.sequence.tracks[0].clips[0].effects[1].instanceId;
  assert.throws(() => structuralSignature(broken), /Unresolved/);
});
