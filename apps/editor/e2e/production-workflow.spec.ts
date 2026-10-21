import { expect, test, type APIRequestContext } from "@playwright/test";
import { mkdirSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

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

test("text-only project plays native title pixels and archives from the activity panel", async ({ page, request }) => {
  test.setTimeout(90_000);
  const name = `纯文字归档 ${Date.now()}`;
  const id = await create(request, name);
  await edit(request, id, "clip.insert", title());
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 45_000 });
  const video = page.locator("video.player__video");
  await expect(video).toBeVisible();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => {
    const canvas = document.createElement("canvas"); canvas.width = 320; canvas.height = 180;
    const ctx = canvas.getContext("2d")!; ctx.drawImage(element, 0, 0, 320, 180);
    const pixels = ctx.getImageData(0, 0, 320, 180).data;
    let count = 0;
    for (let index = 0; index < pixels.length; index += 4) if (pixels[index] > 160) count++;
    return count;
  })).toBeGreaterThan(100);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBeGreaterThan(.2);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
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
