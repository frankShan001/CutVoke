import { expect, test } from "@playwright/test";

test.use({ permissions: ["microphone"], launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] } });

test("microphone take can be auditioned, retaken, inserted and mixed with persistent undo", async ({ page, request }) => {
  test.setTimeout(90_000);
  const id = `audio-workbench-${Date.now()}`, name = `声音工作台 ${id}`;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name } })).status()).toBe(201);
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await page.getByRole("tab", { name: "音频分析", exact: true }).click();
  const voiceover = page.locator("section").filter({ has: page.getByRole("heading", { name: "配音录制", exact: true }) });
  await voiceover.getByRole("button", { name: "选择录音设备" }).click();
  await expect(voiceover.getByLabel("麦克风").locator("option")).not.toHaveCount(1);
  await voiceover.getByLabel("随画面播放录音").uncheck();
  await voiceover.getByLabel("倒计时").selectOption("0");
  await voiceover.getByLabel("实时监听麦克风").check();
  await expect(voiceover.getByLabel("监听音量")).toBeVisible();
  await voiceover.getByRole("button", { name: "开始录音", exact: true }).click();
  await expect(voiceover.getByRole("button", { name: "停止录音" })).toBeVisible();
  await expect.poll(async () => Number.parseFloat((await voiceover.locator("output").textContent()) || "0")).toBeGreaterThan(1);
  await voiceover.getByRole("button", { name: "停止录音" }).click();
  await expect(voiceover.locator("audio")).toBeVisible();
  const decoded = await voiceover.locator("audio").evaluate(async element => {
    const context = new AudioContext();
    const buffer = await context.decodeAudioData(await (await fetch((element as HTMLAudioElement).src)).arrayBuffer());
    const samples = buffer.getChannelData(0);
    const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
    await context.close(); return { duration: buffer.duration, rms };
  });
  expect(decoded.duration).toBeGreaterThan(.8); expect(decoded.rms).toBeGreaterThan(.001);
  await voiceover.getByRole("button", { name: "重录", exact: true }).click();
  await expect.poll(async () => Number.parseFloat((await voiceover.locator("output").textContent()) || "0")).toBeGreaterThan(.6);
  await voiceover.getByRole("button", { name: "停止录音" }).click();
  await voiceover.getByRole("button", { name: "添加配音到时间线" }).click();
  await expect(voiceover.locator("audio")).toHaveCount(0);
  const project = await (await request.get(`/api/v1/projects/${id}`)).json();
  const track = project.sequence.tracks.find((entry: { kind: string }) => entry.kind === "audio");
  expect(track.clips).toHaveLength(1); expect(track.clips[0].assetRef.sourcePath).toMatch(/\.wav$/);
  const mixer = page.locator("section").filter({ has: page.getByRole("heading", { name: "混音台", exact: true }) });
  await mixer.getByLabel("混音通道").selectOption(`track:${track.id}`);
  await mixer.getByLabel("增益（dB）", { exact: true }).first().fill("-9");
  await mixer.getByRole("button", { name: "应用混音", exact: true }).click();
  await expect.poll(async () => {
    const data = await (await request.get(`/api/v1/projects/${id}`)).json();
    return data.sequence.tracks.find((entry: { id: string }) => entry.id === track.id).audioMix?.gainDb;
  }).toBe(-9);
  await page.getByRole("button", { name: /^撤销/ }).first().click();
  await expect.poll(async () => {
    const data = await (await request.get(`/api/v1/projects/${id}`)).json();
    return data.sequence.tracks.find((entry: { id: string }) => entry.id === track.id).audioMix?.gainDb ?? 0;
  }).toBe(0);

  // A deterministic 1.860-second model-output fixture must stay inside its
  // real source range. The actual Demucs engine is separately integration-tested.
  const rate = 48000, samples = rate * 1.86, wav = Buffer.alloc(44 + samples * 2);
  wav.write("RIFF", 0); wav.writeUInt32LE(36 + samples * 2, 4); wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write("data", 36); wav.writeUInt32LE(samples * 2, 40);
  for (let index = 0; index < samples; index++) wav.writeInt16LE(Math.round(Math.sin(index / rate * 2 * Math.PI * 440) * 4000), 44 + index * 2);
  const uploaded = await request.post(`/api/v1/assets?name=exact-stem-1.860.wav&projectId=${encodeURIComponent(id)}`, { data: wav, headers: { "Content-Type": "application/octet-stream" } });
  expect(uploaded.status()).toBe(201);
  const stemAsset = await uploaded.json();
  await page.route(`**/api/v1/projects/${id}/commands`, route => {
    const body = route.request().postDataJSON();
    if (body.type !== "audio.separate") return route.continue();
    return route.fulfill({ json: { commandId: body.commandId, previousRevision: "", revision: "", transactionId: "", warnings: [],
      changedEntities: [{ type: "audio_separation", change: "separated", stems: { vocals: { path: stemAsset.path, duration: 1.86 } } }] } });
  });
  const separation = page.locator("section").filter({ has: page.getByRole("heading", { name: "音源分离", exact: true }) });
  await separation.getByLabel("音频路径", { exact: true }).fill(stemAsset.path);
  await separation.getByLabel("输出文件夹").fill("exact-stem-ui-fixture");
  await separation.getByRole("button", { name: "开始分离", exact: true }).click();
  await separation.getByRole("button", { name: "添加到时间线", exact: true }).click();
  await expect.poll(async () => {
    const data = await (await request.get(`/api/v1/projects/${id}`)).json();
    const clip = data.sequence.tracks.flatMap((entry: { clips: { assetRef: { sourcePath: string }; timelineStart: { num: string; den: string }; timelineEnd: { num: string; den: string } }[] }) => entry.clips)
      .find((entry: { assetRef: { sourcePath: string } }) => entry.assetRef.sourcePath === stemAsset.path);
    return clip ? Number(clip.timelineEnd.num) / Number(clip.timelineEnd.den) - Number(clip.timelineStart.num) / Number(clip.timelineStart.den) : 0;
  }).toBeCloseTo(1.86, 6);
});

test("synchronized voiceover begins on the live transport and closes microphone tracks", async ({ page, request }) => {
  test.setTimeout(90_000);
  const id = `synced-voice-${Date.now()}`, name = `画面配音 ${id}`;
  const fixture = await (await request.get("/api/v1/projects/e2e-fixture")).json();
  const created = await (await request.post("/api/v1/projects", { data: { projectId: id, name, width: 160, height: 90 } })).json();
  const revision = created.revision || "0";
  const inserted = await request.post(`/api/v1/projects/${id}/commands`, { data: {
    type: "clip.insert", payload: { trackId: "picture", createTrackKind: "video", sourcePath: fixture.sequence.tracks[0].clips[0].assetRef.sourcePath,
      timelineStart: { num: "0", den: "1" }, timelineEnd: { num: "5", den: "1" } }, expectedRevision: revision,
    commandId: `sync-clip-${id}`, actor: { kind: "human", id: "browser-test" },
  } });
  expect(inserted.ok()).toBeTruthy();
  await page.addInitScript(() => {
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    (window as Window & { microphoneStreams?: MediaStream[] }).microphoneStreams = [];
    navigator.mediaDevices.getUserMedia = async constraints => {
      const stream = await original(constraints);
      (window as Window & { microphoneStreams?: MediaStream[] }).microphoneStreams!.push(stream);
      return stream;
    };
  });
  await page.goto("/"); await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await page.getByRole("tab", { name: "音频分析", exact: true }).click();
  const panel = page.locator("section").filter({ has: page.getByRole("heading", { name: "配音录制", exact: true }) });
  await panel.getByLabel("倒计时").selectOption("0");
  await expect(panel.getByLabel("随画面播放录音")).toBeChecked();
  await panel.getByRole("button", { name: "开始录音", exact: true }).click();
  await expect.poll(async () => Number.parseFloat((await panel.locator("output").textContent()) || "0")).toBeGreaterThan(1);
  await panel.getByRole("button", { name: "停止录音" }).click();
  await expect(panel.locator("audio")).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as Window & { microphoneStreams?: MediaStream[] }).microphoneStreams?.every(stream => stream.getTracks().every(track => track.readyState === "ended")))).toBe(true);
  const summary = await panel.locator("p").filter({ hasText: "起点" }).textContent();
  expect(summary).toMatch(/起点 0\.[0-9]+ 秒/);
  await panel.getByRole("button", { name: "添加配音到时间线" }).click();
  await expect(panel.locator("audio")).toHaveCount(0);
  const project = await (await request.get(`/api/v1/projects/${id}`)).json();
  const voice = project.sequence.tracks.find((track: { kind: string }) => track.kind === "audio").clips[0];
  expect(Number(voice.timelineStart.num) / Number(voice.timelineStart.den)).toBeLessThan(.5);
});
