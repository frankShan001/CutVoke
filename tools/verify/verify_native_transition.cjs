// Pixel/clock regression through each platform's real frozen export engine.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { execFileSync } = require("node:child_process");

async function verifyNativeTransition({ origin, engine, ffmpeg, out, env, dataPath }) {
  const projectId = "native-canvas-eof-" + crypto.randomUUID().slice(0, 8);
  async function api(route, body) {
    const response = await fetch(origin + "/api/v1/" + route, {
      ...(body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
      signal: AbortSignal.timeout(120_000),
    });
    const value = await response.json();
    assert.ok(response.ok, JSON.stringify(value)); return value;
  }
  await api("projects", { projectId, name: projectId, width: 160, height: 90, fps: 15 });
  for (const [index, color] of ["red", "green", "blue"].entries()) {
    const source = path.join(out, `clock-${color}.mp4`);
    execFileSync(ffmpeg, ["-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
      `color=c=${color}:s=160x90:r=15:d=1`, "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", source], { env, timeout: 30_000 });
    const uploaded = await fetch(origin + "/api/v1/assets?projectId=" + encodeURIComponent(projectId) + "&name=" + encodeURIComponent(path.basename(source)), {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: fs.readFileSync(source),
      signal: AbortSignal.timeout(30_000),
    });
    const asset = await uploaded.json(); assert.ok(uploaded.ok, JSON.stringify(asset));
    const current = await api(`projects/${projectId}`);
    await api(`projects/${projectId}/commands`, { type: "clip.insert", expectedRevision: current.revision,
      commandId: crypto.randomUUID(), actor: { kind: "human", id: "native-clock-acceptance" }, payload: {
        trackId: "video", clipId: color, ...(index === 0 ? { createTrackKind: "video" } : {}),
        assetId: asset.assetId, sourcePath: asset.path,
        timelineStart: { num: String(index), den: "1" }, timelineEnd: { num: String(index + 1), den: "1" },
      } });
  }
  const current = await api(`projects/${projectId}`);
  await api(`projects/${projectId}/commands`, { type: "effect.setTransition", expectedRevision: current.revision,
    commandId: crypto.randomUUID(), actor: { kind: "human", id: "native-clock-acceptance" },
    payload: { clipId: "green", effectId: "cutvoke.transition.crossfade", params: { duration: .3 } } });
  const directory = await api(`projects/${projectId}/directory`);
  assert.equal(directory.storageKind, "directory");
  const results = [];
  for (let round = 0; round < 3; round++) {
    const output = path.join(directory.exportDirectory, `clock-export-${round}.mp4`);
    execFileSync(engine, ["export", "--project", projectId, "--data", dataPath, "--out", output], {
      env, encoding: "utf8", timeout: 120_000, maxBuffer: 10 * 1024 * 1024,
    });
    const pixels = execFileSync(ffmpeg, ["-v", "error", "-nostdin", "-i", output,
      "-vf", "crop=2:2:0:0", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], { env, timeout: 30_000 });
    assert.equal(pixels.length, 45 * 12, "The complete 3s export must decode to 45 frames");
    const samples = [];
    for (const [at, expected] of [[.5, [253, 0, 0]], [1.5, [0, 127, 0]], [2.5, [0, 0, 254]]]) {
      const rgb = [...pixels.subarray(Math.floor(at * 15) * 12, Math.floor(at * 15) * 12 + 3)];
      assert.ok(rgb.every((value, channel) => Math.abs(value - expected[channel]) <= 4), JSON.stringify({ round, at, rgb, expected }));
      samples.push({ at, rgb });
    }
    results.push({ round, decodedFrames: 45, samples });
    fs.writeFileSync(path.join(out, "transition-clock-verification.json"), JSON.stringify({ projectId, results }, null, 2));
  }
  return { projectId, results };
}
module.exports = { verifyNativeTransition };
