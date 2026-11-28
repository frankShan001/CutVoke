import { projectOutputPath } from "./projectDirectoryHelpers";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { copyFileSync, mkdirSync, readFileSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import { basename, resolve } from "node:path";

const image = fileURLToPath(new URL("../../../src/cutvoke/assets/stickers/decor_sparkles.png", import.meta.url));
const audio = fileURLToPath(new URL("../../../src/cutvoke/assets/audio/tech_minimal.wav", import.meta.url));
const evidence = resolve(process.cwd(), "../../.artifacts/acceptance/product-completion-20261003/web");
const api = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;

async function current(request: APIRequestContext, id: string) {
  const response = await request.get(api(id));
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function command(request: APIRequestContext, id: string, type: string, payload: object) {
  const project = await current(request, id);
  const response = await request.post(`${api(id)}/commands`, {
    data: { type, payload, expectedRevision: project.revision,
      commandId: crypto.randomUUID(), actor: { kind: "human", id: "product-browser-test" } },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function projectWithImages(request: APIRequestContext, count = 1) {
  const id = `product-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `产品流程 ${id}`;
  const response = await request.post("/api/v1/projects", { data: { projectId: id, name, width: 320, height: 180, fps: 30 } });
  expect(response.status()).toBe(201);
  await command(request, id, "track.add", { trackId: "video", kind: "video" });
  const assets: Array<{ assetId: string; path: string; name: string }> = [];
  for (let index = 0; index < count; index++) {
    const filename = `现场照片-${id}-${index}.png`;
    const upload = await request.post(`/api/v1/assets?name=${encodeURIComponent(filename)}&projectId=${encodeURIComponent(id)}`, {
      headers: { "Content-Type": "application/octet-stream" }, data: readFileSync(image),
    });
    expect(upload.ok()).toBeTruthy();
    const asset = await upload.json();
    assets.push({ ...asset, name: filename });
    await command(request, id, "clip.insert", {
      trackId: "video", clipId: `image-${index}`, sourcePath: asset.path, assetId: asset.assetId,
      timelineStart: { num: index * 2, den: 1 }, timelineEnd: { num: (index + 1) * 2, den: 1 },
    });
  }
  return { id, name, assets };
}

async function open(page: Page, name: string, repair = false, preparationTimeout = 75_000) {
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  if (repair) {
    const repairButton = page.getByRole("button", { name: "进入工程修复" });
    const timeline = page.getByRole("heading", { name: "时间线" });
    // A valid first frame can enter editing before an unrelated missing clip
    // fails background preparation. Only click repair when loading needs it.
    await expect.poll(async () => await repairButton.isVisible() || await timeline.isVisible(),
      { timeout: preparationTimeout }).toBe(true);
    if (await repairButton.isVisible()) await repairButton.click();
  }
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: preparationTimeout });
}

async function exportDialog(page: Page, request: APIRequestContext, id: string) {
  await page.getByRole("button", { name: "导出", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  mkdirSync(resolve(evidence, "exports"), { recursive: true });
  const path = await projectOutputPath(request, id, `${id}.mp4`);
  await dialog.getByLabel("输出文件路径").fill(path);
  await dialog.getByLabel("画质").selectOption("low");
  return { dialog, path };
}

async function removeQaAsset(path: string) {
  // Only the disposable E2E server's media tree may be changed by this test.
  expect(path.toLowerCase()).toContain("cutvoke-e2e-");
  await expect.poll(() => {
    try { unlinkSync(path); return true; }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return true;
      if ((error as NodeJS.ErrnoException).code === "EBUSY") return false;
      throw error;
    }
  }, { timeout: 15_000, intervals: [100, 300, 1000] }).toBe(true);
}

async function restore(request: APIRequestContext, asset: { assetId: string }, id: string) {
  await request.post(`/api/v1/assets/${encodeURIComponent(asset.assetId)}/relink?name=restored.png&projectId=${encodeURIComponent(id)}`, {
    headers: { "Content-Type": "application/octet-stream" }, data: readFileSync(image),
  });
}

test("original media names survive ledger hydration and audio import updates the open analysis panel", async ({ page, request }) => {
  const { id, name, assets } = await projectWithImages(request);
  await open(page, name);
  const clip = page.locator(".clip-block").first();
  await expect(clip).toContainText(assets[0].name);
  await clip.click();
  await expect(page.getByRole("tabpanel", { name: "片段属性" })).toContainText(assets[0].name);
  await page.reload();
  await open(page, name);
  await expect(page.locator(".clip-block").first()).toContainText(assets[0].name);

  await page.getByRole("tab", { name: "音频分析", exact: true }).click();
  const audioName = `新导入音轨-${id}.wav`;
  await expect(page.getByLabel("选择音频源").locator("option").filter({ hasText: audioName })).toHaveCount(0);
  const revision = (await current(request, id)).revision;
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"), page.getByRole("button", { name: "导入素材", exact: true }).click(),
  ]);
  await chooser.setFiles({ name: audioName, mimeType: "audio/wav", buffer: readFileSync(audio) });
  await expect(page.getByLabel("选择音频源").locator("option").filter({ hasText: audioName })).toHaveCount(1);
  expect((await current(request, id)).revision).toBe(revision);
});

test("export preflight blocks missing media, locates the relink control, and exports after recovery", async ({ page, request }) => {
  test.setTimeout(90_000);
  const { id, name, assets } = await projectWithImages(request);
  await open(page, name);
  await expect(page.locator(".clip-block").first()).toContainText(assets[0].name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效", exact: true }).click();
  await page.getByRole("button", { name: "隐藏素材面板" }).click();
  await removeQaAsset(assets[0].path);
  try {
    const revision = (await current(request, id)).revision;
    const { dialog, path } = await exportDialog(page, request, id);
    await dialog.getByRole("button", { name: "开始导出" }).click();
    await expect(dialog.getByText("导出前检查未通过", { exact: true })).toBeVisible();
    await expect(dialog).toContainText(assets[0].name);
    expect((await current(request, id)).revision).toBe(revision);
    const jobs = await request.get(`${api(id)}/exports`);
    expect((await jobs.json()).jobs).toHaveLength(0);
    await page.screenshot({ path: resolve(evidence, "05-missing-media-preflight.png") });

    await dialog.getByRole("button", { name: "在素材库修复" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: "隐藏素材面板" })).toBeVisible();
    await expect(page.getByLabel("搜索素材")).toHaveValue(assets[0].name);
    await expect(page.locator(".clip-block--selected")).toContainText(assets[0].name);
    const [chooser] = await Promise.all([
      page.waitForEvent("filechooser"), page.getByRole("button", { name: `重新链接 ${assets[0].name}` }).click(),
    ]);
    await chooser.setFiles(image);
    await expect(page.getByRole("contentinfo")).toContainText("已重新链接");
    await expect(page.getByLabel("搜索素材")).toHaveValue("decor_sparkles.png");
    // Other projects in this shared QA session can import the same original filename.
    const relinkedItem = page.locator(`.media-item[title*="${assets[0].assetId}"]`);
    await expect(relinkedItem).toBeVisible();
    await expect(relinkedItem).toContainText("decor_sparkles.png");
    const reopened = await exportDialog(page, request, id);
    await reopened.dialog.getByRole("button", { name: "开始导出" }).click();
    await expect(reopened.dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 75_000 });
    expect(statSync(path).size).toBeGreaterThan(0);
    copyFileSync(path, resolve(evidence, "exports", basename(path)));
    const panel = await reopened.dialog.locator(".export-dialog__panel").boundingBox();
    expect(panel!.y).toBeGreaterThanOrEqual(0);
    expect(panel!.y + panel!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
    await page.screenshot({ path: resolve(evidence, "06-recovered-export.png") });
  } finally {
    await restore(request, assets[0], id);
  }
});

test("selected export passes its range to preflight and keeps out-of-range missing media as a warning", async ({ page, request }) => {
  test.setTimeout(90_000);
  const { id, name, assets } = await projectWithImages(request, 2);
  await removeQaAsset(assets[1].path);
  try {
    await open(page, name, true);
    const { dialog, path } = await exportDialog(page, request, id);
    await dialog.getByLabel("仅导出时间范围").check();
    await dialog.getByLabel("导出开始时间").fill("0");
    await dialog.getByLabel("导出结束时间").fill("1");
    const preflight = page.waitForRequest((request) => request.url().endsWith("/commands") &&
      request.postDataJSON()?.type === "project.preflight");
    await dialog.getByRole("button", { name: "开始导出" }).click();
    expect((await preflight).postDataJSON().payload).toEqual({ range: { start: 0, end: 1 } });
    await expect(dialog.getByText("导出前提醒", { exact: true })).toBeVisible();
    await expect(dialog.getByText("导出前检查未通过", { exact: true })).toHaveCount(0);
    await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 75_000 });
    expect(statSync(path).size).toBeGreaterThan(0);
    copyFileSync(path, resolve(evidence, "exports", basename(path)));
    await expect(dialog.locator(".inspector__row").filter({ hasText: "时长" })).toContainText("1.00 秒");
  } finally {
    await restore(request, assets[1], id);
  }
});

test("selected clip prefills the mixed timeline range without queuing", async ({ page, request }) => {
  test.setTimeout(90_000);
  const { id, name } = await projectWithImages(request);
  const sampleRate = 48_000;
  const samples = sampleRate * 4;
  const tone = Buffer.alloc(44 + samples * 2); // mono PCM16 WAV, including the nonzero source offset
  tone.write("RIFF", 0);
  tone.writeUInt32LE(tone.length - 8, 4);
  tone.write("WAVEfmt ", 8);
  tone.writeUInt32LE(16, 16);
  tone.writeUInt16LE(1, 20);
  tone.writeUInt16LE(1, 22);
  tone.writeUInt32LE(sampleRate, 24);
  tone.writeUInt32LE(sampleRate * 2, 28);
  tone.writeUInt16LE(2, 32);
  tone.writeUInt16LE(16, 34);
  tone.write("data", 36);
  tone.writeUInt32LE(samples * 2, 40);
  for (let i = 0; i < samples; i++) tone.writeInt16LE(Math.round(4096 * Math.sin(2 * Math.PI * 440 * i / sampleRate)), 44 + i * 2);
  const audioAsset = await request.post(`/api/v1/assets?name=range-tone.wav&projectId=${encodeURIComponent(id)}`, {
    headers: { "Content-Type": "application/octet-stream" }, data: tone,
  });
  expect(audioAsset.ok()).toBeTruthy();
  const { path: sourcePath, assetId } = await audioAsset.json();
  await command(request, id, "track.add", { trackId: "sound", kind: "audio" });
  await command(request, id, "clip.insert", {
    trackId: "sound", clipId: "trimmed-sound", sourcePath, assetId,
    timelineStart: { num: 1, den: 2 }, timelineEnd: { num: 3, den: 2 },
    sourceStart: { num: 2, den: 1 },
  });
  await open(page, name);
  const selected = page.locator('.clip-block[data-clip-id="trimmed-sound"]');
  await selected.click();
  await page.locator('.clip-block[data-clip-id="image-0"]').click({ modifiers: ["Control"] });
  await expect(page.locator('.clip-block[aria-pressed="true"]')).toHaveCount(2);
  const { dialog, path } = await exportDialog(page, request, id);
  const preset = dialog.getByRole("button", { name: "使用所选片段的时间范围" });
  await expect(dialog.getByRole("button", { name: "开始导出" })).toBeEnabled();
  await expect(preset).toBeDisabled();
  await dialog.getByRole("button", { name: "关闭导出" }).click();
  await selected.click();
  await expect(page.locator('.clip-block[aria-pressed="true"]')).toHaveCount(1);
  await exportDialog(page, request, id);
  await preset.click();
  await expect(dialog.getByLabel("导出开始时间")).toHaveValue("0.5");
  await expect(dialog.getByLabel("导出结束时间")).toHaveValue("1.5");
  await dialog.getByLabel("导出结束时间").fill("1.25");
  await expect(dialog.getByLabel("导出开始时间")).toHaveValue("0.5");
  await expect(dialog.getByLabel("导出结束时间")).toHaveValue("1.25");
  await preset.click();
  await expect(dialog.getByLabel("导出结束时间")).toHaveValue("1.5");
  await expect(dialog.getByText("仅使用片段的时间范围，保留该时段的全部可见图层和可听音轨。")).toBeVisible();
  expect((await (await request.get(`${api(id)}/exports`)).json()).jobs).toEqual([]);

  const commandRequest = (type: string) => page.waitForRequest(req =>
    req.method() === "POST" && req.url().endsWith(`${api(id)}/commands`) && req.postDataJSON()?.type === type);
  const preflight = commandRequest("project.preflight");
  const queued = commandRequest("export.enqueue");
  await dialog.getByRole("button", { name: "开始导出" }).click();
  const before = (await preflight).postDataJSON();
  const receipt = (await queued).postDataJSON();
  expect(before.payload.range).toEqual({ start: 0.5, end: 1.5 });
  expect(receipt.payload.range).toEqual(before.payload.range);
  expect(receipt.expectedRevision).toBe(before.expectedRevision);
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 75_000 });
  await expect(dialog.locator(".inspector__row").filter({ hasText: "时长" })).toContainText("1.00 秒");
  const ffmpeg = (options: string[]) => execFileSync("ffmpeg", ["-v", "error", "-i", path, ...options, "-"],
    { timeout: 10_000, maxBuffer: 2 * 1024 * 1024 });
  const picture = ffmpeg(["-frames:v", "1", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo"]);
  expect(picture.some(level => level > 32)).toBe(true); // retains the other track's picture
  const waveform = ffmpeg(["-vn", "-t", "1", "-f", "s16le"]);
  let peak = 0;
  for (let i = 0; i + 1 < waveform.length; i += 2) peak = Math.max(peak, Math.abs(waveform.readInt16LE(i)));
  expect(peak).toBeGreaterThan(16);
});

test("preflight request failure keeps export settings and allows an explicit retry", async ({ page, request }) => {
  test.setTimeout(90_000);
  const { id, name } = await projectWithImages(request);
  await open(page, name);
  const { dialog, path } = await exportDialog(page, request, id);
  let failed = false;
  await page.route(`**${api(id)}/commands`, async (route) => {
    if (route.request().postDataJSON()?.type === "project.preflight" && !failed) {
      failed = true;
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({
        error: { code: "TEMP_UNAVAILABLE", message: "检查服务暂不可用", retryable: true },
      }) });
      return;
    }
    await route.continue();
  });
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog).toContainText("检查服务暂不可用");
  await expect(dialog.getByLabel("输出文件路径")).toHaveValue(path);
  expect((await (await request.get(`${api(id)}/exports`)).json()).jobs).toHaveLength(0);
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 75_000 });
});

test("an editor chunk load failure leaves a recovery action and the saved project reopens", async ({ page, request }) => {
  const { name } = await projectWithImages(request);
  await page.route("**/assets/Workspace-*.js", (route) => route.abort("failed"));
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByText("编辑器加载失败", { exact: true })).toBeVisible();
  await page.unroute("**/assets/Workspace-*.js");
  await page.getByRole("button", { name: "重新加载编辑器" }).click();
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await expect(page.locator(".clip-block")).toHaveCount(1);
});

test("a 200-asset project keeps the media list bounded and filtering responsive during playback", async ({ page, request }, testInfo) => {
  test.setTimeout(180_000);
  const { id, name } = await projectWithImages(request, 200);
  let preparationJobId: string | undefined;
  const preparations: Array<{ jobId: string; projectId: string; state: string; phase: string; error?: string; duration: number; completedWindows: number; totalWindows: number }> = [];
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname;
    const submission = pathname === `${api(id)}/preview-jobs` && response.request().method() === "POST";
    if ((submission || /\/preview-jobs\/[^/]+$/.test(pathname)) && response.ok()) {
      void response.json().then((job) => {
        if (job.projectId !== id) return;
        if (submission) preparationJobId ??= job.jobId;
        if (job.jobId === preparationJobId) preparations.push(job);
      }).catch(() => {});
    }
  });
  const started = Date.now();
  // This is a real 400-second composition. The first frame opens the editor;
  // the full preparation must keep advancing without blocking media browsing.
  let lastProgress = "";
  let advancedAt = Date.now();
  let watchdog: ReturnType<typeof setInterval> | undefined;
  const progressing = new Promise<never>((_resolve, reject) => {
    watchdog = setInterval(() => {
      const job = preparations.at(-1);
      const progress = job ? `${job.state}:${job.phase}:${job.completedWindows}` : "";
      if (progress !== lastProgress) { lastProgress = progress; advancedAt = Date.now(); }
      if (job?.state === "failed" || job?.state === "cancelled") {
        reject(new Error(`Complete preview failed: ${job.error || job.state}`));
      } else if (Date.now() - advancedAt > 30_000) {
        reject(new Error(`Complete preview made no progress for 30 seconds: ${lastProgress}`));
      }
    }, 1000);
  });
  try {
    await Promise.race([open(page, name, false, 120_000), progressing]);
  } finally {
    clearInterval(watchdog);
  }
  await expect.poll(() => preparations.some((job) => job.completedWindows > 0),
    { timeout: 30_000 }).toBe(true);
  const prepared = preparations.at(-1)!;
  expect(prepared.duration).toBe(400);
  expect(prepared.totalWindows).toBeGreaterThanOrEqual(Math.ceil(prepared.duration / 8));
  expect(prepared.completedWindows).toBeLessThanOrEqual(prepared.totalWindows);
  expect(prepared.completedWindows).toBeGreaterThan(0);
  await expect(page.getByRole("contentinfo")).toContainText("片段 200");
  expect((await current(request, id)).sequence.tracks[0].clips).toHaveLength(200);
  const openedMs = Date.now() - started;
  const renderedMediaCount = await page.locator(".media-item").count();
  expect(renderedMediaCount).toBeGreaterThan(0);
  expect(renderedMediaCount).toBeLessThan(30);
  const video = page.locator("video.player__video");
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).readyState),
    { timeout: 30_000 }).toBeGreaterThan(1);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime)).toBeGreaterThan(0.2);
  const before = await video.evaluate((element) => (element as HTMLVideoElement).currentTime);
  const filtered = Date.now();
  await page.getByLabel("搜索素材").fill(`现场照片-${id}-199.png`);
  await expect(page.locator(".media-item")).toHaveCount(1);
  const filteredMs = Date.now() - filtered;
  await expect(page.locator(".media-item")).toContainText(`现场照片-${id}-199.png`);
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime)).toBeGreaterThan(before + 0.2);
  await page.getByLabel("搜索素材").press("Backspace");
  expect((await current(request, id)).sequence.tracks[0].clips).toHaveLength(200);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  const observation = { assetCount: 200, clipCount: 200, openedMs, filteredMs, renderedMediaCount };
  writeFileSync(resolve(evidence, "large-project-ui-summary.json"), JSON.stringify(observation, null, 2));
  await testInfo.attach("large-project-ui-observation", {
    body: JSON.stringify(observation, null, 2),
    contentType: "application/json",
  });
  await page.screenshot({ path: resolve(evidence, "08-large-project-filter.png") });
  if (preparationJobId) await request.post(`/api/v1/preview-jobs/${preparationJobId}/cancel`, { data: {} });
});

test("replaying after the end keeps captions and the shared timeline clock synchronized", async ({ page, request }) => {
  const { id, name } = await projectWithImages(request);
  await command(request, id, "caption.add", { captionId: "replay-caption", text: "复播字幕保持可见",
    start: { num: 1, den: 10 }, end: { num: 19, den: 10 } });
  await open(page, name);
  const video = page.locator("video.player__video");
  const ruler = page.getByRole("slider", { name: "时间线播放头" });
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).readyState)).toBeGreaterThan(1);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect(page.locator(".caption-overlay__text")).toContainText("复播字幕保持可见");
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeVisible({ timeout: 8_000 });
  await expect(ruler).toHaveAttribute("aria-valuenow", "2");
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).currentTime)).toBeGreaterThan(0.3);
  await expect(page.locator(".caption-overlay__text")).toContainText("复播字幕保持可见");
  const time = Number(await ruler.getAttribute("aria-valuenow"));
  expect(time).toBeGreaterThan(0.2);
  expect(Math.abs(time - await video.evaluate((element) => (element as HTMLVideoElement).currentTime))).toBeLessThan(0.2);
});

test("caption font selection renders the bundled face and survives undo, redo, reopen and export", async ({ page, request }) => {
  test.setTimeout(120_000);
  const { id, name } = await projectWithImages(request);
  await command(request, id, "caption.add", { captionId: "font-caption", text: "字幕字体可以继续编辑",
    start: { num: 0, den: 1 }, end: { num: 2, den: 1 }, fontSize: 60 });
  const selectCaption = async () => {
    await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕", exact: true }).click();
    await page.getByRole("button", { name: "选择字幕 font-caption", exact: true }).click();
  };
  const storedFamily = async () => (await current(request, id)).sequence.captions[0].fontFamily;
  await open(page, name);
  await selectCaption();
  await page.getByLabel("字幕字体", { exact: true }).selectOption("Noto Serif SC");
  await expect.poll(storedFamily).toBe("Noto Serif SC");
  await expect(page.locator(".caption-overlay__text")).toHaveCSS("font-family", '"Noto Serif SC"');
  await expect.poll(() => page.evaluate(() => [...document.fonts]
    .some((face) => face.family === "Noto Serif SC" && face.status === "loaded"))).toBe(true);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect.poll(storedFamily).toBe("Noto Sans SC");
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await expect.poll(storedFamily).toBe("Noto Serif SC");
  await page.getByRole("button", { name: "字幕加粗", exact: true }).click();
  await expect.poll(async () => (await current(request, id)).sequence.captions[0].bold).toBe(true);
  await expect(page.locator(".caption-overlay__text")).toHaveCSS("font-weight", "700");
  await page.reload();
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await selectCaption();
  await expect(page.getByLabel("字幕字体", { exact: true })).toHaveValue("Noto Serif SC");
  await expect(page.getByRole("button", { name: "字幕加粗", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.screenshot({ path: resolve(evidence, "13-caption-font-reopened.png") });
  const { dialog, path } = await exportDialog(page, request, id);
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 75_000 });
  expect(statSync(path).size).toBeGreaterThan(0);
    copyFileSync(path, resolve(evidence, "exports", basename(path)));
});
test("export share copy is selectable and dismissal survives reopening when permissions are denied", async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true,
      value: { writeText: async () => { throw new DOMException("denied", "NotAllowedError"); } } });
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === "cutvoke.sharePrompt.dismissed") throw new DOMException("denied", "SecurityError");
      return set.call(this, key, value);
    };
  });
  const { id, name } = await projectWithImages(request);
  await open(page, name);
  const { dialog, path } = await exportDialog(page, request, id);
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 80_000 });
  const copy = dialog.getByRole("button", { name: "复制分享文案" });
  await expect(copy).toBeVisible();
  await copy.click();
  const field = dialog.getByRole("textbox", { name: "复制分享文案" });
  await expect(field).toHaveValue(/https:\/\/github\.com\/frankShan001\/CutVoke @shansjl$/);
  await expect(field).toHaveAttribute("readonly", "");
  expect(await field.evaluate((el: HTMLTextAreaElement) => {
    el.select(); return el.value.slice(el.selectionStart, el.selectionEnd);
  })).toContain("https://github.com/frankShan001/CutVoke");
  await dialog.getByRole("button", { name: "不再显示" }).click();
  await expect(copy).toHaveCount(0);
  await expect(field).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "导出", exact: true }).click();
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible();
  await expect(copy).toHaveCount(0);
  execFileSync("ffmpeg", ["-v", "error", "-i", path, "-f", "null", "-"]);
});
