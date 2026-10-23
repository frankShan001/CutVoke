import { expect, test, type APIRequestContext } from "@playwright/test";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const rat = (num: number) => ({ num: String(num), den: "1" });
const evidence = resolve("../../.artifacts/acceptance/production-fixes");

async function create(request: APIRequestContext, name: string) {
  const id = `production-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name, width: 320, height: 180, fps: 24 } })).ok()).toBeTruthy();
  return id;
}
async function edit(request: APIRequestContext, id: string, type: string, payload: object) {
  const project = await (await request.get(`/api/v1/projects/${id}`)).json();
  const response = await request.post(`/api/v1/projects/${id}/commands`, {
    data: { type, payload, expectedRevision: project.revision },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}
const title = (parent?: string) => ({ trackId: "titles", createTrackKind: "text", clipId: "title",
  timelineStart: rat(0), timelineEnd: rat(2), ...(parent ? { attachedToClipId: parent } : {}),
  text: { content: "LIGHT", fontFamily: "Barlow Condensed", fontSize: 180, bold: true, x: .5, y: .5 } });

test("text-only preview paints centered title glyphs and removes them on deletion before archiving", async ({ page, request }) => {
  test.setTimeout(90_000);
  const name = `纯文字归档 ${Date.now()}`;
  const id = await create(request, name);
  await edit(request, id, "clip.insert", title());
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 45_000 });
  const video = page.locator("video.player__video");
  await expect(video).toBeVisible();
  await expect(page.locator(".player__title-overlay text").last()).toHaveText("LIGHT");
  await expect(page.locator(".player__title-overlay text").last()).toBeVisible();
  await expect(page.locator(".player__interactive-scene")).toHaveAttribute("data-ready", "true");
  await expect(page.locator(".player__interactive-status")).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  const preview = page.locator('.player__canvas-frame[aria-label="预览画面"]');
  const pixels = async () => JSON.parse(execFileSync("python", ["-c", [
    "import io,json,sys",
    "from PIL import Image",
    "image=Image.open(io.BytesIO(sys.stdin.buffer.read())).convert('RGB')",
    "mask=image.point(lambda value: 255 if value > 160 else 0).convert('L').point(lambda value: 255 if value > 160 else 0)",
    "print(json.dumps({'count':sum(value>0 for value in mask.tobytes()),'bbox':mask.getbbox(),'size':image.size}))",
  ].join("\n")], { input: await preview.screenshot() }).toString());
  // Inspect the composed viewport: interactive title editing paints glyphs
  // in a DOM overlay above the video, so a video-only sample omits real text.
  await expect.poll(async () => (await pixels()).count).toBeGreaterThan(100);
  const painted = await pixels();
  expect(painted.count).toBeGreaterThan(100);
  const [left, top, right, bottom] = painted.bbox;
  expect((left + right) / (2 * painted.size[0])).toBeCloseTo(.5, 1);
  // The visible cap-height sits above the center of the font's line box.
  expect((top + bottom) / (2 * painted.size[1])).toBeGreaterThan(.3);
  expect((top + bottom) / (2 * painted.size[1])).toBeLessThan(.65);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(.2);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await page.locator(".clip-block--text").click();
  await page.keyboard.press("Delete");
  await expect(page.locator(".clip-block--text")).toHaveCount(0);
  await expect.poll(async () => (await pixels()).count).toBeLessThan(10);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".clip-block--text")).toHaveCount(1);
  await expect.poll(async () => (await pixels()).count).toBeGreaterThan(100);
  await page.getByRole("button", { name: "Agent活动面板" }).click();
  const saved = page.waitForResponse(response => response.url().endsWith("/agent/project_archive"));
  await page.getByRole("button", { name: "保存制作归档", exact: true }).click();
  const response = await saved;
  const result = await response.json();
  expect(response.ok(), JSON.stringify(result)).toBeTruthy();
  expect(result.ok).toBe(true);
  expect(statSync(result.path).size).toBeGreaterThan(1000);
  expect(result.eventCount).toBeGreaterThan(0);
  await expect(page.locator(".agent-panel").getByRole("status")).toContainText(result.path);
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({ path: resolve(evidence, "text-only-and-production-archive.png") });
});

test("right-click copies attached layers as one undoable edit and survives reload", async ({ page, request }) => {
  test.setTimeout(90_000);
  const name = `图层复用 ${Date.now()}`;
  const id = await create(request, name);
  const image = fileURLToPath(new URL("../../../src/cutvoke/assets/stickers/decor_sparkles.png", import.meta.url));
  await edit(request, id, "clip.insert", { trackId: "base-track", createTrackKind: "video", clipId: "base",
    sourcePath: image, timelineStart: rat(0), timelineEnd: rat(2) });
  await edit(request, id, "clip.insert", title("base"));
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 45_000 });
  await page.locator(".clip-block--video").click({ button: "right" });
  await page.getByRole("button", { name: "复制片段及绑定图层", exact: true }).click();
  const project = async () => (await (await request.get(`/api/v1/projects/${id}`)).json());
  await expect.poll(async () => (await project()).sequence.tracks.flatMap((track: { clips: unknown[] }) => track.clips).length).toBe(4);
  let current = await project();
  const base = current.sequence.tracks.find((track: { id: string }) => track.id === "base-track").clips;
  const titles = current.sequence.tracks.find((track: { id: string }) => track.id === "titles").clips;
  expect(titles[1].attachedToClipId).toBe(base[1].id);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect.poll(async () => (await project()).sequence.tracks.flatMap((track: { clips: unknown[] }) => track.clips).length).toBe(2);
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await expect.poll(async () => (await project()).sequence.tracks.flatMap((track: { clips: unknown[] }) => track.clips).length).toBe(4);
  await page.reload();
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 45_000 });
  await expect(page.locator(".clip-block--text")).toHaveCount(2);
  current = await project();
  expect(current.sequence.tracks.find((track: { id: string }) => track.id === "titles").clips[1].attachedToClipId).toBe(base[1].id);
});

test("a slow preparation can be cancelled to enter on-demand editing", async ({ page, request }) => {
  const name = `按需预览 ${Date.now()}`;
  const id = await create(request, name);
  await edit(request, id, "clip.insert", title());
  let cancellations = 0;
  await page.route(`**/api/v1/projects/${id}/preview-jobs`, route => route.fulfill({
    json: { jobId: "slow-production", state: "running", phase: "rendering", progress: 0,
      completedWindows: 0, totalWindows: 1, currentWindow: 1, windowStart: 0, windowEnd: 2,
      elapsedSeconds: 5, cached: false },
  }));
  await page.route("**/api/v1/preview-jobs/slow-production", route => route.fulfill({
    json: { jobId: "slow-production", state: "running", phase: "rendering", progress: 0,
      completedWindows: 0, totalWindows: 1, currentWindow: 1, windowStart: 0, windowEnd: 2,
      elapsedSeconds: 5, cached: false },
  }));
  await page.route("**/api/v1/preview-jobs/slow-production/cancel", route => {
    cancellations++; return route.fulfill({ json: { state: "cancelled" } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByText("正在渲染第 1 段（0–2 秒），已用 5 秒")).toBeVisible();
  await page.getByRole("button", { name: "先进入编辑，按需预览" }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await expect.poll(() => cancellations).toBeGreaterThan(0);
  await expect(page.locator("video.player__video")).toBeVisible({ timeout: 30_000 });
});

test("canvas production versions switch and OTIO download imports through the Web controls", async ({ page, request }) => {
  test.setTimeout(120_000);
  const featureEvidence = resolve("../../.artifacts/feature-completion-20261023");
  mkdirSync(featureEvidence, { recursive: true });
  const fixture = await (await request.get("/api/v1/projects/e2e-fixture")).json();
  const sourcePath = fixture.sequence.tracks[0].clips[0].assetRef.sourcePath;
  const name = `制作版本与互换 ${Date.now()}`;
  const id = await create(request, name);
  await edit(request, id, "clip.insert", { trackId: "main", createTrackKind: "video", clipId: "source",
    sourcePath, timelineStart: rat(0), timelineEnd: rat(2) });
  const project = async () => (await (await request.get(`/api/v1/projects/${id}`)).json());
  const originalId = (await project()).sequence.id;
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 45_000 });
  const tools = page.locator(".cv-production-tools");
  await tools.getByText("画布与制作版本", { exact: true }).click();
  await tools.getByLabel("版本名称", { exact: true }).fill("竖屏成片");
  await tools.getByLabel("画布宽度", { exact: true }).fill("72");
  await tools.getByLabel("画布高度", { exact: true }).fill("128");
  await tools.getByLabel("输出帧率", { exact: true }).fill("23.976");
  await tools.getByLabel("重构图方式", { exact: true }).selectOption("cover");
  await tools.getByRole("button", { name: "复制为制作版本", exact: true }).click();
  await expect.poll(async () => (await project()).sequences.length).toBe(2);
  let current = await project();
  const portraitId = current.sequence.id;
  expect(portraitId).not.toBe(originalId);
  expect([current.sequence.width, current.sequence.height, current.sequence.reframe]).toEqual([72, 128, "cover"]);
  expect(current.sequence.fps).toEqual({ num: "2997", den: "125" });
  await expect(tools.getByLabel("活动序列", { exact: true })).toHaveValue(portraitId);
  await tools.getByLabel("活动序列", { exact: true }).selectOption(originalId);
  await expect.poll(async () => (await project()).sequence.id).toBe(originalId);
  await expect(tools.getByLabel("画布宽度", { exact: true })).toHaveValue("320");
  await tools.getByLabel("活动序列", { exact: true }).selectOption(portraitId);
  await expect.poll(async () => (await project()).sequence.id).toBe(portraitId);
  await expect(tools.getByLabel("画布宽度", { exact: true })).toHaveValue("72");

  await tools.getByText("工程互换", { exact: true }).click();
  const downloadEvent = page.waitForEvent("download");
  await tools.getByRole("button", { name: "下载互换文件", exact: true }).click();
  const download = await downloadEvent;
  const saved = resolve(featureEvidence, "production-version-web.otio");
  await download.saveAs(saved);
  const exported = JSON.parse(readFileSync(saved, "utf8"));
  expect(exported.OTIO_SCHEMA).toBe("Timeline.1");
  expect(exported.metadata.cutvoke).toMatchObject({ width: 72, height: 128, reframe: "cover" });
  await tools.getByLabel("互换文件内容", { exact: true }).fill("");
  await tools.getByLabel("选择互换文件", { exact: true }).setInputFiles(saved);
  await expect(tools.getByLabel("互换文件内容", { exact: true })).toHaveValue(/Timeline\.1/);
  await tools.getByRole("button", { name: "导入为新序列", exact: true }).click();
  await expect.poll(async () => (await project()).sequences.length).toBe(3);
  current = await project();
  expect([current.sequence.width, current.sequence.height]).toEqual([72, 128]);
  expect(current.sequence.tracks[0].clips[0].assetRef.sourcePath).toBe(sourcePath);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect.poll(async () => (await project()).sequences.length).toBe(2);
  await page.getByRole("button", { name: "重做", exact: true }).click();
  await expect.poll(async () => (await project()).sequences.length).toBe(3);
  await page.screenshot({ path: resolve(featureEvidence, "production-versions-interchange.png") });
  await page.reload();
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 45_000 });
  current = await project();
  expect(current.sequences).toHaveLength(3);
  expect([current.sequence.width, current.sequence.height]).toEqual([72, 128]);
  writeFileSync(resolve(featureEvidence, "production-versions-web.json"), JSON.stringify({
    verifiedAt: new Date().toISOString(), projectId: id, originalId, portraitId,
    sequenceCountAfterReload: current.sequences.length, activeCanvas: { width: current.sequence.width, height: current.sequence.height,
      fps: current.sequence.fps, reframe: current.sequence.reframe },
    checks: ["Web duplicate with true canvas and fps", "Web switch between production versions", "Web OTIO download",
      "Web local file import", "import undo and redo", "reload persists imported sequence"],
    artifacts: [saved, resolve(featureEvidence, "production-versions-interchange.png")],
  }, null, 2) + "\n");
});
