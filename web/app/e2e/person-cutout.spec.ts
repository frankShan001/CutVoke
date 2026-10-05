import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

const apiPath = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;

async function readProject(request: APIRequestContext, id: string) {
  const response = await request.get(apiPath(id));
  expect(response.ok(), await response.text()).toBeTruthy();
  return response.json() as Promise<{
    projectId: string;
    revision: string;
    sequence: { tracks: Array<{ kind: string; clips: Array<{ assetRef: { sourcePath: string } }> }> };
  }>;
}

async function applyCommand(request: APIRequestContext, id: string, type: string, payload: object) {
  const project = await readProject(request, id);
  const response = await request.post(`${apiPath(id)}/commands`, {
    data: {
      type,
      payload,
      expectedRevision: project.revision,
      commandId: `person-cutout-e2e-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      actor: { kind: "human", id: "person-cutout-e2e" },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
}

async function createProject(request: APIRequestContext) {
  const fixtureResponse = await request.get(apiPath("e2e-fixture"));
  expect(fixtureResponse.ok(), await fixtureResponse.text()).toBeTruthy();
  const fixture = await fixtureResponse.json() as Awaited<ReturnType<typeof readProject>>;
  const source = fixture.sequence.tracks.find((track) => track.kind === "video")
    ?.clips[0]?.assetRef.sourcePath;
  expect(source).toBeTruthy();

  const id = `person-cutout-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `人物抠像验收 ${id.slice(-5)}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 640, height: 360, fps: 30 },
  });
  expect(created.status(), await created.text()).toBe(201);
  await applyCommand(request, id, "track.add", { trackId: "video-cutout", kind: "video" });
  await applyCommand(request, id, "clip.insert", {
    trackId: "video-cutout",
    clipId: "cutout-source",
    sourcePath: source,
    timelineStart: { num: "0", den: "1" },
    timelineEnd: { num: "4", den: "1" },
  });
  return { id, name };
}

test("person cutout panel submits, tracks, applies, and reports the undo point", async ({ page, request }) => {
  const project = await createProject(request);
  let startPayload: unknown;
  let applyCalled = false;
  let pollCount = 0;

  await page.route("**/api/v1/person-cutout/status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ installed: true, modelReady: true, interactiveModelReady: true, ready: true,
      message: "本机人物分割已就绪（MediaPipe Selfie Segmentation，Apache-2.0）；视频只在本机处理。" }),
  }));
  await page.route("**/api/v1/person-cutout-jobs/job-person-e2e", (route) => {
    pollCount += 1;
    const completed = {
      jobId: "job-person-e2e",
      projectId: project.id,
      clipId: "cutout-source",
      sourceSignature: "fixture-signature",
      status: "completed",
      phase: "透明片段已生成",
      progress: 100,
      error: "",
      selectionMode: "selected_person",
      trackingMode: "magic_touch",
      selectionPoint: [0.5, 0.5],
      selectionAtSeconds: 0,
      result: {
        outputPath: "local-only",
        frameCount: 120,
        fps: "30/1",
        durationSeconds: 4,
        audioPreserved: true,
        videoCodec: "prores_ks",
        pixelFormat: "yuva444p12le",
        alpha: true,
        selectedFrames: 115,
        lostFrames: 5,
        maxSeparatedComponents: 2,
      },
    };
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(completed) });
  });
  await page.route("**/api/v1/projects/*/person-cutout-jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    startPayload = route.request().postDataJSON();
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({
        jobId: "job-person-e2e", projectId: project.id, clipId: "cutout-source",
        sourceSignature: "fixture-signature", status: "queued", phase: "排队中",
        progress: 0, error: "", result: null,
      }),
    });
  });
  await page.route("**/api/v1/projects/*/person-cutout-jobs/job-person-e2e/apply", async (route) => {
    applyCalled = true;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        job: {
          jobId: "job-person-e2e", projectId: project.id, clipId: "cutout-source",
          sourceSignature: "fixture-signature", status: "applied", phase: "已应用；可撤销恢复原片",
          progress: 100, error: "", result: null,
        },
        command: {
          commandId: "cutout-apply", previousRevision: "1", revision: "2",
          transactionId: "cutout-tx", changedEntities: [], warnings: [],
        },
      }),
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${project.name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await page.locator(".clip-block").first().click();
  await page.getByRole("tab", { name: "效果条" }).click();
  await expect(page.getByRole("heading", { name: "人物抠像" })).toBeVisible();
  await expect(page.getByText("本机人物分割已就绪（MediaPipe Selfie Segmentation，Apache-2.0）；视频只在本机处理。")).toBeVisible();

  await page.getByRole("radio", { name: "只保留我点选的人物" }).check();
  await page.getByRole("combobox", { name: "人物抠像跟踪方式" })
    .selectOption("magic_touch");
  const picker = page.getByRole("button", { name: "点选要保留的人物" });
  const preview = picker.locator("img");
  await expect(preview).toBeVisible();
  await expect.poll(() => preview.evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true);
  const pickerBox = await picker.boundingBox();
  expect(pickerBox).not.toBeNull();
  await picker.click({ position: { x: pickerBox!.width / 2, y: pickerBox!.height / 2 } });
  await expect(page.getByText(/MagicTouch 每帧重新分割可见区域/)).toBeVisible();

  await page.getByRole("button", { name: "跟踪并抠出所选人物" }).click();
  await expect.poll(() => startPayload).toMatchObject({
    clipId: "cutout-source", edgeSoftness: 1.2, selectionMode: "selected",
    trackingMode: "magic_touch",
    selectionPoint: { x: expect.any(Number), y: expect.any(Number) }, selectionAtSeconds: 0,
  });
  const submitted = startPayload as { selectionPoint: { x: number; y: number } };
  expect(submitted.selectionPoint.x).toBeGreaterThan(0.45);
  expect(submitted.selectionPoint.x).toBeLessThan(0.55);
  expect(submitted.selectionPoint.y).toBeGreaterThan(0.45);
  expect(submitted.selectionPoint.y).toBeLessThan(0.55);
  await expect(page.getByText("透明片段已生成")).toBeVisible({ timeout: 5000 });
  await expect(page.getByText("120 帧 · 4.0 秒 · 已保留原音频 · MagicTouch 点提示跟踪 · 跟踪 115/120 帧 · 丢失 5 帧")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("跟踪丢失 5 帧，输出对应区间可能透明断开");
  await expect(page.getByRole("alert")).toContainText("建议先检查结果画面，再应用到片段");

  await page.getByRole("button", { name: "应用到片段" }).click();
  await expect.poll(() => applyCalled).toBe(true);
  await expect(page.getByText("已替换片段素材，撤销可恢复原片。")).toBeVisible();
  expect(pollCount).toBeGreaterThan(0);

  const output = resolve(process.cwd(), "../../output/acceptance/jy-r20-person-20260925");
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: resolve(output, "web-person-cutout-panel.png"), fullPage: true });
});

test("SAM2 picker sends identity and exclusion prompts", async ({ page, request }) => {
  const project = await createProject(request);
  let startPayload: unknown;

  await page.route("**/api/v1/person-cutout/status", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ installed: true, modelReady: true, interactiveModelReady: true,
      mediapipeReady: true, sam2RuntimeReady: true, sam2ModelReady: true,
      sam2Ready: true, ready: true, message: "本机人物分割已就绪。" }),
  }));
  await page.route("**/api/v1/projects/*/person-cutout-jobs", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    startPayload = route.request().postDataJSON();
    return route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ jobId: "job-person-sam2-e2e", projectId: project.id,
        clipId: "cutout-source", sourceSignature: "fixture-signature",
        status: "queued", phase: "排队中", progress: 0, error: "", result: null }),
    });
  });
  await page.route("**/api/v1/person-cutout-jobs/job-person-sam2-e2e", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ jobId: "job-person-sam2-e2e", projectId: project.id,
      clipId: "cutout-source", sourceSignature: "fixture-signature",
      status: "queued", phase: "排队中", progress: 0, error: "", result: null }),
  }));

  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${project.name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await page.locator(".clip-block").first().click();
  await page.getByRole("tab", { name: "效果条" }).click();
  await page.getByRole("radio", { name: "只保留我点选的人物" }).check();
  await page.getByRole("combobox", { name: "人物抠像跟踪方式" }).selectOption("sam2");
  const promptTime = page.getByRole("spinbutton", { name: "SAM2 提示帧源素材时间" });
  await promptTime.fill("2");
  const picker = page.getByRole("button", { name: "点选要保留的人物" });
  await expect.poll(() => picker.locator("img").evaluate((image: HTMLImageElement) =>
    image.complete && image.naturalWidth > 0)).toBe(true);
  const box = await picker.boundingBox();
  expect(box).not.toBeNull();
  await picker.click({ position: { x: box!.width * 0.4, y: box!.height * 0.5 } });
  await expect(page.locator(".person-cutout__target-mark")).toHaveCount(1);
  await picker.click({ position: { x: box!.width * 0.62, y: box!.height * 0.5 }, modifiers: ["Shift"] });
  await expect(page.locator(".person-cutout__target-mark")).toHaveCount(2);
  await expect(page.locator(".person-cutout__target-mark--exclude")).toHaveCount(1);
  await page.getByRole("button", { name: "保存此帧提示" }).click();
  await expect(page.getByText("2.00 秒 · 2 点")).toBeVisible();
  await promptTime.fill("3");
  await expect.poll(() => picker.locator("img").evaluate((image: HTMLImageElement) =>
    image.complete && image.naturalWidth > 0)).toBe(true);
  const laterBox = await picker.boundingBox();
  expect(laterBox).not.toBeNull();
  await picker.click({ position: { x: laterBox!.width * 0.32, y: laterBox!.height * 0.47 } });
  await page.getByRole("button", { name: "保存此帧提示" }).click();
  await expect(page.getByText("3.00 秒 · 1 点")).toBeVisible();
  await page.getByRole("button", { name: "跟踪并抠出所选人物" }).click();
  await expect.poll(() => startPayload).toMatchObject({
    selectionMode: "selected", trackingMode: "sam2",
    selectionPoint: { x: expect.any(Number), y: expect.any(Number) },
    selectionPrompts: [
      { atSeconds: 2, points: [
        { x: expect.any(Number), y: expect.any(Number), label: 1 },
        { x: expect.any(Number), y: expect.any(Number), label: 0 },
      ] },
      { atSeconds: 3, points: [
        { x: expect.any(Number), y: expect.any(Number), label: 1 },
      ] },
    ],
  });
});
