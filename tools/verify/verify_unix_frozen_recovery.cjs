// Linux native QA only. Observe existing fail-closed behavior; never enable frozen recovery.
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const width = 64, height = 48, fps = 12, clipCount = 26, frames = clipCount * 9;
const seconds = frames / fps;
const colors = [0, 1, 0, 0, 1, 1, 0];
const maxBuffer = 8 * 1024 * 1024;

function digest(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

async function verifyUnixFrozenRecovery({ origin, engine, ffmpeg, ffprobe, env, dataPath, sourcePython, sourceRoot }) {
  const projectId = "native-frozen-recovery-" + crypto.randomUUID();
  const cpuEnv = { ...env, CUTVOKE_GPU_ACCELERATION: "0" };
  const sourceEnv = { ...cpuEnv, PATH: path.dirname(ffmpeg), PYTHONPATH: sourceRoot, PYTHONDONTWRITEBYTECODE: "1" };
  const run = (file, args, childEnv, timeout = 30_000) =>
    execFileSync(file, args, { env: childEnv, timeout, maxBuffer });
  const media = args => run(ffmpeg, ["-v", "error", "-nostdin", ...args], cpuEnv);
  const json = (file, args, childEnv, timeout) => JSON.parse(run(file, args, childEnv, timeout));
  const sourceArgs = ["-m", "cutvoke.interfaces.cli"];
  const sourceDoctor = json(sourcePython, [...sourceArgs, "doctor", "--mcp-only", "--json"], sourceEnv);
  assert.equal(sourceDoctor.ok, true);
  assert.notEqual(sourceDoctor.runtime.pythonExecutable, engine);
  assert.equal(sourceDoctor.runtime.packageDirectory, fs.realpathSync(path.join(sourceRoot, "cutvoke")));
  assert.equal(sourceDoctor.ffmpeg, fs.realpathSync(ffmpeg));
  assert.equal(sourceDoctor.ffprobe, fs.realpathSync(ffprobe));
  // The native caller already asserts the extracted engine resolves these exact tools.

  async function api(route, body, upload = false) {
    const raw = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": upload ? "application/octet-stream" : "application/json" },
      body: upload ? body : JSON.stringify(body),
    };
    const response = await fetch(origin + "/api/v1/" + route, { ...raw, signal: AbortSignal.timeout(30_000) });
    const result = await response.json();
    assert.ok(response.ok, JSON.stringify(result));
    return result;
  }
  const upload = (name, bytes) => api(
    "assets?projectId=" + encodeURIComponent(projectId) + "&name=" + encodeURIComponent(name), bytes, true);
  await api("projects", { projectId, name: projectId, width, height, fps });
  const stills = [];
  for (const color of ["red", "blue"]) {
    stills.push(await upload(color + ".png", media(["-f", "lavfi", "-i", `color=c=${color}:s=${width}x${height}`,
      "-frames:v", "1", "-threads", "1", "-c:v", "png", "-f", "image2pipe", "pipe:1"])));
  }
  const tonePCM = media(["-f", "lavfi", "-i", `sine=frequency=440:sample_rate=48000:duration=${seconds}`,
    "-af", "volume='if(lt(t,2),1,if(lt(t,8.25),0.42,if(lt(t,15.5),0.78,0.25)))':eval=frame",
    "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"]);
  assert.equal(tonePCM.length, seconds * 48000 * 2);
  // FFmpeg cannot seek back to fill a RIFF header on stdout. Wrap the exact
  // mono PCM in a finite, seekable WAV before uploading to the real project.
  const wavHeader = Buffer.from("524946460000000057415645666d7420100000000100010080bb000000770100020010006461746100000000", "hex");
  wavHeader.writeUInt32LE(tonePCM.length + 36, 4);
  wavHeader.writeUInt32LE(tonePCM.length, 40);
  const tone = await upload("tone.wav", Buffer.concat([wavHeader, tonePCM]));
  const at = numerator => ({ num: String(numerator), den: String(fps) });
  function insert(trackId, clipId, asset, start, end, createTrackKind) {
    return { type: "clip.insert", payload: { trackId, clipId, assetId: asset.assetId, sourcePath: asset.path,
      timelineStart: at(start), timelineEnd: at(end), ...(createTrackKind ? { createTrackKind } : {}) } };
  }
  const commands = Array.from({ length: clipCount }, (_, i) =>
    insert("video", "still-" + i, stills[colors[i % colors.length]], i * 9, (i + 1) * 9, i === 0 ? "video" : null));
  commands.push(insert("audio", "tone", tone, 0, frames, "audio"));
  const current = await api(`projects/${projectId}`);
  await api(`projects/${projectId}/commands`, { type: "edit.batch", expectedRevision: current.revision,
    commandId: crypto.randomUUID(), actor: { kind: "human", id: "native-frozen-recovery" }, payload: { commands } });
  const directory = await api(`projects/${projectId}/directory`);
  assert.equal(directory.storageKind, "directory");
  const target = fs.mkdtempSync(path.join(directory.exportDirectory, "dense-recovery-"));
  const sourceOutput = path.join(target, "source");
  const frozenOutput = path.join(target, "frozen");
  fs.mkdirSync(sourceOutput);
  fs.mkdirSync(frozenOutput);
  const frozenEnv = { ...cpuEnv, PATH: fs.mkdtempSync(path.join(target, "empty-path-")) };
  assert.equal(fs.existsSync(path.join(path.dirname(engine), "_internal/cutvoke/rendering/render.py")), false,
    "Packaged fixture unexpectedly contains loose renderer source");

  function render(exe, prefix, environment, directory, name, expectedReuse, expectCache) {
    const output = path.join(directory, name + ".mp4");
    const result = json(exe, [...prefix, "export", "--project", projectId, "--data", dataPath, "--out", output,
      "--quality", "low", "--json"], environment, 90_000);
    assert.equal(result.ok, true);
    assert.equal(result.renderStrategy, "lossless-windows");
    assert.equal(result.windowCount, 3, "The 19.5-second fixture must exercise three lossless windows");
    assert.equal(result.reusedWindowCount, expectedReuse === "all" ? result.windowCount : 0);
    assert.equal(result.audioStrategy, "continuous-pcm");
    assert.ok(result.audioVerification && result.videoVerification);
    const cache = path.join(directory, ".cutvoke-render-cache");
    if (expectCache) assert.ok(fs.statSync(path.join(cache, "windows.sqlite3")).isFile());
    else assert.equal(fs.existsSync(cache), false, "Frozen run unexpectedly opened recovery storage");
    const rgb = media(["-i", output, "-map", "0:v:0", "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]);
    const pcm = media(["-i", output, "-map", "0:a:0", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"]);
    assert.equal(rgb.length, frames * width * height * 3);
    assert.ok(pcm.length >= (seconds - .05) * 48000 * 4 && pcm.length <= (seconds + .05) * 48000 * 4);
    for (let i = 0; i < clipCount; ++i) {
      const offset = (i * 9 + 4) * width * height * 3;
      const blue = colors[i % colors.length];
      assert.ok(rgb[offset + blue * 2] >= 200 && rgb[offset + (1 - blue) * 2] < 15,
        "Decoded fixture lost the expected color at clip " + i);
    }
    function rms(at) {
      let sum = 0;
      for (let i = at * 48000; i < at * 48000 + 4800; ++i) sum += pcm.readInt16LE(i * 4) ** 2;
      return Math.sqrt(sum / 4800);
    }
    const [loud, quiet, swell, tail] = [1, 3, 10, 17].map(rms);
    assert.ok(loud > swell && swell > quiet && quiet > tail && tail > 10,
      "Decoded audio must retain the nonuniform fixture envelope");
    return { windows: result.windowCount, reused: result.reusedWindowCount, frames, pcmBytes: pcm.length,
      videoSha256: digest(rgb), audioSha256: digest(pcm) };
  }

  assert.equal(fs.existsSync(path.join(sourceOutput, ".cutvoke-render-cache")), false);
  const cold = render(sourcePython, sourceArgs, sourceEnv, sourceOutput, "first", "none", true);
  const warm = render(sourcePython, sourceArgs, sourceEnv, sourceOutput, "second", "all", true);
  assert.deepEqual(warm, { ...cold, reused: cold.windows });
  const packagedFirst = render(engine, [], frozenEnv, frozenOutput, "first", "none", false);
  const packagedSecond = render(engine, [], frozenEnv, frozenOutput, "second", "none", false);
  assert.deepEqual(packagedSecond, packagedFirst);
  assert.deepEqual(packagedFirst, cold, "Exact bundled tools must decode the same source and frozen A/V");
  return { bundledToolsMatchSourceControl: true, source: [cold, warm], frozen: [packagedFirst, packagedSecond] };
}
module.exports = { verifyUnixFrozenRecovery };
