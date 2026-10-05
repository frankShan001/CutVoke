import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, statSync, unlinkSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { getBuiltinPackVersion } from "./resourcePackHelpers";

type Project = {
  projectId: string;
  revision: string;
  sequence: {
    tracks: Array<{ id: string; clips: Array<{
      id: string;
      assetRef: {
        sourcePath: string;
        resourceRef?: {
          resourceId: string; packId: string; packVersion: string;
          resourceVersion: string; sha256: string;
        };
      };
      timelineStart: { num: string; den: string };
      timelineEnd: { num: string; den: string };
      speed?: { num: string; den: string };
      speedCurve?: {
        sourceDuration: { num: string; den: string };
        points: Array<{ at: { num: string; den: string }; speed: { num: string; den: string } }>;
      } | null;
      attachedToClipId?: string | null;
      effects: Array<{
        effectId: string;
        params?: Record<string, unknown>;
        range?: { start: { num: string; den: string }; end: { num: string; den: string } };
      }>;
      keyframes?: Record<string, Array<{ id: string; time: { num: string; den: string }; value: number }>>;
    }> }>;
    captions: Array<{
      id: string;
      text: string;
      animInStyle?: "fade" | "scale" | "typewriter" | "none";
      wordHighlightColor?: string;
      words?: Array<{ text: string; start: { num: string; den: string }; end: { num: string; den: string } }>;
    }>;
  };
};

const apiPath = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;

const imageFixture = fileURLToPath(new URL("../../../src/cutvoke/assets/stickers/decor_sparkles.png", import.meta.url));
const bgmFixture = fileURLToPath(new URL("../../../src/cutvoke/assets/audio/tech_minimal.wav", import.meta.url));

const seconds = (time: { num: string; den: string }) => Number(time.num) / Number(time.den);

function pythonSortedJson(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "bigint") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return `[${value.map(pythonSortedJson).join(", ")}]`;
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}: ${pythonSortedJson(item)}`).join(", ")}}`;
  }
  throw new Error(`Cannot serialize ASR signature field: ${typeof value}`);
}

function sourceSignatureForTest(clip: Project["sequence"]["tracks"][number]["clips"][number]): string {
  const completeClip = clip as typeof clip & {
    sourceStart: { num: string; den: string };
    speed: { num: string; den: string };
    speedCurve?: unknown;
  };
  const path = resolve(completeClip.assetRef.sourcePath);
  const stat = statSync(path, { bigint: true });
  const state = {
    clipId: completeClip.id,
    path,
    size: stat.size,
    mtimeNs: stat.mtimeNs,
    timelineStart: completeClip.timelineStart,
    timelineEnd: completeClip.timelineEnd,
    sourceStart: completeClip.sourceStart,
    speed: completeClip.speed,
    speedCurve: completeClip.speedCurve ?? null,
  };
  return createHash("sha256").update(pythonSortedJson(state), "utf8").digest("hex");
}

async function readProject(request: APIRequestContext, id: string): Promise<Project> {
  const response = await request.get(apiPath(id));
  expect(response.ok()).toBeTruthy();
  return response.json();
}

async function apply(request: APIRequestContext, id: string, type: string, payload: object): Promise<Project> {
  const current = await readProject(request, id);
  const response = await request.post(`${apiPath(id)}/commands`, {
    data: {
      type, payload, expectedRevision: current.revision,
      commandId: `e2e-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      actor: { kind: "human", id: "browser-test" },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return readProject(request, id);
}

async function createProject(
  request: APIRequestContext,
  width = 160,
  height = 90,
): Promise<{ id: string; name: string; sources: string[] }> {
  const fixture = await readProject(request, "e2e-fixture");
  const sources = fixture.sequence.tracks[0].clips.map((clip) => clip.assetRef.sourcePath);
  const id = `browser-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `回归 ${id}`;
  const response = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width, height, fps: 15 },
  });
  expect(response.status()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  for (const [index, sourcePath] of sources.entries()) {
    await apply(request, id, "clip.insert", {
      trackId: "v1", clipId: `color-${index}`, sourcePath,
      timelineStart: { num: index * 5, den: 1 },
      timelineEnd: { num: (index + 1) * 5, den: 1 },
    });
  }
  await apply(request, id, "caption.add", {
    captionId: "caption-one", text: "原字幕",
    start: { num: 1, den: 1 }, end: { num: 3, den: 1 },
  });
  return { id, name, sources };
}

async function openProject(page: Page, name: string, repair = false): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  if (repair) await page.getByRole("button", { name: "进入工程修复" }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 30_000 });
}

async function clipOrder(request: APIRequestContext, id: string): Promise<string[]> {
  const project = await readProject(request, id);
  return [...project.sequence.tracks[0].clips]
    .sort((a, b) => Number(a.timelineStart.num) / Number(a.timelineStart.den)
      - Number(b.timelineStart.num) / Number(b.timelineStart.den))
    .map((clip) => clip.id);
}

test("title and action safe guides map to landscape, portrait, and square canvases", async ({ page, request }) => {
  const landscape = await createProject(request, 1920, 1080);
  await openProject(page, landscape.name);
  const landscapeToggle = page.getByRole("button", { name: "切换标题安全区" });
  await landscapeToggle.click();
  let guides = page.locator(".player__title-safe-area");
  await expect(guides).toHaveAttribute("viewBox", "0 0 1920 1080");
  await expect(guides.locator('[data-guide="title-safe"]')).toHaveAttribute("x", "192");
  await expect(guides.locator('[data-guide="action-safe"]')).toHaveAttribute("x", "96");
  await landscapeToggle.click();
  await expect(guides).toHaveCount(0);

  const portrait = await createProject(request, 1080, 1920);
  await openProject(page, portrait.name);
  const toggle = page.getByRole("button", { name: "切换标题安全区" });
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  guides = page.locator(".player__title-safe-area");
  await expect(guides).toHaveAttribute("viewBox", "0 0 1080 1920");
  await expect(guides.locator('[data-guide="title-safe"]')).toHaveAttribute("x", "108");
  await expect(guides.locator('[data-guide="title-safe"]')).toHaveAttribute("y", "192");
  await expect(guides.locator('[data-guide="title-safe"]')).toHaveAttribute("width", "864");
  await expect(guides.locator('[data-guide="title-safe"]')).toHaveAttribute("height", "1536");
  await expect(guides.locator('[data-guide="action-safe"]')).toHaveAttribute("x", "54");
  await expect(guides.locator('[data-guide="action-safe"]')).toHaveAttribute("y", "96");
  await expect(guides.locator('[data-guide="action-safe"]')).toHaveAttribute("width", "972");
  await expect(guides.locator('[data-guide="action-safe"]')).toHaveAttribute("height", "1728");
  await toggle.click();
  await expect(guides).toHaveCount(0);

  const square = await createProject(request, 1080, 1080);
  await openProject(page, square.name);
  await page.getByRole("button", { name: "切换标题安全区" }).click();
  guides = page.locator(".player__title-safe-area");
  await expect(guides).toHaveAttribute("viewBox", "0 0 1080 1080");
  await expect(guides.locator('[data-guide="title-safe"]')).toHaveAttribute("y", "108");
  await expect(guides.locator('[data-guide="action-safe"]')).toHaveAttribute("y", "54");
});

test("click selects without moving; keyboard and pointer reorder whole clips with undo", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  const green = page.locator(".clip-block").filter({ hasText: "green.mp4" });
  const originalRevision = (await readProject(request, id)).revision;
  await green.click();
  expect((await readProject(request, id)).revision).toBe(originalRevision);
  await green.focus();
  await green.press("Alt+ArrowLeft");
  await expect.poll(() => clipOrder(request, id)).toEqual(["color-1", "color-0", "color-2"]);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(() => clipOrder(request, id)).toEqual(["color-0", "color-1", "color-2"]);

  const blue = page.locator(".clip-block").filter({ hasText: "blue.mp4" });
  const red = page.locator(".clip-block").filter({ hasText: "red.mp4" });
  const source = await blue.boundingBox();
  const target = await red.boundingBox();
  expect(source && target).toBeTruthy();
  await page.mouse.move(source!.x + source!.width / 2, source!.y + source!.height / 2);
  await page.mouse.down();
  await page.mouse.move(target!.x + target!.width / 4, target!.y + target!.height / 2, { steps: 12 });
  await expect(page.locator(".clip-block--ghost")).toBeVisible();
  await expect(page.locator(".timeline-insert-caret")).toBeVisible();
  await page.mouse.up();
  await expect.poll(() => clipOrder(request, id)).toEqual(["color-2", "color-0", "color-1"]);
  expect((await readProject(request, id)).sequence.tracks[0].clips).toHaveLength(3);
});

test("agent edit lease keeps the browser read-only while showing agent edits, then restores editing", async ({ page, request }) => {
  const { id, name, sources } = await createProject(request);
  await openProject(page, name);

  const acquiredResponse = await request.post(`${apiPath(id)}/edit-lock`, {
    data: { owner: "e2e-agent", ttlSeconds: 60 },
  });
  expect(acquiredResponse.ok(), await acquiredResponse.text()).toBeTruthy();
  const acquired = await acquiredResponse.json() as { locked: boolean; lease: { leaseId: string } };
  expect(acquired.locked).toBe(true);
  const leaseId = acquired.lease.leaseId;
  let agentRevision = "";

  try {
    const lockStatus = page.getByRole("status").filter({ hasText: "Agent 正在编辑" });
    await expect(lockStatus).toBeVisible();

    const green = page.locator(".clip-block").filter({ hasText: "green.mp4" });
    const before = (await readProject(request, id)).revision;
    await green.focus();
    await green.press("Alt+ArrowLeft");
    expect((await readProject(request, id)).revision).toBe(before);

    const sourcePath = sources[0];
    const payload = {
      trackId: "v1", clipId: "agent-added-clip", sourcePath,
      timelineStart: { num: 15, den: 1 }, timelineEnd: { num: 20, den: 1 },
    };
    const dryRunResponse = await request.post(`${apiPath(id)}/commands`, {
      data: {
        type: "clip.insert", payload, expectedRevision: before, dryRun: true,
        actor: { kind: "agent", id: "e2e-agent" },
      },
    });
    expect(dryRunResponse.ok(), await dryRunResponse.text()).toBeTruthy();
    const dryRun = await dryRunResponse.json() as {
      valid: boolean;
      changedEntities: Array<{
        type: string; id: string; change: string; mode?: string;
        timelineRange?: {
          before: { start: { num: string; den: string }; end: { num: string; den: string } } | null;
          after: { start: { num: string; den: string }; end: { num: string; den: string } } | null;
        };
      }>;
      commandType: string;
    };
    expect(dryRun.valid).toBe(true);
    expect(dryRun.commandType).toBe("clip.insert");
    expect(dryRun.changedEntities).toContainEqual({
      type: "clip", id: "agent-added-clip", change: "created", mode: "append",
      timelineRange: {
        before: null,
        after: {
          start: { num: "15", den: "1" },
          end: { num: "20", den: "1" },
        },
      },
    });
    expect((await readProject(request, id)).revision).toBe(before);

    const commandResponse = await request.post(`${apiPath(id)}/commands`, {
      data: {
        type: "clip.insert", payload, expectedRevision: before,
        editLeaseId: leaseId, commandId: `e2e-agent-${Date.now()}`,
        actor: { kind: "agent", id: "e2e-agent" },
      },
    });
    expect(commandResponse.ok(), await commandResponse.text()).toBeTruthy();
    agentRevision = (await commandResponse.json() as { revision: string }).revision;

    const sourceName = sourcePath.split(/[\\/]/).pop()!;
    await expect(page.locator(".clip-block").filter({ hasText: sourceName })).toHaveCount(2);
    await expect(lockStatus).toBeVisible();
  } finally {
    const releasedResponse = await request.post(`${apiPath(id)}/edit-lock/release`, {
      data: { leaseId },
    });
    expect(releasedResponse.ok(), await releasedResponse.text()).toBeTruthy();
    expect((await releasedResponse.json() as { released: boolean }).released).toBe(true);
  }

  await expect(page.getByRole("status").filter({ hasText: "Agent 正在编辑" })).toHaveCount(0);
  const green = page.locator(".clip-block").filter({ hasText: "green.mp4" });
  await green.focus();
  await green.press("Alt+ArrowLeft");
  await expect.poll(async () => (await readProject(request, id)).revision).not.toBe(agentRevision);
});

test("resource thumbnails show session cache, retry preview errors, and cancel pending renders", async ({ page, request }) => {
  const { name } = await createProject(request);
  await openProject(page, name);

  let targetEffectId: string | null = null;
  let failNextPreview = false;
  let delayNextPreview = false;
  let delayedPreviewStarted = false;
  let releaseDelayedPreview: (() => void) | undefined;
  const delayedPreview = new Promise<void>((resolve) => { releaseDelayedPreview = resolve; });
  const previewImage = readFileSync(imageFixture);
  const errorBody = JSON.stringify({ error: { message: "模拟预览失败" } });

  const previewRoute = /\/resource-preview\?/;
  await page.route(previewRoute, async (route) => {
    const effectId = new URL(route.request().url()).searchParams.get("effectId");
    if (!effectId) {
      await route.continue();
      return;
    }
    if (targetEffectId === null) {
      targetEffectId = effectId;
      await route.fulfill({ status: 500, contentType: "application/json", body: errorBody });
      return;
    }
    if (effectId === targetEffectId && failNextPreview) {
      failNextPreview = false;
      await route.fulfill({ status: 500, contentType: "application/json", body: errorBody });
      return;
    }
    if (effectId === targetEffectId && delayNextPreview) {
      delayNextPreview = false;
      delayedPreviewStarted = true;
      await delayedPreview;
      try {
        await route.fulfill({ status: 200, contentType: "image/png", body: previewImage });
      } catch {
        // Closing the preview aborts this routed browser request.
      }
      return;
    }
    await route.fulfill({ status: 200, contentType: "image/png", body: previewImage });
  });

  const creativeTabs = page.getByRole("tablist", { name: "创作域" });
  await creativeTabs.getByRole("tab", { name: "特效" }).click();
  await expect(page.getByText(`资源包 v${await getBuiltinPackVersion(request)} · 离线可用`)).toBeVisible();
  const packScreenshotPath = resolve(process.cwd(), "../../output/playwright/resource-pack-offline-status-20260924.png");
  mkdirSync(dirname(packScreenshotPath), { recursive: true });
  await page.screenshot({ path: packScreenshotPath, fullPage: false });
  const firstEffectCard = page.locator(".resource-grid:not(.resource-preset-grid) .resource-card").first();
  await firstEffectCard.scrollIntoViewIfNeeded();
  const thumbnailRetry = firstEffectCard.getByRole("button", { name: /^重试.*封面$/ });
  await expect(thumbnailRetry).toBeVisible({ timeout: 15_000 });
  const card = firstEffectCard;
  expect(targetEffectId).toBeTruthy();
  await thumbnailRetry.click();
  await expect(card.locator(".resource-card__thumbnail-cache")).toHaveText("已缓存");

  failNextPreview = true;
  await card.getByRole("button", { name: "预览", exact: true }).click();
  await expect(page.locator(".resource-preview__failure")).toContainText("模拟预览失败");
  await page.getByRole("button", { name: "重试预览" }).click();
  await expect(page.locator(".resource-preview img")).toBeVisible();
  const screenshotPath = resolve(process.cwd(), "../../output/playwright/resource-preview-cache-retry-20260924.png");
  mkdirSync(dirname(screenshotPath), { recursive: true });
  await page.screenshot({ path: screenshotPath, fullPage: false });

  delayNextPreview = true;
  await card.getByRole("button", { name: "预览", exact: true }).click();
  await expect.poll(() => delayedPreviewStarted).toBe(true);
  const cancelPreview = page.getByRole("button", { name: "取消效果预览" });
  await expect(cancelPreview).toBeVisible();
  await cancelPreview.click();
  await expect(page.locator(".resource-preview")).toHaveCount(0);
  releaseDelayedPreview?.();
  await page.unroute(previewRoute);
});

test("resource catalog load failures show the cause and can be retried", async ({ page, request }) => {
  const { name } = await createProject(request);
  await openProject(page, name);

  let failNextRequest = true;
  await page.route("**/resources", async (route) => {
    if (failNextRequest) {
      failNextRequest = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "模拟资源目录暂时不可用" } }),
      });
      return;
    }
    await route.continue();
  });

  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  const failure = page.locator(".resource-preview__failure")
    .filter({ hasText: "效果清单加载失败" });
  await expect(failure).toContainText("模拟资源目录暂时不可用");
  await expect(failure.getByRole("button", { name: "重试资源库" })).toBeVisible();
  await expect(page.locator(".cv-empty:visible")).toHaveCount(0);

  await failure.getByRole("button", { name: "重试资源库" }).click();
  await expect(page.locator(".resource-grid:not(.resource-preset-grid) .resource-card").first())
    .toBeVisible();
  await expect(page.locator(".resource-preview__failure:visible")).toHaveCount(0);
  await expect(page.locator(".resource-context:visible"))
    .toContainText("可先浏览工程示例封面；应用前请选中目标片段");
  const samplePreset = page.locator(".resource-preset-card").first();
  await expect(samplePreset.getByRole("button", { name: /播放.*动态样片/ })).toBeEnabled();
  const applyPreset = samplePreset.getByRole("button", { name: "应用" });
  await expect(applyPreset).toBeDisabled();
  await expect(applyPreset).toHaveAttribute("title", "请先选中片段；样片仍可播放");
  await page.unroute("**/resources");
});

test("text title preset failures show an in-domain retry", async ({ page, request }) => {
  const { name } = await createProject(request);
  let releaseFailure!: () => void;
  const failureGate = new Promise<void>((resolve) => { releaseFailure = resolve; });
  let firstRequest = true;
  await page.route("**/api/v1/presets", async (route) => {
    if (firstRequest) {
      firstRequest = false;
      await failureGate;
      await route.fulfill({ status: 503, contentType: "application/json",
        body: JSON.stringify({ error: { message: "模拟文字预设目录暂时不可用" } }) });
      return;
    }
    await route.continue();
  });

  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "文字" }).click();
  const titleLibrary = page.locator(".title-library");
  await expect.poll(() => firstRequest).toBe(false);
  await expect(titleLibrary.getByRole("status").filter({ hasText: "正在读取文字预设" })).toBeVisible();
  await expect(titleLibrary.locator(".cv-empty")).toHaveCount(0);

  releaseFailure();
  const failure = titleLibrary.getByRole("alert");
  await expect(failure).toContainText("模拟文字预设目录暂时不可用");
  await failure.getByRole("button", { name: "重试文字预设" }).click();
  await expect(titleLibrary.locator(".title-library__card").first()).toBeVisible();
  await expect(titleLibrary.getByRole("alert")).toHaveCount(0);
  await page.unroute("**/api/v1/presets");
});

test("caption SRT import errors can be dismissed and retried", async ({ page, request }) => {
  const { name } = await createProject(request);
  let failFirstRequest = true;
  await page.route("**/api/v1/projects/*/captions/import", async (route) => {
    if (!failFirstRequest) return route.continue();
    failFirstRequest = false;
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟字幕导入服务暂时不可用" } }) });
  });

  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕" }).click();
  await expect(page.locator(".cv-caption-row__text").filter({ hasText: "原字幕" })).toBeVisible();
  const srtInput = page.locator(".cv-srt-row input[type=file]");
  const srtFile = {
    name: "opening.srt",
    mimeType: "application/x-subrip",
    buffer: Buffer.from("1\n00:00:00,000 --> 00:00:02,000\n开场字幕\n", "utf8"),
  };

  await srtInput.setInputFiles(srtFile);
  const errorDialog = page.getByRole("alertdialog", { name: "操作未完成" });
  await expect(errorDialog).toContainText("模拟字幕导入服务暂时不可用");
  await expect(page.locator(".cv-caption-row__text").filter({ hasText: "原字幕" })).toBeVisible();
  await errorDialog.getByRole("button", { name: "关闭" }).click();

  await srtInput.setInputFiles(srtFile);
  await expect(page.locator(".cv-caption-row__text").filter({ hasText: "开场字幕" })).toBeVisible();
  await expect(page.locator(".cv-caption-row__text").filter({ hasText: "原字幕" })).toBeVisible();
  await page.unroute("**/api/v1/projects/*/captions/import");
});

test("caption save and SRT export failures preserve state and can be retried", async ({ page, request }) => {
  const { name } = await createProject(request);
  let failCaptionSave = true;
  let failSrtExport = true;
  await page.route("**/api/v1/projects/*/commands", async (route) => {
    const body = route.request().postDataJSON() as { type?: string };
    if (body.type === "caption.update" && failCaptionSave) {
      failCaptionSave = false;
      await route.fulfill({ status: 503, contentType: "application/json",
        body: JSON.stringify({ error: { message: "模拟字幕保存服务暂时不可用" } }) });
      return;
    }
    await route.continue();
  });
  await page.route("**/api/v1/projects/*/captions.srt", async (route) => {
    if (failSrtExport) {
      failSrtExport = false;
      await route.fulfill({ status: 503, contentType: "application/json",
        body: JSON.stringify({ error: { message: "模拟字幕导出服务暂时不可用" } }) });
      return;
    }
    await route.continue();
  });

  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕" }).click();
  const originalCaption = page.locator(".cv-caption-row__text").filter({ hasText: "原字幕" });
  await expect(originalCaption).toBeVisible();
  await page.getByRole("button", { name: "选择字幕 caption-one" }).click();
  const captionText = page.getByRole("textbox", { name: "编辑字幕文字" });
  await captionText.fill("恢复后字幕");
  await page.getByRole("button", { name: "保存字幕文字与时间" }).click();
  const saveError = page.getByRole("alertdialog", { name: "操作未完成" });
  await expect(saveError).toContainText("模拟字幕保存服务暂时不可用");
  await saveError.getByRole("button", { name: "关闭" }).click();
  await expect(captionText).toHaveValue("恢复后字幕");
  await expect(originalCaption).toBeVisible();
  await page.getByRole("button", { name: "保存字幕文字与时间" }).click();
  await expect(page.locator(".cv-caption-row__text").filter({ hasText: "恢复后字幕" })).toBeVisible();

  await page.getByRole("button", { name: "导出SRT" }).click();
  const exportError = page.getByRole("alertdialog", { name: "操作未完成" });
  await expect(exportError).toContainText("SRT 导出失败");
  await exportError.getByRole("button", { name: "关闭" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出SRT" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`${name}.srt`);
});

test("caption style and batch save failures can be retried", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "caption.add", {
    captionId: "caption-two", text: "第二条字幕",
    start: { num: 3, den: 1 }, end: { num: 5, den: 1 },
  });
  const failures = new Map([
    ["caption.update", "模拟字幕样式服务暂时不可用"],
    ["caption.patch", "模拟批量样式服务暂时不可用"],
  ]);
  await page.route("**/api/v1/projects/*/commands", async (route) => {
    const body = route.request().postDataJSON() as { type?: string };
    const message = body.type ? failures.get(body.type) : undefined;
    if (!message) return route.continue();
    failures.delete(body.type!);
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message } }) });
  });

  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕" }).click();
  await page.getByRole("button", { name: "选择字幕 caption-one" }).click();
  await page.getByRole("button", { name: "右对齐" }).click();
  const styleError = page.getByRole("alertdialog", { name: "操作未完成" });
  await expect(styleError).toContainText("模拟字幕样式服务暂时不可用");
  await styleError.getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "右对齐" }).click();
  await expect.poll(async () => (await readProject(request, id)).sequence.captions
    .find((caption) => caption.id === "caption-one")?.align).toBe("right");

  await page.getByRole("button", { name: "批量应用文字样式" }).click();
  const batchError = page.getByRole("alertdialog", { name: "操作未完成" });
  await expect(batchError).toContainText("模拟批量样式服务暂时不可用");
  await batchError.getByRole("button", { name: "关闭" }).click();
  await page.getByRole("button", { name: "批量应用文字样式" }).click();
  await expect.poll(async () => (await readProject(request, id)).sequence.captions
    .find((caption) => caption.id === "caption-two")?.align).toBe("right");
});

test("built-in preset status failure leaves effects available and can be retried", async ({ page, request }) => {
  const { name } = await createProject(request);
  let failFirstRequest = true;
  await page.route("**/api/v1/presets", async (route) => {
    if (!failFirstRequest) return route.continue();
    failFirstRequest = false;
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟预设审核服务暂时不可用" } }) });
  });

  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  const presetFailure = page.getByRole("alert")
    .filter({ hasText: "内置预设审核状态读取失败" });
  await expect(presetFailure).toContainText("模拟预设审核服务暂时不可用");
  await expect(presetFailure.getByRole("button", { name: "重试内置预设" })).toBeVisible();
  await expect(page.locator(".resource-grid:not(.resource-preset-grid) .resource-card").first())
    .toBeVisible();
  await expect(page.locator(".resource-preset-card")).toHaveCount(0);

  await presetFailure.getByRole("button", { name: "重试内置预设" }).click();
  await expect(page.locator(".resource-preset-card").first()).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "内置预设审核状态读取失败" })).toHaveCount(0);
  await page.unroute("**/api/v1/presets");
});

test("media and sticker catalog failures expose a retry in their own domains", async ({ page, request }) => {
  const { name } = await createProject(request);
  let assetsAvailable = false;
  let stickersAvailable = false;
  await page.route("**/api/v1/assets", async (route) => {
    if (assetsAvailable) return route.continue();
    return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟素材服务暂时不可用" } }),
    });
  });
  await page.route("**/api/v1/stickers", async (route) => {
    if (stickersAvailable) return route.continue();
    return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟贴纸服务暂时不可用" } }),
    });
  });

  await openProject(page, name);
  const assetFailure = page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "素材库读取失败" });
  await expect(assetFailure).toContainText("模拟素材服务暂时不可用");
  assetsAvailable = true;
  await assetFailure.getByRole("button", { name: "重试素材库" }).click();
  await expect(page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "素材库读取失败" })).toHaveCount(0);

  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();
  const stickerFailure = page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "贴纸库读取失败" });
  await expect(stickerFailure).toContainText("模拟贴纸服务暂时不可用");
  stickersAvailable = true;
  await stickerFailure.getByRole("button", { name: "重试贴纸库" }).click();
  await expect(page.locator(".sticker-card").first()).toBeVisible({ timeout: 15_000 });
  await page.unroute("**/api/v1/assets");
  await page.unroute("**/api/v1/stickers");
});

test("six resource services can fail together and recover independently across domains", async ({ page, request }) => {
  const { name, sources } = await createProject(request);
  let releaseAssetFailure!: () => void;
  let releaseStickerFailure!: () => void;
  let releaseEffectFailure!: () => void;
  let releasePackFailure!: () => void;
  let releaseRegistryFailure!: () => void;
  let releasePresetFailure!: () => void;
  const assetFailureGate = new Promise<void>((resolve) => { releaseAssetFailure = resolve; });
  const stickerFailureGate = new Promise<void>((resolve) => { releaseStickerFailure = resolve; });
  const effectFailureGate = new Promise<void>((resolve) => { releaseEffectFailure = resolve; });
  const packFailureGate = new Promise<void>((resolve) => { releasePackFailure = resolve; });
  const registryFailureGate = new Promise<void>((resolve) => { releaseRegistryFailure = resolve; });
  const presetFailureGate = new Promise<void>((resolve) => { releasePresetFailure = resolve; });
  let assetRequestsStarted = 0;
  let assetsAvailable = false;
  let stickersAvailable = false;
  let effectsAvailable = false;
  let packAvailable = false;
  let registryAvailable = false;
  let presetAvailable = false;

  await page.route("**/api/v1/assets", async (route) => {
    assetRequestsStarted += 1;
    await assetFailureGate;
    if (assetsAvailable) return route.continue();
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟素材服务暂时不可用" } }) });
  });
  await page.route("**/api/v1/stickers", async (route) => {
    await stickerFailureGate;
    if (stickersAvailable) return route.continue();
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟贴纸服务暂时不可用" } }) });
  });
  await page.route("**/api/v1/projects/*/resources", async (route) => {
    await effectFailureGate;
    if (effectsAvailable) return route.continue();
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟效果目录暂时不可用" } }) });
  });
  await page.route("**/api/v1/resource-packs", async (route) => {
    await packFailureGate;
    if (packAvailable) return route.continue();
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟资源包状态暂时不可用" } }) });
  });
  await page.route("**/api/v1/resource-packs/registries", async (route) => {
    await registryFailureGate;
    if (registryAvailable) return route.continue();
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟在线目录读取暂时不可用" } }) });
  });
  await page.route("**/api/v1/presets", async (route) => {
    await presetFailureGate;
    if (presetAvailable) return route.continue();
    await route.fulfill({ status: 503, contentType: "application/json",
      body: JSON.stringify({ error: { message: "模拟预设审核服务暂时不可用" } }) });
  });

  await openProject(page, name);
  await expect.poll(() => assetRequestsStarted).toBeGreaterThan(0);
  await expect(page.getByRole("status").filter({ hasText: "正在读取素材库" })).toBeVisible();
  await expect(page.locator(".media-empty .cv-empty")).toHaveCount(0);
  const projectMediaName = sources[0].split(/[\\/]/).pop() || sources[0];
  await expect(page.locator(".media-item").filter({ hasText: projectMediaName }).first()).toBeVisible();
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  const manager = page.locator(".resource-pack-manager:visible").first();
  await manager.locator("summary").click();
  const registry = manager.locator(".resource-registry");
  await expect(manager.getByRole("status").filter({ hasText: "正在读取本机资源包状态" })).toBeVisible();
  await expect(registry.getByRole("status").filter({ hasText: "正在读取在线资源目录" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "正在读取内置预设审核状态" })).toBeVisible();
  await expect(registry.getByText("尚未添加在线资源目录。")).toHaveCount(0);

  releaseAssetFailure();
  releaseStickerFailure();
  releaseEffectFailure();
  releasePackFailure();
  releaseRegistryFailure();
  releasePresetFailure();
  const effectFailure = page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "效果清单加载失败" });
  const packFailure = manager.getByRole("alert").filter({ hasText: "资源包状态读取失败" });
  const registryFailure = registry.getByRole("alert").filter({ hasText: "在线资源目录读取失败" });
  const presetFailure = page.getByRole("alert").filter({ hasText: "内置预设审核状态读取失败" });
  await expect(effectFailure).toContainText("模拟效果目录暂时不可用");
  await expect(effectFailure.getByRole("button", { name: "重试资源库" })).toBeVisible();
  await expect(packFailure).toContainText("模拟资源包状态暂时不可用");
  await expect(packFailure.getByRole("button", { name: "重试资源包状态" })).toBeVisible();
  await expect(registryFailure).toContainText("模拟在线目录读取暂时不可用");
  await expect(registryFailure.getByRole("button", { name: "重试资源目录" })).toBeVisible();
  await expect(presetFailure).toContainText("模拟预设审核服务暂时不可用");
  await expect(presetFailure.getByRole("button", { name: "重试内置预设" })).toBeVisible();
  await expect(registry.getByText("尚未添加在线资源目录。")).toHaveCount(0);
  const statusScreenshot = resolve(process.cwd(), "../../output/playwright/resource-recovery-states-20260926.png");
  mkdirSync(dirname(statusScreenshot), { recursive: true });
  await page.screenshot({ path: statusScreenshot });

  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "素材" }).click();
  const assetFailure = page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "素材库读取失败" });
  await expect(assetFailure).toContainText("模拟素材服务暂时不可用");
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "音乐" }).click();
  await expect(page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "素材库读取失败" })).toContainText("模拟素材服务暂时不可用");

  assetsAvailable = true;
  await assetFailure.getByRole("button", { name: "重试素材库" }).click();
  await expect(page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "素材库读取失败" })).toHaveCount(0);

  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();
  const stickerFailure = page.locator(".resource-preview__failure:visible")
    .filter({ hasText: "贴纸库读取失败" });
  await expect(stickerFailure).toContainText("模拟贴纸服务暂时不可用");
  stickersAvailable = true;
  await stickerFailure.getByRole("button", { name: "重试贴纸库" }).click();
  await expect(page.locator(".sticker-card").first()).toBeVisible({ timeout: 15_000 });

  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  await expect(effectFailure).toContainText("模拟效果目录暂时不可用");
  await expect(presetFailure).toContainText("模拟预设审核服务暂时不可用");
  await expect(packFailure).toContainText("模拟资源包状态暂时不可用");
  await expect(registryFailure).toContainText("模拟在线目录读取暂时不可用");
  effectsAvailable = true;
  await effectFailure.getByRole("button", { name: "重试资源库" }).click();
  await expect(page.locator(".resource-grid:not(.resource-preset-grid) .resource-card").first())
    .toBeVisible();

  packAvailable = true;
  await packFailure.getByRole("button", { name: "重试资源包状态" }).click();
  await expect(manager.locator(".resource-pack-manager__list")).toContainText("内置资源");
  await expect(registry.getByRole("alert").filter({ hasText: "在线资源目录读取失败" })).toBeVisible();
  await expect(presetFailure).toBeVisible();

  presetAvailable = true;
  await presetFailure.getByRole("button", { name: "重试内置预设" }).click();
  await expect(page.locator(".resource-preset-card").first()).toBeVisible();
  await expect(registry.getByRole("alert").filter({ hasText: "在线资源目录读取失败" })).toBeVisible();

  registryAvailable = true;
  await registryFailure.getByRole("button", { name: "重试资源目录" }).click();
  await expect(registry.getByText("尚未添加在线资源目录。")).toBeVisible();
  await expect(registry.getByRole("alert")).toHaveCount(0);
  await page.unroute("**/api/v1/assets");
  await page.unroute("**/api/v1/stickers");
  await page.unroute("**/api/v1/projects/*/resources");
  await page.unroute("**/api/v1/presets");
});

const isolatedResourceFailures = [
  { id: "assets", label: "素材", route: "**/api/v1/assets", message: "模拟素材单域故障" },
  { id: "stickers", label: "贴纸", route: "**/api/v1/stickers", message: "模拟贴纸单域故障" },
  { id: "effects", label: "效果目录", route: "**/api/v1/projects/*/resources", message: "模拟效果目录单域故障" },
  { id: "packs", label: "资源包状态", route: "**/api/v1/resource-packs", message: "模拟资源包状态单域故障" },
  { id: "registries", label: "在线资源目录", route: "**/api/v1/resource-packs/registries", message: "模拟在线目录单域故障" },
  { id: "presets", label: "内置预设", route: "**/api/v1/presets", message: "模拟内置预设单域故障" },
] as const;

for (const target of isolatedResourceFailures) {
  test(`${target.label}接口单独失败时其他资源域可用并可独立恢复`, async ({ page, request }) => {
    const { id, name, sources } = await createProject(request);
    let targetAvailable = false;
    await page.route(target.route, async (route) => {
      if (targetAvailable) return route.continue();
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: target.message } }),
      });
    });

    await openProject(page, name);
    const tabs = page.getByRole("tablist", { name: "创作域" });
    const assetTab = tabs.getByRole("tab", { name: "素材" });
    const stickerTab = tabs.getByRole("tab", { name: "贴纸" });
    const effectsTab = tabs.getByRole("tab", { name: "特效" });
    const projectMediaName = sources[0].split(/[\\/]/).pop() || sources[0];

    await assetTab.click();
    if (target.id === "assets") {
      const failure = page.locator(".resource-preview__failure:visible")
        .filter({ hasText: "素材库读取失败" });
      await expect(failure).toContainText(target.message);
    } else {
      await expect(page.locator(".media-item").filter({ hasText: projectMediaName }).first()).toBeVisible();
    }

    await stickerTab.click();
    if (target.id === "stickers") {
      const failure = page.locator(".resource-preview__failure:visible")
        .filter({ hasText: "贴纸库读取失败" });
      await expect(failure).toContainText(target.message);
    } else {
      await expect(page.locator(".sticker-card").first()).toBeVisible({ timeout: 15_000 });
    }

    await effectsTab.click();
    if (target.id === "effects") {
      const failure = page.locator(".resource-preview__failure:visible")
        .filter({ hasText: "效果清单加载失败" });
      await expect(failure).toContainText(target.message);
    } else {
      await expect(page.locator(".resource-grid:not(.resource-preset-grid) .resource-card").first())
        .toBeVisible();
    }
    const presetFailure = page.getByRole("alert").filter({ hasText: "内置预设审核状态读取失败" });
    if (target.id === "presets") {
      await expect(presetFailure).toContainText(target.message);
    } else {
      await expect(page.locator(".resource-preset-card").first()).toBeVisible();
    }

    const manager = page.locator(".resource-pack-manager:visible").first();
    await manager.locator("summary").click();
    const registry = manager.locator(".resource-registry");
    const packFailure = manager.getByRole("alert").filter({ hasText: "资源包状态读取失败" });
    const registryFailure = registry.getByRole("alert").filter({ hasText: "在线资源目录读取失败" });
    if (target.id === "packs") {
      await expect(packFailure).toContainText(target.message);
    } else {
      await expect(manager.locator(".resource-pack-manager__list")).toContainText("内置资源");
    }
    if (target.id === "registries") {
      await expect(registryFailure).toContainText(target.message);
    } else {
      await expect(registry.getByText("尚未添加在线资源目录。")).toBeVisible();
    }

    targetAvailable = true;
    if (target.id === "assets") {
      await assetTab.click();
      const failure = page.locator(".resource-preview__failure:visible")
        .filter({ hasText: "素材库读取失败" });
      await failure.getByRole("button", { name: "重试素材库" }).click();
      await expect(failure).toHaveCount(0);
      await expect(page.locator(".media-item").filter({ hasText: projectMediaName }).first()).toBeVisible();
    } else if (target.id === "stickers") {
      await stickerTab.click();
      const failure = page.locator(".resource-preview__failure:visible")
        .filter({ hasText: "贴纸库读取失败" });
      await failure.getByRole("button", { name: "重试贴纸库" }).click();
      await expect(page.locator(".sticker-card").first()).toBeVisible({ timeout: 15_000 });
    } else if (target.id === "effects") {
      await effectsTab.click();
      const failure = page.locator(".resource-preview__failure:visible")
        .filter({ hasText: "效果清单加载失败" });
      await failure.getByRole("button", { name: "重试资源库" }).click();
      await expect(page.locator(".resource-grid:not(.resource-preset-grid) .resource-card").first())
        .toBeVisible();
    } else if (target.id === "packs") {
      await packFailure.getByRole("button", { name: "重试资源包状态" }).click();
      await expect(manager.locator(".resource-pack-manager__list")).toContainText("内置资源");
    } else if (target.id === "registries") {
      await registryFailure.getByRole("button", { name: "重试资源目录" }).click();
      await expect(registry.getByText("尚未添加在线资源目录。")).toBeVisible();
    } else {
      await presetFailure.getByRole("button", { name: "重试内置预设" }).click();
      await expect(page.locator(".resource-preset-card").first()).toBeVisible();
    }

    await page.unroute(target.route);
  });
}

test("resource category keeps target, filters, search, and scroll across panel changes", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "green.mp4" }).click();

  const editorTabs = page.getByRole("tablist", { name: "工程编辑面板" });
  const creativeTabs = page.getByRole("tablist", { name: "创作域" });
  const inspectTab = editorTabs.getByRole("tab", { name: "片段属性" });
  const speedTab = editorTabs.getByRole("tab", { name: "变速" });
  await expect(inspectTab).toHaveAttribute("tabindex", "0");
  await inspectTab.focus();
  await inspectTab.press("ArrowRight");
  await expect(speedTab).toBeFocused();
  await expect(speedTab).toHaveAttribute("aria-selected", "true");
  await speedTab.press("End");
  const audioTab = editorTabs.getByRole("tab", { name: "音频分析" });
  await expect(audioTab).toBeFocused();
  await expect(audioTab).toHaveAttribute("aria-selected", "true");
  await audioTab.press("Home");
  await expect(inspectTab).toBeFocused();
  await expect(inspectTab).toHaveAttribute("aria-selected", "true");

  await creativeTabs.getByRole("tab", { name: "特效" }).click();

  const filterTab = page.getByRole("tablist", { name: "效果分类" })
    .getByRole("tab", { name: /滤镜/ });
  await expect(filterTab).toBeVisible();
  const categoryTabs = page.getByRole("tablist", { name: "效果分类" }).getByRole("tab");
  const allCategoryTab = categoryTabs.first();
  const lastCategoryTab = categoryTabs.last();
  await expect(allCategoryTab).toHaveAttribute("tabindex", "0");
  await allCategoryTab.focus();
  await allCategoryTab.press("End");
  await expect(lastCategoryTab).toBeFocused();
  await expect(lastCategoryTab).toHaveAttribute("aria-selected", "true");
  await lastCategoryTab.press("Home");
  await expect(allCategoryTab).toBeFocused();
  await expect(allCategoryTab).toHaveAttribute("aria-selected", "true");
  await filterTab.click();
  const search = page.getByRole("searchbox", { name: "搜索预设和效果" });
  await search.fill("cutvoke.fx");
  const cards = page.locator(".resource-grid:not(.resource-preset-grid) .resource-card");
  await expect(cards.first()).toBeVisible();
  const applyEffect = cards.first().getByRole("button", { name: "应用到选中片段" });
  await apply(request, id, "track.update", { trackId: "v1", locked: true });
  await expect(page.getByRole("status")
    .filter({ hasText: "所选片段所在轨道已锁定，解锁后可应用效果" }).first()).toBeVisible();
  await expect(applyEffect).toBeDisabled();
  await expect(applyEffect).toHaveAttribute("title", "所选片段所在轨道已锁定，解锁后可应用效果");
  await apply(request, id, "track.update", { trackId: "v1", locked: false });
  await expect(applyEffect).toBeEnabled();

  const panel = page.locator(".zone-left__scroll");
  await panel.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const savedScroll = await panel.evaluate((element) => element.scrollTop);
  expect(savedScroll).toBeGreaterThan(0);
  const effectCount = async () => (await readProject(request, id)).sequence.tracks
    .find((track) => track.id === "v1")!.clips
    .find((clip) => clip.id === "color-1")!.effects.length;
  const before = await effectCount();

  await editorTabs.getByRole("tab", { name: "片段属性" }).click();
  await creativeTabs.getByRole("tab", { name: "特效" }).click();
  await expect(search).toHaveValue("cutvoke.fx");
  await expect(filterTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(".resource-context:visible")).toContainText("视频片段");
  await expect.poll(() => panel.evaluate((element) => element.scrollTop)).toBe(savedScroll);

  await applyEffect.click();
  await expect.poll(effectCount).toBe(before + 1);
});

test("eight creative domains share keyboard navigation and preserve the selected edit target", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  let finishAudit!: () => void;
  const auditGate = new Promise<void>((resolve) => { finishAudit = resolve; });
  const auditRequests = { packs: 0, presets: 0 };
  await page.route(/\/api\/v1\/(resource-packs|presets)$/, async (route) => {
    const key = route.request().url().endsWith("/presets") ? "presets" : "packs";
    auditRequests[key]++;
    await auditGate;
    await route.continue();
  });
  await page.route("**/api/v1/assets", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ assets: [] }),
  }));
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "green.mp4" }).click();

  const creativeTabs = page.getByRole("tablist", { name: "创作域" });
  const tabs = creativeTabs.getByRole("tab");
  await expect(tabs).toHaveCount(8);
  const inspectorTab = page.getByRole("tablist", { name: "工程编辑面板" }).getByRole("tab", { name: "片段属性" });
  await expect(inspectorTab).toHaveAttribute("aria-selected", "true");

  let current = creativeTabs.getByRole("tab", { name: "素材" });
  await current.focus();
  const domains = [
    { name: "音乐", verify: async () => {
      await expect(page.getByRole("searchbox", { name: "搜索音乐与音效" })).toBeVisible();
      await expect(page.getByText(/还没有导入音乐或音效/)).toBeVisible();
      await expect(page.getByRole("tablist", { name: "素材来源" })
        .getByRole("tab", { name: /我的素材/ })).toContainText("0");
    } },
    { name: "文字", verify: () => expect(page.getByRole("region", { name: "文字标题库" })).toBeVisible() },
    { name: "贴纸", verify: () => expect(page.getByRole("region", { name: "贴纸库" })).toBeVisible() },
    { name: "特效", verify: () => expect(page.getByRole("searchbox", { name: "搜索预设和效果" })).toBeVisible() },
    { name: "转场", verify: () => expect(page.getByRole("searchbox", { name: "搜索预设和效果" })).toBeVisible() },
    { name: "字幕", verify: () => expect(page.getByLabel("字幕文本")).toBeVisible() },
    { name: "滤镜", verify: () => expect(page.getByRole("searchbox", { name: "搜索预设和效果" })).toBeVisible() },
  ];
  for (const domain of domains) {
    await current.press("ArrowRight");
    current = creativeTabs.getByRole("tab", { name: domain.name });
    await expect(current).toBeFocused();
    await expect(current).toHaveAttribute("aria-selected", "true");
    await domain.verify();
    await expect(inspectorTab).toHaveAttribute("aria-selected", "true");
  }
  // Domain switches must share pending audits rather than queueing one
  // expensive scan per panel and starving unrelated catalog requests.
  await expect.poll(() => auditRequests).toEqual({ packs: 1, presets: 1 });
  finishAudit();

  await current.press("Home");
  current = creativeTabs.getByRole("tab", { name: "素材" });
  await expect(current).toBeFocused();
  await current.press("End");
  current = creativeTabs.getByRole("tab", { name: "滤镜" });
  await expect(current).toBeFocused();
  await creativeTabs.getByRole("tab", { name: "特效" }).click();
  const effectSearch = page.getByRole("searchbox", { name: "搜索预设和效果" });
  await effectSearch.fill("cutvoke.fx");
  await expect(page.locator(".resource-grid:visible:not(.resource-preset-grid) .resource-card").first()).toBeVisible();
  const leftScroll = page.locator(".zone-left__scroll");
  await leftScroll.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  const effectScroll = await leftScroll.evaluate((element) => element.scrollTop);
  expect(effectScroll).toBeGreaterThan(0);
  let finishRefresh!: () => void;
  const refreshGate = new Promise<void>((resolve) => { finishRefresh = resolve; });
  let refreshRequests = 0;
  await page.route(/\/api\/v1\/projects\/[^/]+\/resources$/, async (route) => {
    refreshRequests++;
    await refreshGate;
    const response = await route.fetch();
    const catalog = await response.json();
    const effect = catalog.effects.find((item: { effectId: string }) => item.effectId.startsWith("cutvoke.fx"));
    catalog.effects.push({ ...effect, effectId: "cutvoke.fx.refreshProof", name: "Fresh catalog proof" });
    await route.fulfill({ response, json: catalog });
  });
  await creativeTabs.getByRole("tab", { name: "转场" }).click();
  await creativeTabs.getByRole("tab", { name: "特效" }).click();
  await expect.poll(() => refreshRequests).toBeGreaterThan(0);
  await expect(page.locator(".resource-grid:visible:not(.resource-preset-grid) .resource-card").first()).toBeVisible();
  finishRefresh();
  await expect(page.locator(".resource-grid:visible:not(.resource-preset-grid)")
    .getByText("Fresh catalog proof", { exact: true })).toBeVisible();
  await expect(effectSearch).toHaveValue("cutvoke.fx");
  await expect.poll(() => leftScroll.evaluate((element) => element.scrollTop)).toBe(effectScroll);
  await expect(page.locator(".resource-context:visible")).toContainText("视频片段");
  await page.unroute(/\/api\/v1\/projects\/[^/]+\/resources$/);
  const pending: Array<{ release: () => void; done: Promise<void> }> = [];
  await page.route(/\/api\/v1\/projects\/[^/]+\/resources$/, async (route) => {
    const response = await route.fetch();
    const catalog = await response.json();
    const index = pending.length;
    const effect = catalog.effects.find((item: { effectId: string }) => item.effectId.startsWith("cutvoke.fx"));
    catalog.effects.push({ ...effect, effectId: `cutvoke.fx.generation${index}`, name: `Catalog generation ${index}` });
    let release!: () => void;
    let done!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const completed = new Promise<void>((resolve) => { done = resolve; });
    pending.push({ release, done: completed });
    await gate;
    await route.fulfill({ response, json: catalog });
    done();
  });
  await apply(request, id, "caption.update", { captionId: "caption-one", text: "refresh one" });
  await expect.poll(() => pending.length).toBe(1);
  await apply(request, id, "caption.update", { captionId: "caption-one", text: "refresh two" });
  await expect.poll(() => pending.length).toBe(2);
  pending[1].release();
  const grid = page.locator(".resource-grid:visible:not(.resource-preset-grid)");
  await expect(grid.getByText("Catalog generation 1", { exact: true })).toBeVisible();
  const olderResponse = page.waitForResponse((response) => new URL(response.url()).pathname === `${apiPath(id)}/resources`);
  pending[0].release();
  await pending[0].done;
  await (await olderResponse).finished();
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
  await expect(grid.getByText("Catalog generation 0", { exact: true })).toHaveCount(0);
  await expect(grid.getByText("Catalog generation 1", { exact: true })).toBeVisible();
});

test("offline resource pack manager installs, activates, and rolls back a version", async ({ page, request }) => {
  const { name } = await createProject(request);
  const builtin = {
    packId: "cutvoke.builtin-resources", version: "1.9.0", manifestSha256: "a".repeat(64),
    resourceCount: 442, presetCount: 165, stickerCount: 277, fileCount: 2481,
    provenanceCoverage: { resourceCount: 442, licenseDeclaredCount: 442,
      sourceDeclaredCount: 64, completeCount: 64, incompleteCount: 378 },
    offlineAvailable: true, missingFiles: [], invalidFiles: [], builtin: true, active: true,
    signatureStatus: "bundled", publisherVerified: true, publisherId: "cutvoke.builtin",
    keyId: "", fingerprint: "",
  };
  const update = {
    ...builtin, packId: "cutvoke.builtin-resources", version: "1.8.1",
    manifestSha256: "b".repeat(64), builtin: false, active: false,
    signatureStatus: "unknown_publisher", publisherVerified: false,
    publisherId: "cutvoke.demo", keyId: "c".repeat(64), fingerprint: "c".repeat(64),
  };
  let trustedUpdate = update;
  let packStatus = { active: builtin, installed: [builtin], history: [], canRollback: false };
  await page.route("**/api/v1/resource-packs", (route) => route.fulfill({ json: packStatus }));
  await page.route("**/api/v1/resource-packs/install", async (route) => {
    packStatus = { ...packStatus, installed: [...packStatus.installed, update] };
    await route.fulfill({ status: 201, json: update });
  });
  await page.route("**/api/v1/resource-packs/activate", async (route) => {
    packStatus = {
      active: trustedUpdate,
      installed: packStatus.installed.map((item) => ({ ...item, active: item.version === trustedUpdate.version })),
      history: [{ packId: builtin.packId, version: builtin.version }],
      canRollback: true,
    };
    await route.fulfill({ json: packStatus });
  });
  await page.route("**/api/v1/resource-packs/trust", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.publisherId).toBe("cutvoke.demo");
    expect(body.fingerprint).toBe("c".repeat(64));
    expect(body.publicKeyPem).toContain("BEGIN PUBLIC KEY");
    const verified = { ...update, signatureStatus: "verified", publisherVerified: true };
    trustedUpdate = verified;
    packStatus = { ...packStatus, installed: packStatus.installed.map((item) =>
      item.packId === verified.packId && item.version === verified.version ? verified : item) };
    await route.fulfill({ json: packStatus });
  });
  await page.route("**/api/v1/resource-packs/rollback", async (route) => {
    packStatus = { active: builtin, installed: [builtin, trustedUpdate], history: [], canRollback: false };
    await route.fulfill({ json: packStatus });
  });

  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  const manager = page.locator(".resource-pack-manager:visible").first();
  await expect(manager.getByText("离线资源包管理 · 当前 v1.9.0")).toBeVisible();
  await manager.locator("summary").click();
  await expect(manager.getByRole("list", { name: "已安装资源包版本" }))
    .toContainText("授权元数据：许可证 442/442 · 来源 64/442 · 字段待补 378");
  await manager.getByLabel("安装离线资源包 ZIP").setInputFiles({
    name: "cutvoke-1.8.1.zip", mimeType: "application/zip", buffer: Buffer.from("test zip"),
  });
  await expect(manager.getByRole("status").filter({ hasText: "已安装 v1.8.1" })).toBeVisible();
  const updatedPack = manager.locator("li").filter({ hasText: "v1.8.1" });
  await expect(updatedPack).toContainText("发布者未受信任 · cutvoke.demo");
  await updatedPack.getByRole("button", { name: "核对并信任" }).click();
  await updatedPack.getByLabel("可信渠道给出的 SHA-256 指纹").fill("c".repeat(64));
  await updatedPack.getByLabel("Ed25519 公钥 PEM 文件").setInputFiles({
    name: "cutvoke-demo.pub", mimeType: "application/x-pem-file",
    buffer: Buffer.from("-----BEGIN PUBLIC KEY-----\nZHVtbXk=\n-----END PUBLIC KEY-----\n"),
  });
  await updatedPack.getByRole("button", { name: "验证指纹并信任" }).click();
  await expect(updatedPack).toContainText("发布者已验证 · cutvoke.demo");
  await updatedPack.getByRole("button", { name: "启用" }).click();
  await expect(manager.getByRole("status").filter({ hasText: "刷新页面后加载" })).toBeVisible();
  await manager.getByRole("button", { name: "回滚上一版本" }).click();
  await expect(manager.getByRole("status").filter({ hasText: "已回滚资源包" })).toBeVisible();
  await expect(manager.getByRole("button", { name: "回滚上一版本" })).toBeDisabled();
});

test("offline resource pack manager reports a corrupt ZIP and remains usable", async ({ page, request }) => {
  const { name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  const manager = page.locator(".resource-pack-manager:visible").first();
  await manager.locator("summary").click();
  await expect(manager.getByText(`离线资源包管理 · 当前 v${await getBuiltinPackVersion(request)}`)).toBeVisible();

  await manager.getByLabel("安装离线资源包 ZIP").setInputFiles({
    name: "broken-resources.zip", mimeType: "application/zip", buffer: Buffer.from("not a ZIP archive"),
  });
  const installError = manager.getByRole("alert").filter({ hasText: /zip/i });
  await expect(installError).toContainText(/zip/i);
  await expect(manager.getByRole("list", { name: "已安装资源包版本" }))
    .toContainText("内置资源");
  await expect(manager.getByLabel("安装离线资源包 ZIP")).toBeEnabled();
  await expect(manager.getByText(`离线资源包管理 · 当前 v${await getBuiltinPackVersion(request)}`)).toBeVisible();
});

test("signed resource registry can be added, trusted, downloaded, and removed", async ({ page, request }) => {
  const { name } = await createProject(request);
  const builtin = {
    packId: "cutvoke.builtin-resources", version: "1.9.0", manifestSha256: "a".repeat(64),
    resourceCount: 442, presetCount: 165, stickerCount: 277, fileCount: 2481,
    offlineAvailable: true, missingFiles: [], invalidFiles: [], builtin: true, active: true,
    signatureStatus: "bundled", publisherVerified: true, publisherId: "cutvoke.builtin",
    keyId: "", fingerprint: "",
  };
  const downloaded = {
    ...builtin, packId: "cutvoke.online-effects", version: "2.0.0",
    manifestSha256: "d".repeat(64), builtin: false, active: false,
    signatureStatus: "verified", publisherVerified: true,
    publisherId: "cutvoke.demo", keyId: "c".repeat(64), fingerprint: "c".repeat(64),
  };
  let packStatus = { active: builtin, installed: [builtin], history: [], canRollback: false };
  let registry = null;
  await page.route("**/api/v1/resource-packs", (route) => route.fulfill({ json: packStatus }));
  await page.route("**/api/v1/resource-packs/registries", (route) =>
    route.fulfill({ json: { registries: registry ? [registry] : [] } }));
  await page.route("**/api/v1/resource-packs/registries/add", async (route) => {
    const body = route.request().postDataJSON();
    registry = {
      url: body.url, registryId: "demo.registry", displayName: "Demo Resources",
      publisherId: "cutvoke.demo", keyId: "c".repeat(64), fingerprint: "c".repeat(64),
      signatureStatus: "unknown_publisher", publisherVerified: false,
      lastCheckedAt: "2026-09-25T00:00:00+00:00", lastError: "",
      packages: [{ packId: downloaded.packId, version: downloaded.version, name: "Demo Effects",
        url: "https://example.com/demo.zip", sha256: "e".repeat(64), sizeBytes: 1024 }],
    };
    await route.fulfill({ status: 201, json: registry });
  });
  await page.route("**/api/v1/resource-packs/registries/refresh", async (route) => {
    if (registry) registry = { ...registry, signatureStatus: "verified", publisherVerified: true };
    await route.fulfill({ json: { registries: registry ? [registry] : [] } });
  });
  await page.route("**/api/v1/resource-packs/registries/remove", async (route) => {
    registry = null;
    await route.fulfill({ json: { registries: [] } });
  });
  await page.route("**/api/v1/resource-packs/trust", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.publisherId).toBe("cutvoke.demo");
    expect(body.fingerprint).toBe("c".repeat(64));
    expect(body.publicKeyPem).toContain("BEGIN PUBLIC KEY");
    await route.fulfill({ json: packStatus });
  });
  await page.route("**/api/v1/resource-packs/registries/download", async (route) => {
    const body = route.request().postDataJSON();
    expect(body.url).toBe("https://example.com/resources.json");
    expect(body.packId).toBe(downloaded.packId);
    expect(body.version).toBe(downloaded.version);
    packStatus = { ...packStatus, installed: [...packStatus.installed.filter((item) =>
      item.packId !== downloaded.packId || item.version !== downloaded.version), downloaded] };
    await route.fulfill({ status: 201, json: {
      installed: downloaded, bytesReceived: 1024, registryId: "demo.registry",
      catalogPublisherId: "cutvoke.demo", catalogSignatureStatus: "verified",
    } });
  });

  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  const manager = page.locator(".resource-pack-manager:visible").first();
  await manager.locator("summary").click();
  await manager.getByLabel("签名目录 HTTPS 地址").fill("https://example.com/resources.json");
  await manager.getByRole("button", { name: "添加", exact: true }).click();
  const source = manager.locator(".resource-registry__list > li");
  await expect(source).toContainText("Demo Resources");
  await expect(source).toContainText("目录发布者未受信任 · cutvoke.demo");
  await source.getByRole("button", { name: "核对并信任目录发布者" }).click();
  await source.getByLabel("可信渠道给出的 SHA-256 指纹").fill("c".repeat(64));
  await source.getByLabel("Ed25519 公钥 PEM 文件").setInputFiles({
    name: "cutvoke-demo.pub", mimeType: "application/x-pem-file",
    buffer: Buffer.from("-----BEGIN PUBLIC KEY-----\nZHVtbXk=\n-----END PUBLIC KEY-----\n"),
  });
  await source.getByRole("button", { name: "验证指纹并信任目录发布者" }).click();
  await expect(source).toContainText("目录签名已验证 · cutvoke.demo");
  await source.getByRole("button", { name: "下载并安装" }).click();
  await expect(manager.getByRole("status").filter({ hasText: "已下载并安装「cutvoke.online-effects」v2.0.0" })).toBeVisible();
  const installed = manager.getByRole("list", { name: "已安装资源包版本" }).locator("li")
    .filter({ hasText: "cutvoke.online-effects" });
  await expect(installed).toContainText("发布者已验证 · cutvoke.demo");
  await expect(installed.getByRole("button", { name: "启用" })).toBeEnabled();
  await expect(manager.getByRole("button", { name: "回滚上一版本" })).toBeDisabled();
  await source.getByRole("button", { name: "移除" }).click();
  await expect(manager.locator(".resource-registry")).toContainText("尚未添加在线资源目录");
  await expect(installed).toBeVisible();
});

test("music library classifies imported audio as background music or sound effect", async ({ page, request }) => {
  const { name } = await createProject(request);
  const importedAudio = {
    assetId: "audio-role-demo",
    path: "C:/cutvoke/media/field-recording.wav",
    name: "field-recording.wav",
    size: 128,
    kind: "audio",
    duration: 4.5,
    hasVideo: false,
    hasAudio: true,
    width: null,
    height: null,
    createdAt: "2026-09-25T00:00:00.000Z",
    available: true,
    builtin: false,
    audioRole: "unclassified",
  };
  const builtinAudio = {
    assetId: "builtin_audio_ambient_pad",
    path: "C:/cutvoke/assets/audio/ambient_pad.wav",
    name: "内置·环境氛围",
    size: 256,
    kind: "audio",
    duration: 12,
    hasVideo: false,
    hasAudio: true,
    width: null,
    height: null,
    createdAt: "2026-09-25T00:00:00.000Z",
    available: true,
    builtin: true,
    audioRole: "music",
  };
  let savedRole = "unclassified";
  let failNextAudition = true;
  const auditionFixture = readFileSync(bgmFixture);
  await page.route("**/api/v1/assets", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ assets: [
      { ...importedAudio, audioRole: savedRole }, builtinAudio,
    ] }),
  }));
  await page.route("**/api/v1/assets/audio-role-demo/media", async (route) => {
    if (failNextAudition) {
      failNextAudition = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "模拟音频服务暂时不可用" } }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "audio/wav", body: auditionFixture });
  });
  await page.route("**/api/v1/assets/audio-role-demo", async (route) => {
    if (route.request().method() !== "PATCH") return route.continue();
    savedRole = route.request().postDataJSON().audioRole;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...importedAudio, audioRole: savedRole }),
    });
  });
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "音乐" }).click();

  await page.getByRole("button", { name: "试听 field-recording.wav" }).click();
  const auditionError = page.getByRole("alert").filter({ hasText: "音频无法播放" });
  await expect(auditionError).toBeVisible();
  await auditionError.getByRole("button", { name: "重试试听" }).click();
  await expect(auditionError).toHaveCount(0);
  await expect.poll(async () => page.locator(".media-audition audio")
    .evaluate((audio) => (audio as HTMLAudioElement).readyState)).toBeGreaterThanOrEqual(1);

  const role = page.getByRole("combobox", { name: "音频用途 field-recording.wav" });
  await expect(role).toHaveValue("unclassified");
  await page.getByRole("group", { name: "音频用途过滤" }).getByRole("button", { name: "未分类" }).click();
  await expect(page.locator(".media-item")).toHaveCount(1);
  await page.getByRole("group", { name: "音频用途过滤" }).getByRole("button", { name: "全部用途" }).click();
  await role.selectOption("sound_effect");
  await expect(role).toHaveValue("sound_effect");
  expect(savedRole).toBe("sound_effect");
  await page.getByRole("group", { name: "音频用途过滤" }).getByRole("button", { name: "音效" }).click();
  await expect(page.locator(".media-item")).toHaveCount(1);

  await page.reload();
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "音乐" }).click();
  await expect(page.getByRole("combobox", { name: "音频用途 field-recording.wav" }))
    .toHaveValue("sound_effect");
  const sourceTabs = page.getByRole("tablist", { name: "素材来源" });
  await sourceTabs.getByRole("tab", { name: /内置资源/ }).click();
  await page.getByRole("group", { name: "音频用途过滤" }).getByRole("button", { name: "背景音乐" }).click();
  const builtinCard = page.locator(".media-item").filter({ hasText: "内置·环境氛围" });
  await expect(builtinCard).toHaveCount(1);
  await expect(builtinCard).toContainText("来源 CutVoke 合成 · MIT");
  await expect(page.getByRole("combobox", { name: "音频用途 内置·环境氛围" })).toBeDisabled();
  await sourceTabs.getByRole("tab", { name: /我的素材/ }).click();
  await page.getByRole("group", { name: "音频用途过滤" }).getByRole("button", { name: "背景音乐" }).click();
  await expect(page.locator(".media-item")).toHaveCount(0);
  await expect(page.getByText("没有匹配的音乐或音效。试试调整搜索词或使用状态筛选。"))
    .toBeVisible();
});

test("preset motion sample explains preview failure and retries the bundled video", async ({ page, request }) => {
  const { name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "特效" }).click();
  const previewButton = page.locator(".resource-preset-card__preview").first();
  await expect(previewButton).toBeVisible();
  const previewRoute = "**/api/v1/presets/*/preview";
  await page.route(previewRoute, (route) => route.abort());
  await previewButton.click();

  const dialog = page.getByRole("dialog", { name: /动态样片/ });
  await expect(dialog.locator(".resource-sample-dialog__head small"))
    .toContainText("src/cutvoke/core/builtin_presets.json");
  await expect(dialog.getByRole("alert")).toContainText("动态样片加载失败");
  const retry = dialog.getByRole("button", { name: "重试动态样片" });
  await expect(retry).toBeVisible();
  await page.unroute(previewRoute);
  await retry.click();
  await expect(dialog.getByRole("button", { name: "重试动态样片" })).toHaveCount(0);
  await expect.poll(async () => dialog.locator("video").evaluate((video) =>
    (video as HTMLVideoElement).readyState)).toBeGreaterThanOrEqual(2);
  await dialog.getByRole("button", { name: "关闭动态样片" }).click();
});

test("botanical sticker atlas category previews and inserts a saved overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  const creativeTabs = page.getByRole("tablist", { name: "创作域" });
  const mediaTab = creativeTabs.getByRole("tab", { name: "素材" });
  const musicTab = creativeTabs.getByRole("tab", { name: "音乐" });
  await mediaTab.focus();
  await mediaTab.press("ArrowRight");
  await expect(musicTab).toBeFocused();
  await expect(musicTab).toHaveAttribute("aria-selected", "true");
  await mediaTab.click();

  const sourceTabs = page.getByRole("tablist", { name: "素材来源" });
  const mineTab = sourceTabs.getByRole("tab", { name: /我的素材/ });
  const builtinTab = sourceTabs.getByRole("tab", { name: /内置资源/ });
  await expect(sourceTabs.getByRole("tab")).toHaveCount(2);
  await expect(mineTab).toHaveAttribute("tabindex", "0");
  await mineTab.focus();
  await mineTab.press("ArrowRight");
  await expect(builtinTab).toBeFocused();
  await expect(builtinTab).toHaveAttribute("aria-selected", "true");
  await creativeTabs.getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("花草");
  await expect(page.getByText("16 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "樱花枝" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const stickerScreenshot = resolve(process.cwd(), "../../output/playwright/botanical-sticker-library-20260924.png");
  mkdirSync(dirname(stickerScreenshot), { recursive: true });
  await page.screenshot({ path: stickerScreenshot, fullPage: false });
  const preview = card.locator("video");
  await expect(preview).toBeVisible();
  await preview.hover();
  await expect.poll(() => preview.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0.1);

  await card.getByRole("button", { name: "添加贴纸 樱花枝" }).click();
  await expect(page.getByText("已添加贴纸「樱花枝」到独立叠加轨")).toBeVisible();
  const hasSticker = async () => (await readProject(request, id)).sequence.tracks
    .flatMap((track) => track.clips)
    .some((clip) => clip.assetRef.sourcePath.includes("botanical_cherry_blossom.png"));
  await expect.poll(hasSticker).toBe(true);

  await page.reload();
  await openProject(page, name);
  expect(await hasSticker()).toBe(true);
});

test("travel-vlog sticker atlas previews and inserts a saved overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("旅行手帐");
  await expect(page.getByText("16 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "旅行路线地图" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const preview = card.locator("video");
  await expect(preview).toBeVisible();
  await preview.hover();
  await expect.poll(() => preview.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0.1);

  await card.getByRole("button", { name: "添加贴纸 旅行路线地图" }).click();
  await expect(page.getByText("已添加贴纸「旅行路线地图」到独立叠加轨")).toBeVisible();
  const hasSticker = async () => (await readProject(request, id)).sequence.tracks
    .some((track) => track.role === "sticker" && track.clips
      .some((clip) => clip.assetRef.sourcePath.includes("travel_map_route.png")));
  await expect.poll(hasSticker).toBe(true);

  await page.reload();
  await openProject(page, name);
  expect(await hasSticker()).toBe(true);
});

test("food-and-drink sticker category previews and inserts a saved overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("美食饮品");
  await expect(page.getByText("16 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "草莓奶油蛋糕" });
  await expect(card).toHaveCount(1);
  const preview = card.locator("video");
  await expect(preview).toBeVisible();
  await preview.hover();
  await expect.poll(() => preview.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0.1);

  await card.getByRole("button", { name: "添加贴纸 草莓奶油蛋糕" }).click();
  await expect(page.getByText("已添加贴纸「草莓奶油蛋糕」到独立叠加轨")).toBeVisible();
  const hasSticker = async () => (await readProject(request, id)).sequence.tracks
    .some((track) => track.role === "sticker" && track.clips
      .some((clip) => clip.assetRef.sourcePath.includes("food_strawberry_cake.png")));
  await expect.poll(hasSticker).toBe(true);

  await page.reload();
  await openProject(page, name);
  expect(await hasSticker()).toBe(true);
  const stickerClip = (await readProject(request, id)).sequence.tracks
    .find((track) => track.role === "sticker")?.clips
    .find((clip) => clip.assetRef.sourcePath.includes("food_strawberry_cake.png"));
  expect(stickerClip).toBeTruthy();
  await apply(request, id, "effect.remove", {
    clipId: stickerClip!.id, effectId: "cutvoke.transform",
  });
  await page.reload();
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "草莓奶油蛋糕" }).click();
  const stickerTransform = page.getByRole("region", { name: "贴纸变换" });
  await expect(stickerTransform.getByRole("button", { name: "应用贴纸变换" })).toBeEnabled();
  await stickerTransform.getByRole("spinbutton", { name: "缩放倍率" }).fill("0.42");
  await stickerTransform.getByRole("button", { name: "应用贴纸变换" }).click();
  await expect.poll(async () => {
    const updated = (await readProject(request, id)).sequence.tracks
      .find((track) => track.role === "sticker")?.clips
      .find((clip) => clip.id === stickerClip!.id);
    return updated?.effects.find((effect) => effect.effectId === "cutvoke.transform")?.params?.scale;
  }).toBe(0.42);
});

test("manga-motion atlas offers the expanded preset set and inserts a full-frame sticker", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("漫画动感");
  await expect(page.getByText("33 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "黑白放射冲击线" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("manga-motion-atlas-v2-20260926.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 黑白放射冲击线" }).click();
  await expect(page.getByText("已添加贴纸「黑白放射冲击线」到独立叠加轨")).toBeVisible();
  const project = await readProject(request, id);
  const stickerClip = project.sequence.tracks.flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("manga_v2_bw_speed_burst.png"));
  expect(stickerClip).toBeTruthy();
  expect(stickerClip?.assetRef.resourceRef).toMatchObject({
    resourceId: "cutvoke.sticker.manga_v2_bw_speed_burst",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
  expect(stickerClip?.effects.find((effect) => effect.effectId === "cutvoke.transform")
    ?.params?.scale).toBe(0.92);
});

test("pixel-game effect atlas previews, exposes provenance, and inserts at the default scale", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("特效贴图");
  await expect(page.getByText("95 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "冰蓝像素护盾" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("pixel-game-effect-atlas-20260926.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 冰蓝像素护盾" }).click();
  await expect(page.getByText("已添加贴纸「冰蓝像素护盾」到独立叠加轨")).toBeVisible();
  const project = await readProject(request, id);
  const stickerClip = project.sequence.tracks.flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("fxoverlay_pixel_shield.png"));
  expect(stickerClip).toBeTruthy();
  expect(stickerClip?.assetRef.resourceRef).toMatchObject({
    resourceId: "cutvoke.sticker.fxoverlay_pixel_shield",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
  expect(stickerClip?.effects.find((effect) => effect.effectId === "cutvoke.transform")
    ?.params?.scale).toBe(0.92);
});

test("glass/liquid refraction atlas previews, exposes provenance, and inserts as a saved overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("特效贴图");
  await expect(page.getByText("95 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "青碧水纹焦散" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("glass-liquid-refraction-atlas-20260927.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 青碧水纹焦散" }).click();
  await expect(page.getByText("已添加贴纸「青碧水纹焦散」到独立叠加轨")).toBeVisible();
  const project = await readProject(request, id);
  const stickerClip = project.sequence.tracks.flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("fxoverlay_refraction_caustics_turquoise.png"));
  expect(stickerClip).toBeTruthy();
  expect(stickerClip?.assetRef.resourceRef).toMatchObject({
    resourceId: "cutvoke.sticker.fxoverlay_refraction_caustics_turquoise",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
  expect(stickerClip?.effects.find((effect) => effect.effectId === "cutvoke.transform")
    ?.params?.scale).toBe(0.92);
});

test("music-rhythm sticker atlas previews, exposes provenance, and inserts as an overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("音乐节奏");
  await expect(page.getByText("16 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "彩虹频谱光环" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("music-rhythm-overlay-atlas-20260926.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 彩虹频谱光环" }).click();
  await expect(page.getByText("已添加贴纸「彩虹频谱光环」到独立叠加轨")).toBeVisible();
  const project = await readProject(request, id);
  const stickerClip = project.sequence.tracks.flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("audiofx_spectrum_halo_rainbow.png"));
  expect(stickerClip).toBeTruthy();
  expect(stickerClip?.assetRef.resourceRef).toMatchObject({
    resourceId: "cutvoke.sticker.audiofx_spectrum_halo_rainbow",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
  expect(stickerClip?.effects.find((effect) => effect.effectId === "cutvoke.transform")
    ?.params?.scale).toBe(0.92);
});

test("weather-atmosphere atlas previews, exposes provenance, and inserts as an overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("特效贴图");
  await expect(page.getByText("95 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "密集雨幕" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("weather-atmosphere-overlay-atlas-20260926.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 密集雨幕" }).click();
  await expect(page.getByText("已添加贴纸「密集雨幕」到独立叠加轨")).toBeVisible();
  const project = await readProject(request, id);
  const stickerClip = project.sequence.tracks.flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("weatherfx_heavy_rain.png"));
  expect(stickerClip).toBeTruthy();
  expect(stickerClip?.assetRef.resourceRef).toMatchObject({
    resourceId: "cutvoke.sticker.weatherfx_heavy_rain",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
  expect(stickerClip?.effects.find((effect) => effect.effectId === "cutvoke.transform")
    ?.params?.scale).toBe(1.1);
});

test("East Asian ink atlas previews, exposes provenance, and inserts as an overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("特效贴图");
  await expect(page.getByText("95 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "朱红飞白笔势" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("east-asian-ink-motion-overlay-atlas-20260926.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 朱红飞白笔势" }).click();
  await expect(page.getByText("已添加贴纸「朱红飞白笔势」到独立叠加轨")).toBeVisible();
  const project = await readProject(request, id);
  const stickerClip = project.sequence.tracks.flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("inkfx_vermilion_slash.png"));
  expect(stickerClip).toBeTruthy();
  expect(stickerClip?.assetRef.resourceRef).toMatchObject({
    resourceId: "cutvoke.sticker.inkfx_vermilion_slash",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
  expect(stickerClip?.effects.find((effect) => effect.effectId === "cutvoke.transform")
    ?.params?.scale).toBe(1.1);
});

test("cinematic optical atlas previews, exposes provenance, and inserts as an overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("镜头光效");
  await expect(page.getByText("32 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "玫瑰色镜头光斑" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("cinematic-optical-overlay-atlas-20260926.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 玫瑰色镜头光斑" }).click();
  await expect(page.getByText("已添加贴纸「玫瑰色镜头光斑」到独立叠加轨")).toBeVisible();
  const project = await readProject(request, id);
  const stickerClip = project.sequence.tracks.flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("cinematic_optical_flare_rose.png"));
  expect(stickerClip).toBeTruthy();
  expect(stickerClip?.assetRef.resourceRef).toMatchObject({
    resourceId: "cutvoke.sticker.cinematic_optical_flare_rose",
    packId: "cutvoke.builtin-resources",
    packVersion: await getBuiltinPackVersion(request),
    resourceVersion: "1.0.0",
    sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
  });
});

test("main-track auto-fit is independent from magnetism and leaves BGM independent", async ({ page, request }) => {
  const { id, name, sources } = await createProject(request);
  for (let index = 3; index < 6; index += 1) {
    await apply(request, id, "clip.insert", {
      trackId: "v1", clipId: `color-${index}`, sourcePath: sources[index % sources.length],
      timelineStart: { num: index * 5, den: 1 },
      timelineEnd: { num: (index + 1) * 5, den: 1 },
    });
  }
  await apply(request, id, "track.add", { trackId: "a1", kind: "audio" });
  await apply(request, id, "clip.insert", {
    trackId: "a1", clipId: "bgm", sourcePath: bgmFixture,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 5, den: 1 },
  });
  await openProject(page, name);
  await page.getByRole("button", { name: "磁吸开关" }).click();
  const fitToggle = page.getByRole("button", { name: "主轨自动贴合开关" });
  await expect(fitToggle).toHaveAttribute("aria-pressed", "true");

  const dragFirstClipToEnd = async () => {
    const clip = page.locator(".timeline-track__lane[data-track-id='v1'] .clip-block").first();
    const lane = page.locator(".timeline-track__lane[data-track-id='v1']");
    const clipBox = await clip.boundingBox();
    const laneBox = await lane.boundingBox();
    expect(clipBox && laneBox).toBeTruthy();
    const pixelsPerSecond = clipBox!.width / 5;
    const sourceX = clipBox!.x + clipBox!.width / 2;
    const destinationX = sourceX + 30 * pixelsPerSecond;
    const y = laneBox!.y + laneBox!.height / 2;
    await page.mouse.move(sourceX, y);
    await page.mouse.down();
    await page.mouse.move(destinationX, y, { steps: 12 });
    await expect(page.locator(".clip-block--ghost")).toBeVisible();
    if (await fitToggle.getAttribute("aria-pressed") === "true") {
      await expect(page.locator(".drag-time-tip")).toContainText("主轨贴合");
    }
    await page.mouse.up();
  };

  await dragFirstClipToEnd();
  const fitted = await readProject(request, id);
  const fittedMain = fitted.sequence.tracks.find((track) => track.id === "v1")!.clips;
  expect(seconds(fittedMain.find((clip) => clip.id === "color-0")!.timelineStart)).toBe(25);
  expect(seconds(fittedMain.find((clip) => clip.id === "color-1")!.timelineStart)).toBe(0);
  expect(seconds(fittedMain.find((clip) => clip.id === "color-2")!.timelineStart)).toBe(5);
  expect(seconds(fittedMain.find((clip) => clip.id === "color-5")!.timelineStart)).toBe(20);
  expect(seconds(fitted.sequence.tracks.find((track) => track.id === "a1")!.clips[0].timelineStart)).toBe(0);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks.find((track) => track.id === "v1")!.clips
      .sort((a, b) => seconds(a.timelineStart) - seconds(b.timelineStart))
      .map((clip) => seconds(clip.timelineStart));
  }).toEqual([0, 5, 10, 15, 20, 25]);
  await fitToggle.click();
  await expect(fitToggle).toHaveAttribute("aria-pressed", "false");
  await dragFirstClipToEnd();
  const gapped = await readProject(request, id);
  const gappedMain = gapped.sequence.tracks.find((track) => track.id === "v1")!.clips;
  expect(seconds(gappedMain.find((clip) => clip.id === "color-0")!.timelineStart)).toBeCloseTo(30, 0);
  expect(seconds(gappedMain.find((clip) => clip.id === "color-1")!.timelineStart)).toBe(5);
  expect(seconds(gappedMain.find((clip) => clip.id === "color-2")!.timelineStart)).toBe(10);
  expect(seconds(gappedMain.find((clip) => clip.id === "color-5")!.timelineStart)).toBe(25);
  expect(seconds(gapped.sequence.tracks.find((track) => track.id === "a1")!.clips[0].timelineStart)).toBe(0);
  await page.reload();
  const reopened = await readProject(request, id);
  expect(seconds(reopened.sequence.tracks.find((track) => track.id === "v1")!.clips
    .find((clip) => clip.id === "color-0")!.timelineStart)).toBeCloseTo(30, 0);
});

test("six-clip high-density timeline locates a seam and trims to an exact frame", async ({ page, request }) => {
  const fixture = await readProject(request, "e2e-fixture");
  const sources = fixture.sequence.tracks[0].clips.map((clip) => clip.assetRef.sourcePath);
  const id = `timeline-density-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `高密度时间线 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 15 },
  });
  expect(created.status()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  await apply(request, id, "track.add", { trackId: "music", kind: "audio" });
  for (let index = 0; index < 6; index += 1) {
    await apply(request, id, "clip.insert", {
      trackId: "v1", clipId: `shot-${index + 1}`, sourcePath: sources[index % sources.length],
      timelineStart: { num: index * 2, den: 1 },
      timelineEnd: { num: index * 2 + 2, den: 1 },
    });
  }
  await apply(request, id, "clip.insert", {
    trackId: "music", clipId: "bgm", sourcePath: bgmFixture,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 12, den: 1 },
  });
  await apply(request, id, "caption.add", {
    captionId: "caption-third-shot", text: "第三段字幕",
    start: { num: 4, den: 1 }, end: { num: 6, den: 1 },
  });
  await apply(request, id, "effect.add", {
    clipId: "shot-3", effectId: "cutvoke.fx.vibrance", params: { intensity: 0.25 },
  });
  await page.setViewportSize({ width: 760, height: 900 });
  await openProject(page, name);

  const mainLane = page.locator(".timeline-track__lane[data-track-id='v1']");
  const musicLane = page.locator(".timeline-track__lane[data-track-id='music']");
  const captionLane = page.getByTestId("caption-timeline-track");
  const caption = page.getByTestId("caption-timeline-clip");
  await expect(mainLane.locator(".clip-block")).toHaveCount(6);
  await expect(musicLane.locator(".clip-block")).toHaveCount(1);
  await expect(page.getByTestId("effect-segment")).toBeVisible();
  await expect(captionLane).toBeVisible();
  await expect(caption).toContainText("第三段字幕");
  await caption.click();
  await expect(caption).toHaveAttribute("aria-pressed", "true");
  const ruler = page.getByRole("slider", { name: "时间线播放头" });
  await expect(ruler).toHaveAttribute("aria-valuenow", "4");

  const startedAt = Date.now();
  await page.getByRole("button", { name: "在 6.00 秒的片段接缝添加转场" }).click();
  await expect(ruler).toHaveAttribute("aria-valuenow", "6");
  const thirdClip = mainLane.locator(".clip-block").nth(2);
  await thirdClip.click();
  const zoomIn = page.locator(".timeline__zoom button[aria-label='放大']");
  for (let level = 0; level < 7 && await zoomIn.isEnabled(); level += 1) {
    await zoomIn.click();
  }
  const trimHandle = thirdClip.locator(".clip-block__drag--r");
  const handleBox = await trimHandle.boundingBox();
  expect(handleBox).toBeTruthy();
  const centerY = handleBox!.y + handleBox!.height / 2;
  const centerX = handleBox!.x + handleBox!.width / 2;
  await page.mouse.move(centerX, centerY);
  await page.mouse.down();
  // Project frame 88 at 15fps is 5.8667s, two frames before the 6s seam.
  await page.mouse.move(centerX - (2 / 15) * 320, centerY, { steps: 8 });
  const trimReadout = page.getByTestId("clip-trim-readout");
  await expect(trimReadout).toHaveAttribute("data-edge-frame", "88");
  await expect(trimReadout).toHaveAttribute("data-duration-frames", "28");
  await expect(trimReadout).toContainText("0:05.867");
  await page.mouse.up();

  const shotEnd = async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks.find((track) => track.id === "v1")!
      .clips.find((clip) => clip.id === "shot-3")!.timelineEnd;
  };
  await expect.poll(shotEnd).toEqual({ num: "88", den: "15" });
  await expect(thirdClip).toHaveAttribute("aria-label", /1\.9 秒/);
  expect(Date.now() - startedAt).toBeLessThan(10_000);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(shotEnd).toEqual({ num: "6", den: "1" });
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(shotEnd).toEqual({ num: "88", den: "15" });

  const zoomOut = page.locator(".timeline__zoom button[aria-label='缩小']");
  for (let level = 0; level < 4 && await zoomOut.isEnabled(); level += 1) {
    await zoomOut.click();
  }
  await thirdClip.click();
  const lowZoomBox = await thirdClip.boundingBox();
  expect(lowZoomBox).toBeTruthy();
  const lowZoomHandle = thirdClip.locator(".clip-block__drag--r");
  const lowHandleBox = await lowZoomHandle.boundingBox();
  expect(lowHandleBox).toBeTruthy();
  const lowStartX = lowHandleBox!.x + lowHandleBox!.width / 2;
  const lowCenterY = lowHandleBox!.y + lowHandleBox!.height / 2;
  const lowZoomPixelsPerSecond = lowZoomBox!.width / 2;
  const fourFramesAtLowZoom = (4 / 15) * lowZoomPixelsPerSecond;
  expect(fourFramesAtLowZoom).toBeGreaterThanOrEqual(4);
  await page.mouse.move(lowStartX, lowCenterY);
  await page.mouse.down();
  await page.mouse.move(lowStartX - fourFramesAtLowZoom, lowCenterY, { steps: 4 });
  await expect(trimReadout).toHaveAttribute("data-edge-frame", "84");
  await expect(trimReadout).toHaveAttribute("data-duration-frames", "24");
  await page.mouse.up();
  await expect.poll(async () => seconds(await shotEnd())).toBeCloseTo(84 / 15, 6);
});

test("dropping library media over a clip inserts at its boundary without splitting it", async ({ page, request }) => {
  const { id, name, sources } = await createProject(request);
  await openProject(page, name);
  const lane = page.locator(".timeline-track__lane[data-track-id='v1']");
  const green = page.locator(".clip-block").filter({ hasText: "green.mp4" });
  const box = await green.boundingBox();
  expect(box).toBeTruthy();
  const dragData = await page.evaluateHandle((sourcePath) => {
    const transfer = new DataTransfer();
    transfer.setData("text/cutvoke-media", JSON.stringify({ sourcePath, kind: "video" }));
    return transfer;
  }, sources[0]);
  const atX = box!.x + box!.width * 0.75;
  const atY = box!.y + box!.height / 2;
  await lane.dispatchEvent("dragover", { dataTransfer: dragData, clientX: atX, clientY: atY });
  await expect(page.locator(".timeline-track__insert-target")).toBeVisible();
  await lane.dispatchEvent("drop", { dataTransfer: dragData, clientX: atX, clientY: atY });
  await expect.poll(async () => (await readProject(request, id)).sequence.tracks[0].clips.length).toBe(4);
  const project = await readProject(request, id);
  const clips = project.sequence.tracks[0].clips;
  const greenAfter = clips.find((clip) => clip.id === "color-1")!;
  const blueAfter = clips.find((clip) => clip.id === "color-2")!;
  expect(greenAfter.timelineStart).toEqual({ num: "5", den: "1" });
  expect(greenAfter.timelineEnd).toEqual({ num: "10", den: "1" });
  expect(blueAfter.timelineStart).toEqual({ num: "15", den: "1" });
});

test("buffering keeps the video position and size fixed", async ({ page, request }) => {
  const { name } = await createProject(request);
  await openProject(page, name);
  const video = page.locator("video.player__video");
  await expect.poll(() => video.evaluate((element) =>
    (element as HTMLVideoElement).readyState)).toBeGreaterThan(1);
  const before = await video.boundingBox();
  expect(before).toBeTruthy();
  // Exercise the real waiting/canplay handlers even on a fully cached window.
  await page.getByRole("button", { name: "播放", exact: true }).click();
  // Native playing/canplay can otherwise arrive after the synthetic waiting
  // event and immediately clear it. Wait for the play promise to settle first.
  await video.evaluate((element) => (element as HTMLVideoElement).play());
  await video.dispatchEvent("waiting");
  await expect(page.getByText("播放缓冲中…", { exact: true })).toBeVisible();
  const waiting = await video.boundingBox();
  await video.dispatchEvent("canplay");
  await expect(page.getByText("播放缓冲中…", { exact: true })).toBeHidden();
  const after = await video.boundingBox();
  for (const bounds of [waiting, after]) {
    expect(bounds).toBeTruthy();
    for (const key of ["x", "y", "width", "height"] as const) {
      expect(Math.abs(bounds![key] - before![key])).toBeLessThan(.1);
    }
  }
});

test("prepared playback follows the ruler, stops at the end, and survives an Agent subtitle edit", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  const ruler = page.getByRole("slider", { name: "时间线播放头" });
  const bounds = await ruler.boundingBox();
  expect(bounds).toBeTruthy();
  const video = page.locator("video.player__video");
  await page.mouse.click(bounds!.x + 7.6 * 40, bounds!.y + bounds!.height / 2);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow"))).toBeGreaterThan(8.25);
  await expect(video).toHaveAttribute("src", /preview-jobs\/[^/]+\/media/);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await page.mouse.click(bounds!.x + 9 * 40, bounds!.y + bounds!.height / 2);
  await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow"))).toBeCloseTo(9, 0);
  await expect(video).toHaveAttribute("src", /preview-jobs\/[^/]+\/media/);
  await expect.poll(() => video.evaluate((element) => (element as HTMLVideoElement).readyState)).toBeGreaterThan(1);
  const color = await video.evaluate((element) => {
    const canvas = document.createElement("canvas");
    canvas.width = 1; canvas.height = 1;
    const context = canvas.getContext("2d")!;
    context.drawImage(element as HTMLVideoElement, 80, 45, 1, 1, 0, 0, 1, 1);
    return [...context.getImageData(0, 0, 1, 1).data];
  });
  expect(color[1]).toBeGreaterThan(color[0] * 1.4);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await page.mouse.click(bounds!.x + 10 * 40, bounds!.y + bounds!.height / 2);
  await page.waitForTimeout(350);
  expect(Number(await ruler.getAttribute("aria-valuenow"))).toBeGreaterThan(9.8);
  expect(await video.evaluate((element) => (element as HTMLVideoElement).currentTime)).toBeGreaterThan(9.8);
  await page.getByRole("button", { name: "暂停", exact: true }).click();

  const before = await video.getAttribute("src");
  await video.evaluate((element) => { (window as Window & { oldVideo?: Element }).oldVideo = element; });
  const lock = await request.post(`${apiPath(id)}/edit-lock`, {
    data: { owner: "subtitle-agent", ttlSeconds: 30 },
  });
  expect(lock.ok()).toBeTruthy();
  const leaseId = (await lock.json()).lease.leaseId as string;
  const current = await readProject(request, id);
  const patch = await request.post(`${apiPath(id)}/commands`, {
    data: {
      type: "caption.patch", expectedRevision: current.revision,
      editLeaseId: leaseId, actor: { kind: "agent", id: "subtitle-agent" },
      payload: { updates: [{ captionId: "caption-one", text: "AI 已修正" }] },
    },
  });
  expect(patch.ok(), await patch.text()).toBeTruthy();
  await expect(page.getByText("Agent 正在编辑", { exact: true })).toBeVisible();
  await expect.poll(async () => video.evaluate((element) => element ===
    (window as Window & { oldVideo?: Element }).oldVideo)).toBe(true);
  expect(await video.getAttribute("src")).toBe(before);
  await page.keyboard.press("Control+z");
  expect((await readProject(request, id)).revision).toBe((await patch.json()).revision);
  const release = await request.post(`${apiPath(id)}/edit-lock/release`, { data: { leaseId } });
  expect(release.ok()).toBeTruthy();
  await expect(page.getByText("Agent 正在编辑", { exact: true })).toBeHidden();

  await ruler.focus();
  await ruler.press("End");
  await expect(ruler).toHaveAttribute("aria-valuenow", "15");
  await page.getByRole("button", { name: "停止" }).click();
  await page.mouse.click(bounds!.x + 14.8 * 40, bounds!.y + bounds!.height / 2);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeVisible({ timeout: 8_000 });
  await expect(ruler).toHaveAttribute("aria-valuenow", "15");

  await page.reload();
  if (await page.getByRole("button", { name: `打开工程 ${name}` }).isVisible()) {
    await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  }
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕" }).click();
  await expect(page.getByRole("button", { name: "选择字幕 caption-one" }))
    .toContainText("AI 已修正");
});

test("player explains missing media and lets the user retry a failed preview", async ({ page, request }) => {
  let frameRequests = 0;
  // Exercise the supported fallback used when a server has no preparation API.
  await page.route("**/projects/*/preview-jobs", route => route.fulfill({ status: 404,
    contentType: "application/json", body: JSON.stringify({ error: { code: "NOT_FOUND" } }) }));
  await page.route(/preview-window/, async (route) => route.fulfill({
    status: 422,
    contentType: "application/json",
    body: JSON.stringify({ error: {
      code: "PREVIEW_FAILED",
      message: "source file missing for clip green: C:\\lost\\green.mp4",
    } }),
  }));
  await page.route(/preview-frame/, async (route) => {
    frameRequests += 1;
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ error: {
        code: "PREVIEW_FAILED",
        message: "source file missing for clip green: C:\\lost\\green.mp4",
      } }),
    });
  });

  const { name } = await createProject(request);
  await openProject(page, name);
  const error = page.getByRole("alert");
  await expect(error).toContainText("素材文件不存在");
  await expect(error).toContainText("C:\\lost\\green.mp4");

  const requestsBeforeRetry = frameRequests;
  await page.getByRole("button", { name: "重试预览" }).click();
  await expect.poll(() => frameRequests).toBeGreaterThan(requestsBeforeRetry);
});

test("relinking a missing source restores the current preview window", async ({ page, request }) => {
  const assetName = `e2e-relink-${Date.now()}.png`;
  const upload = await request.post(`/api/v1/assets?name=${encodeURIComponent(assetName)}`, {
    headers: { "Content-Type": "application/octet-stream" },
    data: readFileSync(imageFixture),
  });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  const asset = await upload.json() as { assetId: string; path: string };
  const id = `relink-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `回归 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 15 },
  });
  expect(created.status()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  await apply(request, id, "clip.insert", {
    trackId: "v1", clipId: "relinked-image", sourcePath: asset.path,
    assetId: asset.assetId, timelineStart: { num: 0, den: 1 },
    timelineEnd: { num: 4, den: 1 },
  });
  const createdProject = await readProject(request, id);
  expect(createdProject.sequence.tracks.map((track) => ({
    id: track.id,
    kind: track.kind,
    clipIds: track.clips.map((clip) => clip.id),
  }))).toEqual([{ id: "v1", kind: "video", clipIds: ["relinked-image"] }]);
  unlinkSync(asset.path);

  try {
    await openProject(page, name, true);
    const previewError = page.locator(".player__preview-error");
    await expect(previewError).toContainText("素材文件不存在");

    const [chooser] = await Promise.all([
      page.waitForEvent("filechooser"),
      page.getByRole("button", { name: `重新链接 ${assetName}` }).click(),
    ]);
    await chooser.setFiles(imageFixture);
    await expect(page.getByRole("contentinfo")).toContainText("已重新链接 decor_sparkles.png");

    const video = page.locator("video.player__video");
    await expect(video).toHaveAttribute("data-preview-source", /restore=1/);
    await expect.poll(() => video.evaluate((element) =>
      (element as HTMLVideoElement).readyState)).toBeGreaterThan(1);
    await expect(previewError).toHaveCount(0);
    expect(statSync(asset.path).isFile()).toBe(true);
    const listed = await request.get("/api/v1/assets");
    const assets = (await listed.json()).assets as Array<{ assetId: string; available: boolean }>;
    expect(assets.find((item) => item.assetId === asset.assetId)?.available).toBe(true);
  } finally {
    try {
      statSync(asset.path);
    } catch {
      await request.post(
        `/api/v1/assets/${encodeURIComponent(asset.assetId)}/relink?name=${encodeURIComponent(assetName)}`,
        { headers: { "Content-Type": "application/octet-stream" }, data: readFileSync(imageFixture) },
      );
    }
  }
});

test("subtitle word highlighting composes with typewriter entrance and survives reload", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "caption.update", {
    captionId: "caption-one",
    text: "第一句 第二句",
    words: [
      { text: "第一句", start: { num: "1", den: "1" }, end: { num: "2", den: "1" } },
      { text: "第二句", start: { num: "2", den: "1" }, end: { num: "3", den: "1" } },
    ],
    wordHighlightColor: "#ff0000",
  });
  await openProject(page, name);
  const captionTab = page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕" });
  await captionTab.click();
  await page.getByRole("button", { name: "选择字幕 caption-one" }).click();

  const highlight = page.getByRole("button", { name: "逐词高亮" });
  await expect(highlight).toBeEnabled();
  await expect(highlight).toHaveAttribute("aria-pressed", "true");
  await page.getByLabel("字幕入场出场动画").selectOption("typewriter-mid");
  await expect.poll(async () => {
    const caption = (await readProject(request, id)).sequence.captions[0];
    return [caption.animInStyle, caption.wordHighlightColor];
  }).toEqual(["typewriter", "#FF0000"]);
  await page.getByLabel("字幕循环动画").selectOption("pulse");
  await expect.poll(async () => (await readProject(request, id)).sequence.captions[0].animLoopStyle)
    .toBe("pulse");
  await page.getByLabel("字幕循环周期").selectOption("500");
  await expect.poll(async () => (await readProject(request, id)).sequence.captions[0].animLoopMs)
    .toBe(500);
  await expect(highlight).toBeEnabled();
  await expect(highlight).toHaveAttribute("aria-pressed", "true");
  const overlayText = page.locator(".caption-overlay__text").first();
  await expect(overlayText).toHaveText("第");
  await expect(overlayText.locator("span").first()).toHaveCSS("color", "rgb(255, 0, 0)");

  await page.reload();
  await openProject(page, name);
  await captionTab.click();
  await page.getByRole("button", { name: "选择字幕 caption-one" }).click();
  await expect(page.getByLabel("字幕入场出场动画")).toHaveValue("typewriter-mid");
  await expect(page.getByLabel("字幕循环动画")).toHaveValue("pulse");
  await expect(page.getByLabel("字幕循环周期")).toHaveValue("500");
  await expect(page.getByRole("button", { name: "逐词高亮" })).toBeEnabled();
  await expect(page.getByText(/可与逐字入场同时使用/)).toBeVisible();
});

test("effects and transitions are timeline objects that can be selected, deleted and undone", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "effect.add", {
    clipId: "color-1", effectId: "cutvoke.fx.vibrance", params: { intensity: 0.35 },
  });
  await apply(request, id, "effect.setTransition", {
    clipId: "color-2", effectId: "cutvoke.transition.crossfade", params: { duration: 0.5 },
  });
  await openProject(page, name);
  const effect = page.getByRole("button", { name: /自然增色.*位于/ });
  await expect(effect).toBeVisible();
  await effect.click();
  await expect(effect).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: /删除效果 自然增色/ }).click();
  await expect.poll(async () => (await readProject(request, id)).sequence.tracks[0].clips[1].effects.length).toBe(0);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect(effect).toBeVisible();

  const transition = page.getByRole("button", { name: /转场，位于/ });
  await expect(transition).toBeVisible();
  await transition.click();
  await expect(transition).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: /删除转场/ }).click();
  await expect.poll(async () => (await readProject(request, id)).sequence.tracks[0].clips[2].effects.length).toBe(0);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect(transition).toBeVisible();
  await page.reload();
  await openProject(page, name);
  await expect(page.getByRole("button", { name: /转场，位于/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /自然增色.*位于/ })).toBeVisible();
});

test("seam hotspot selects the incoming clip for transition preview, apply, replace, and undo", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);

  await page.getByRole("button", { name: "在 5.00 秒的片段接缝添加转场" }).click();
  await expect(page.getByRole("tablist", { name: "工程编辑面板" }).getByRole("tab", { name: "转场" }))
    .toHaveAttribute("aria-selected", "true");
  const target = page.getByLabel("后一段片段（转场挂此段）");
  await expect(target).toHaveValue("color-1");
  await expect(page.getByText("转场将连接此前一段与当前片段")).toBeVisible();

  const options = page.locator(".transition-options .cv-chip");
  await expect(options).toHaveCount(41);
  const currentOption = page.locator(".transition-options .cv-chip--on");
  const originalName = (await currentOption.innerText()).trim();
  await page.getByRole("button", { name: "预览当前转场" }).click();
  await expect(page.locator('.resource-preview img[alt="当前转场与时长在两段片段之间的真实预览帧"]'))
    .toBeVisible({ timeout: 20_000 });

  const transitionOfIncoming = async () => (await readProject(request, id)).sequence.tracks
    .find((track) => track.id === "v1")!.clips.find((clip) => clip.id === "color-1")!.effects
    .find((effect) => effect.effectId.startsWith("cutvoke.transition."));
  await page.getByRole("button", { name: "应用转场" }).click();
  await expect.poll(async () => (await transitionOfIncoming())?.effectId)
    .toMatch(/^cutvoke\.transition\./);
  const firstId = (await transitionOfIncoming())!.effectId;

  await options.nth(1).click();
  const replacementName = (await page.locator(".transition-options .cv-chip--on").innerText()).trim();
  expect(replacementName).not.toBe(originalName);
  await page.getByRole("button", { name: "切换转场" }).click();
  await expect.poll(async () => (await transitionOfIncoming())?.effectId)
    .not.toBe(firstId);
  const replacementId = (await transitionOfIncoming())!.effectId;

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await transitionOfIncoming())?.effectId).toBe(firstId);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => (await transitionOfIncoming())?.effectId).toBe(replacementId);

  await page.locator(".cv-panel .cv-btn--danger").click();
  await expect.poll(transitionOfIncoming).toBeUndefined();
});

test("visual effects move and resize independently with undo and redo", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "effect.add", {
    clipId: "color-1", effectId: "cutvoke.fx.grayscale",
  });
  await openProject(page, name);

  const range = async () => {
    const clip = (await readProject(request, id)).sequence.tracks[0].clips.find((item) => item.id === "color-1")!;
    const effect = clip.effects.find((item) => item.effectId === "cutvoke.fx.grayscale")!;
    return effect.range ? [seconds(effect.range.start), seconds(effect.range.end)] : undefined;
  };
  const segment = page.locator('[data-testid="effect-segment"][data-effect-id="cutvoke.fx.grayscale"]');
  await expect(segment).toBeVisible();
  await segment.hover();
  const endHandle = segment.locator(".effect-segment__resize--end");
  const endBox = await endHandle.boundingBox();
  expect(endBox).toBeTruthy();
  await page.mouse.move(endBox!.x + endBox!.width / 2, endBox!.y + endBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(endBox!.x + endBox!.width / 2 - 80, endBox!.y + endBox!.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(range).toEqual([0, 3]);

  const segmentBox = await segment.boundingBox();
  expect(segmentBox).toBeTruthy();
  await page.mouse.move(segmentBox!.x + segmentBox!.width / 2, segmentBox!.y + segmentBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(segmentBox!.x + segmentBox!.width / 2 + 40, segmentBox!.y + segmentBox!.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect.poll(range).toEqual([1, 4]);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(range).toEqual([0, 3]);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(range).toEqual([1, 4]);

  await page.reload();
  await openProject(page, name);
  await expect.poll(range).toEqual([1, 4]);
});

test("crop effect ranges can be edited, undone and restored after reload", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "effect.add", {
    clipId: "color-1", effectId: "cutvoke.fx.crop",
    params: { x: 0.25, y: 0, w: 0.75, h: 1 },
  });
  await openProject(page, name);

  const range = async () => {
    const clip = (await readProject(request, id)).sequence.tracks[0].clips.find((item) => item.id === "color-1")!;
    const effect = clip.effects.find((item) => item.effectId === "cutvoke.fx.crop")!;
    return effect.range ? [seconds(effect.range.start), seconds(effect.range.end)] : undefined;
  };
  const segment = page.locator('[data-testid="effect-segment"][data-effect-id="cutvoke.fx.crop"]');
  await expect(segment).toBeVisible();
  await segment.hover();
  const endHandle = segment.locator(".effect-segment__resize--end");
  const endBox = await endHandle.boundingBox();
  expect(endBox).toBeTruthy();
  await page.mouse.move(endBox!.x + endBox!.width / 2, endBox!.y + endBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(endBox!.x + endBox!.width / 2 - 80, endBox!.y + endBox!.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(range).toEqual([0, 3]);

  const segmentBox = await segment.boundingBox();
  expect(segmentBox).toBeTruthy();
  await page.mouse.move(segmentBox!.x + segmentBox!.width / 2, segmentBox!.y + segmentBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(segmentBox!.x + segmentBox!.width / 2 + 40, segmentBox!.y + segmentBox!.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect.poll(range).toEqual([1, 4]);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(range).toEqual([0, 3]);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(range).toEqual([1, 4]);

  await page.reload();
  await openProject(page, name);
  await expect.poll(range).toEqual([1, 4]);
});

test("video transforms stay in sync between canvas and inspector while audio hides visual tools", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "clip.keyframe", {
    clipId: "color-0", param: "opacity", time: { num: 1, den: 2 }, value: 0.25,
  });
  await apply(request, id, "clip.keyframe", {
    clipId: "color-0", param: "opacity", time: { num: 7, den: 2 }, value: 1,
  });
  await apply(request, id, "track.add", { trackId: "audio-r06", kind: "audio" });
  await apply(request, id, "clip.insert", {
    trackId: "audio-r06", clipId: "audio-r06-clip", sourcePath: bgmFixture,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 5, den: 1 },
  });
  await openProject(page, name);

  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();
  await expect(page.getByRole("region", { name: "画面变换" })).toBeVisible();
  const keyframeStatus = page.locator('[aria-label="画面变换关键帧状态"]');
  await expect(keyframeStatus).toContainText("不透明度 2 个");
  await expect(page.getByRole("spinbutton", { name: "不透明度" })).toHaveValue("0.25");
  await keyframeStatus.getByRole("button", { name: "编辑不透明度关键帧" }).click();
  const keyframeEditor = page.locator("#clip-opacity-keyframes");
  await expect(keyframeEditor).toBeInViewport();
  await expect(keyframeEditor).toContainText("不透明度关键帧");
  await page.getByRole("spinbutton", { name: "水平位置 X" }).fill("12");
  await page.getByRole("button", { name: "应用变换" }).click();
  const readTransform = async () => {
    const clip = (await readProject(request, id)).sequence.tracks[0].clips.find((item) => item.id === "color-0")!;
    return clip.effects.find((item) => item.effectId === "cutvoke.transform")?.params as {
      position: { x: number; y: number }; scale: number; rotation: number;
    } | undefined;
  };
  await expect.poll(async () => (await readTransform())?.position.x).toBe(12);
  const transformKeyframeCount = async () => (await readProject(request, id)).sequence.tracks[0].clips
    .find((clip) => clip.id === "color-0")?.keyframes?.x?.length ?? 0;
  await page.getByRole("button", { name: "记录水平位置 X关键帧" }).click();
  await expect.poll(transformKeyframeCount).toBe(1);
  await expect(keyframeStatus).toContainText("X 1 个");
  await page.getByRole("button", { name: "删除水平位置 X关键帧 t=0.00s" }).click();
  await expect.poll(transformKeyframeCount).toBe(0);
  const moveHandle = page.getByRole("button", { name: "拖动画面位置" });
  const outlineBox = await moveHandle.boundingBox();
  expect(outlineBox).toBeTruthy();
  await page.mouse.move(outlineBox!.x + outlineBox!.width / 2, outlineBox!.y + outlineBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(outlineBox!.x + outlineBox!.width / 2 + 28, outlineBox!.y + outlineBox!.height / 2 + 8, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => (await readTransform())?.position.x || 0).toBeGreaterThan(12);
  const movedX = (await readTransform())!.position.x;
  await expect(page.getByRole("spinbutton", { name: "水平位置 X" })).toHaveValue(String(movedX));

  const scaleHandle = page.getByRole("button", { name: "调整画面大小" });
  await expect(scaleHandle).toBeVisible();
  const handleBox = await scaleHandle.boundingBox();
  expect(handleBox).toBeTruthy();
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleBox!.x + handleBox!.width / 2 - 60, handleBox!.y + handleBox!.height / 2 - 45, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => (await readTransform())?.scale || 1).toBeGreaterThan(1.1);
  await expect.poll(async () => Number(await page.getByRole("spinbutton", { name: "缩放" }).inputValue()))
    .toBeGreaterThan(1.1);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await readTransform())?.scale).toBe(1);
  await expect.poll(async () => Number(await page.getByRole("spinbutton", { name: "缩放" }).inputValue())).toBe(1);
  expect((await readTransform())?.position.x).toBe(movedX);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await readTransform())?.position.x).toBe(12);

  const rotateHandle = page.getByRole("button", { name: "旋转画面" });
  const rotateBox = await rotateHandle.boundingBox();
  expect(rotateBox).toBeTruthy();
  await page.mouse.move(rotateBox!.x + rotateBox!.width / 2, rotateBox!.y + rotateBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(rotateBox!.x + rotateBox!.width / 2 + 30, rotateBox!.y + rotateBox!.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => Math.abs((await readTransform())?.rotation || 0)).toBeGreaterThan(5);
  await expect.poll(async () => Math.abs(Number(await page.getByRole("spinbutton", { name: "旋转" }).inputValue())))
    .toBeGreaterThan(5);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await readTransform())?.rotation).toBe(0);

  await page.locator(".clip-block").filter({ hasText: "tech_minimal.wav" }).click();
  await expect(page.getByRole("slider", { name: "片段音量" })).toBeVisible();
  await expect(page.getByRole("region", { name: "画面变换" })).toHaveCount(0);
  await expect(page.getByText("视频动画", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "调色", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "叠化", exact: true })).toHaveCount(0);
  await expect(page.getByText("音频特效", { exact: true })).toBeVisible();
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();
  await expect(page.getByRole("spinbutton", { name: "水平位置 X" })).toHaveValue("12");
  await expect(page.getByRole("spinbutton", { name: "缩放" })).toHaveValue("1");
  await expect(page.getByRole("spinbutton", { name: "旋转" })).toHaveValue("0");
  await page.locator(".clip-block").filter({ hasText: "green.mp4" }).click();
  const transform = page.getByRole("region", { name: "画面变换" });
  await expect(transform.getByRole("button", { name: "记录水平位置 X关键帧" })).toBeDisabled();
  await expect(transform.getByRole("status")).toContainText("播放头位于片段外");
});

test("video audio controls explain unavailable source audio and allow probe retry", async ({ page, request }) => {
  const { name } = await createProject(request);
  let probeAttempts = 0;
  let allowProbeSuccess = false;
  await page.route("**/api/v1/probe**", async (route) => {
    probeAttempts += 1;
    if (!allowProbeSuccess) {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({ error: { message: "模拟检查失败" } }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        duration: 4, width: 1280, height: 720, has_video: true, has_audio: false, format: "mp4",
      }),
    });
  });
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();

  const audio = page.locator(".inspector__audio");
  await expect(audio.getByRole("status")).toContainText("无法检查素材原声");
  await expect(audio.getByRole("button", { name: "重试检查" })).toBeEnabled();
  allowProbeSuccess = true;
  await audio.getByRole("button", { name: "重试检查" }).click();

  await expect(audio.getByRole("status")).toContainText("素材不含原声");
  await expect(audio.getByRole("slider", { name: "片段音量" })).toBeDisabled();
  await expect(audio.getByRole("spinbutton", { name: "淡入时长" })).toBeDisabled();
  await expect(audio.getByRole("spinbutton", { name: "淡出时长" })).toBeDisabled();
  await expect(audio.getByRole("button", { name: "分离原声" })).toBeDisabled();
  expect(probeAttempts).toBeGreaterThanOrEqual(2);
});

test("title inspector explains why invalid title settings cannot be applied", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "clip.insert", {
    trackId: "title-track-r06", createTrackKind: "text", clipId: "title-clip-r06",
    text: { content: "R06 标题" },
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 3, den: 1 },
  });
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "R06 标题" }).click();

  const title = page.getByRole("region", { name: "文字标题属性" });
  await expect(title).toBeVisible();
  await title.getByRole("spinbutton", { name: "标题字号" }).fill("7");
  await expect(title.getByRole("button", { name: "应用标题属性" })).toBeDisabled();
  await expect(title.getByRole("status")).toContainText("字号需在 8–200 范围内");
});

test("keyframed transform bounds follow the playhead and canvas drag records both position axes in one undo", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "effect.add", { clipId: "color-0", effectId: "cutvoke.transform" });
  await apply(request, id, "effect.update", {
    clipId: "color-0", effectId: "cutvoke.transform",
    params: { position: { x: 0, y: 0 }, scale: 1, rotation: 0, opacity: 1 },
  });
  await apply(request, id, "clip.keyframe", {
    clipId: "color-0", action: "batch", keyframes: [
      { param: "x", time: { num: 0, den: 1 }, value: 0 },
      { param: "x", time: { num: 2, den: 1 }, value: 100 },
      { param: "y", time: { num: 0, den: 1 }, value: 0 },
      { param: "y", time: { num: 2, den: 1 }, value: 20 },
    ],
  });
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();

  const center = page.getByRole("button", { name: "拖动画面位置" });
  await expect(center).toHaveAttribute("cx", "80");
  const ruler = page.getByRole("slider", { name: "时间线播放头" });
  await ruler.focus();
  await ruler.press("PageUp");
  for (let frame = 0; frame < 5; frame += 1) await ruler.press("ArrowRight");
  await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow"))).toBeCloseTo(1, 4);
  await expect(page.getByRole("spinbutton", { name: "水平位置 X" })).toHaveValue("50");
  await expect(page.getByRole("spinbutton", { name: "垂直位置 Y" })).toHaveValue("10");
  await expect(center).toHaveAttribute("cx", "130");
  await expect(center).toHaveAttribute("cy", "55");

  const bounds = await center.boundingBox();
  expect(bounds).toBeTruthy();
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + bounds!.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds!.x + bounds!.width / 2 + 12, bounds!.y + bounds!.height / 2 + 7, { steps: 5 });
  await page.mouse.up();

  const keyframesAtOne = async (param: "x" | "y") => {
    const clip = (await readProject(request, id)).sequence.tracks[0].clips.find((item) => item.id === "color-0")!;
    return clip.keyframes?.[param]?.find((keyframe) => Math.abs(seconds(keyframe.time) - 1) < 0.02);
  };
  await expect.poll(async () => keyframesAtOne("x")).toBeTruthy();
  await expect.poll(async () => keyframesAtOne("y")).toBeTruthy();
  expect((await keyframesAtOne("x"))?.value).toBeGreaterThan(50);
  expect((await keyframesAtOne("y"))?.value).toBeGreaterThan(10);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await readProject(request, id)).sequence.tracks[0].clips
    .find((item) => item.id === "color-0")?.keyframes?.x?.length).toBe(2);
  await expect.poll(async () => (await readProject(request, id)).sequence.tracks[0].clips
    .find((item) => item.id === "color-0")?.keyframes?.y?.length).toBe(2);
  await expect(center).toHaveAttribute("cx", "130");
  await expect(center).toHaveAttribute("cy", "55");
});

test("opacity animation asks how to handle manual keyframes and undo restores both", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "clip.keyframe", {
    clipId: "color-0", param: "opacity", time: { num: 0, den: 1 }, value: 0.2,
  });
  await apply(request, id, "clip.keyframe", {
    clipId: "color-0", param: "opacity", time: { num: 2, den: 1 }, value: 1,
  });
  expect((await readProject(request, id)).sequence.tracks[0].clips
    .find((clip) => clip.id === "color-0")?.keyframes?.opacity).toHaveLength(2);
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();

  const entrance = page.getByLabel("入场动画");
  await entrance.selectOption("cutvoke.anim.rotateIn");
  const conflict = page.getByRole("alertdialog", { name: "动画与关键帧冲突选择" });
  await expect(conflict).toBeVisible();
  await conflict.getByRole("button", { name: "保留关键帧并叠加" }).click();
  const clipState = async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks[0].clips.find((clip) => clip.id === "color-0")!;
  };
  await expect.poll(async () => {
    const clip = await clipState();
    return [clip.keyframes?.opacity?.length || 0,
      clip.effects.some((effect) => effect.effectId === "cutvoke.anim.rotateIn")];
  }).toEqual([2, true]);

  const exit = page.getByLabel("出场动画");
  await exit.selectOption("cutvoke.anim.fadeOut");
  await expect(conflict).toBeVisible();
  await conflict.getByRole("button", { name: "移除关键帧后应用" }).click();
  await expect.poll(async () => {
    const clip = await clipState();
    return [clip.keyframes?.opacity?.length || 0,
      clip.effects.some((effect) => effect.effectId === "cutvoke.anim.fadeOut")];
  }).toEqual([0, true]);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await clipState()).keyframes?.opacity?.length).toBe(2);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => (await clipState()).keyframes?.opacity?.length || 0).toBe(0);
});

test("image and video animation slots stay separate and each application has one undo step", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "clip.insert", {
    trackId: "v1", clipId: "still-image", sourcePath: imageFixture,
    timelineStart: { num: 15, den: 1 }, timelineEnd: { num: 20, den: 1 },
  });
  await openProject(page, name);

  const imageClip = page.locator(".clip-block").filter({ hasText: "decor_sparkles.png" });
  await imageClip.click();
  await expect(page.getByText("图片动画", { exact: true })).toBeVisible();
  await expect(page.getByLabel("组合动画")).toHaveCount(0);
  const imageEntrance = page.getByLabel("入场动画");
  const entranceId = await imageEntrance.locator("option").nth(1).getAttribute("value");
  expect(entranceId).toBeTruthy();
  await imageEntrance.selectOption(entranceId!);
  await expect.poll(async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks[0].clips.find((clip) => clip.id === "still-image")
      ?.effects.some((effect) => effect.effectId === entranceId);
  }).toBe(true);

  const videoClip = page.locator(".clip-block").filter({ hasText: "green.mp4" });
  await videoClip.click();
  await expect(page.getByText("视频动画", { exact: true })).toBeVisible();
  const combo = page.getByLabel("组合动画");
  await expect(combo).toBeVisible();
  const comboId = await combo.locator("option").nth(1).getAttribute("value");
  expect(comboId).toBeTruthy();
  await combo.selectOption(comboId!);
  await expect.poll(async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks[0].clips.find((clip) => clip.id === "color-1")
      ?.effects.some((effect) => effect.effectId === comboId);
  }).toBe(true);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => {
    const project = await readProject(request, id);
    const still = project.sequence.tracks[0].clips.find((clip) => clip.id === "still-image")!;
    const video = project.sequence.tracks[0].clips.find((clip) => clip.id === "color-1")!;
    return [still.effects.some((effect) => effect.effectId === entranceId),
      video.effects.some((effect) => effect.effectId === comboId)];
  }).toEqual([true, false]);

  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks[0].clips.find((clip) => clip.id === "color-1")
      ?.effects.some((effect) => effect.effectId === comboId);
  }).toBe(true);
});

test("Web speed change on six clips leaves later shots and independent BGM in place", async ({ page, request }) => {
  const fixture = await readProject(request, "e2e-fixture");
  const sources = fixture.sequence.tracks[0].clips.map((clip) => clip.assetRef.sourcePath);
  const id = `speed-web-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `变速 Web 验收 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 15 },
  });
  expect(created.status()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  await apply(request, id, "track.add", { trackId: "music", kind: "audio" });
  for (let index = 0; index < 6; index++) {
    await apply(request, id, "clip.insert", {
      trackId: "v1", clipId: `shot-${index + 1}`, sourcePath: sources[index % sources.length],
      timelineStart: { num: index * 2, den: 1 },
      timelineEnd: { num: index * 2 + 2, den: 1 },
    });
  }
  await apply(request, id, "clip.insert", {
    trackId: "music", clipId: "bgm", sourcePath: bgmFixture,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 12, den: 1 },
  });
  await openProject(page, name);

  await page.locator(".clip-block").first().click();
  await page.getByRole("tab", { name: "变速" }).click();
  const exactSpeed = page.getByLabel("精确倍速");
  await exactSpeed.fill("1.4");
  await expect(page.locator("#speed-preview-hint"))
    .toContainText("其他片段与独立音轨不会自动移动");
  await page.getByRole("button", { name: "应用变速设置" }).click();

  const timeline = () => readProject(request, id).then((project) => ({
    video: project.sequence.tracks.find((track) => track.id === "v1")!,
    music: project.sequence.tracks.find((track) => track.id === "music")!,
  }));
  await expect.poll(async () => {
    const { video, music } = await timeline();
    return [seconds(video.clips[0].timelineEnd), seconds(video.clips[1].timelineStart),
      seconds(music.clips[0].timelineStart), seconds(music.clips[0].timelineEnd)];
  }).toEqual([10 / 7, 2, 0, 12]);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => {
    const { video, music } = await timeline();
    return [seconds(video.clips[0].timelineEnd), seconds(video.clips[1].timelineStart),
      seconds(music.clips[0].timelineStart), seconds(music.clips[0].timelineEnd)];
  }).toEqual([2, 2, 0, 12]);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => seconds((await timeline()).video.clips[0].timelineEnd)).toBeCloseTo(10 / 7, 5);

  await page.getByRole("button", { name: "导出", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  await expect(dialog).toBeVisible();
  const outputPath = resolve(process.cwd(), "../../output", `${id}.mp4`);
  await dialog.getByLabel("输出文件路径").fill(outputPath);
  await dialog.getByLabel("画质").selectOption("low");
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 45_000 });
  await expect(dialog.locator(".inspector__row").filter({ hasText: "时长" })).toContainText("12.00 秒");
  await expect(dialog.locator(".inspector__row").filter({ hasText: "含音频" })).toContainText("是");

  await page.reload();
  await openProject(page, name);
  const reopened = await readProject(request, id);
  const reopenedVideo = reopened.sequence.tracks.find((track) => track.id === "v1")!;
  const reopenedMusic = reopened.sequence.tracks.find((track) => track.id === "music")!;
  expect(seconds(reopenedVideo.clips[0].speed!)).toBeCloseTo(1.4, 5);
  expect(seconds(reopenedVideo.clips[0].timelineEnd)).toBeCloseTo(10 / 7, 5);
  expect(seconds(reopenedVideo.clips[1].timelineStart)).toBe(2);
  expect([seconds(reopenedMusic.clips[0].timelineStart), seconds(reopenedMusic.clips[0].timelineEnd)])
    .toEqual([0, 12]);
});

test("speed curve can be edited, undone, reopened, and exported from the Web editor", async ({ page, request }) => {
  test.setTimeout(90_000);
  const fixture = await readProject(request, "e2e-fixture");
  const sourcePath = fixture.sequence.tracks[0].clips[0].assetRef.sourcePath;
  const id = `speed-curve-web-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `曲线变速 Web 验收 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 15 },
  });
  expect(created.status()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  await apply(request, id, "clip.insert", {
    trackId: "v1", clipId: "curve-clip", sourcePath,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 2, den: 1 },
  });
  await openProject(page, name);
  await page.locator(".clip-block").first().click();
  await page.getByRole("tab", { name: "变速" }).click();
  await page.getByRole("tab", { name: "曲线变速" }).click();
  await page.getByRole("button", { name: "慢快慢", exact: true }).click();
  await expect(page.getByRole("button", { name: "应用速度曲线" })).toBeEnabled();
  await expect(page.getByRole("tabpanel", { name: "曲线变速" }))
    .toContainText("时长变化");
  await page.getByRole("button", { name: "应用速度曲线" }).click();

  const curveState = async () => {
    const project = await readProject(request, id);
    const clip = project.sequence.tracks.find((track) => track.id === "v1")!.clips[0];
    return {
      end: seconds(clip.timelineEnd),
      sourceDuration: clip.speedCurve ? seconds(clip.speedCurve.sourceDuration) : null,
      speeds: clip.speedCurve?.points.map((point) => seconds(point.speed)) ?? null,
    };
  };
  await expect.poll(curveState).toEqual({
    end: expect.any(Number), sourceDuration: 2, speeds: [0.5, 1, 2, 1, 0.5],
  });
  const applied = await curveState();
  expect(applied.end).toBeGreaterThan(2);
  expect(applied.end).toBeLessThan(5);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(curveState).toEqual({ end: 2, sourceDuration: null, speeds: null });
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(curveState).toEqual(applied);

  const outputPath = resolve(process.cwd(), "../../output", `${id}.mp4`);
  await page.getByRole("button", { name: "导出", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("输出文件路径").fill(outputPath);
  await dialog.getByLabel("画质").selectOption("low");
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 60_000 });
  const jobsResponse = await request.get(`${apiPath(id)}/exports`);
  expect(jobsResponse.ok()).toBeTruthy();
  const jobs = (await jobsResponse.json()).jobs as Array<{
    outPath: string; status: string; result: { duration: number } | null;
  }>;
  const completed = jobs.find((job) => job.outPath === outputPath);
  expect(completed?.status).toBe("succeeded");
  expect(Math.abs((completed?.result?.duration ?? 0) - applied.end))
    .toBeLessThanOrEqual(1 / 15 + 0.002);

  await page.reload();
  await openProject(page, name);
  await page.locator(".clip-block").first().click();
  await page.getByRole("tab", { name: "变速" }).click();
  await expect(page.getByRole("tab", { name: "曲线变速" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByLabel("节点 1 倍率")).toHaveValue("0.5");
  await expect.poll(curveState).toEqual(applied);
  unlinkSync(outputPath);
});

test("attached overlays follow video moves, Alt keeps them in place, and unlinking persists", async ({ page, request }) => {
  const fixture = await readProject(request, "e2e-fixture");
  const sourcePath = fixture.sequence.tracks[0].clips[0].assetRef.sourcePath;
  const id = `attached-move-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `关联移动 Web 验收 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 15 },
  });
  expect(created.status()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  await apply(request, id, "clip.insert", {
    trackId: "v1", clipId: "parent-video", sourcePath,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 2, den: 1 },
  });
  await apply(request, id, "clip.insert", {
    trackId: "overlay", createTrackKind: "video", createTrackRole: "sticker", role: "sticker",
    clipId: "attached-sticker", sourcePath: imageFixture,
    timelineStart: { num: 1, den: 2 }, timelineEnd: { num: 3, den: 2 },
  });
  await openProject(page, name);
  const overlay = page.locator('.timeline-track__lane[data-track-id="overlay"] .clip-block');
  await overlay.click();
  await page.getByLabel("跟随视频片段").selectOption("parent-video");
  const readPositions = async () => {
    const project = await readProject(request, id);
    const clips = project.sequence.tracks.flatMap((track) => track.clips);
    const video = clips.find((clip) => clip.id === "parent-video")!;
    const sticker = clips.find((clip) => clip.id === "attached-sticker")!;
    return {
      video: seconds(video.timelineStart),
      sticker: seconds(sticker.timelineStart),
      attachedTo: sticker.attachedToClipId ?? null,
    };
  };
  await expect.poll(readPositions).toEqual({ video: 0, sticker: 0.5, attachedTo: "parent-video" });

  const videoBlock = page.locator('.timeline-track__lane[data-track-id="v1"] .clip-block');
  const moveBy = async (fractionOfClip: number, holdAlt = false) => {
    const box = await videoBlock.boundingBox();
    expect(box).not.toBeNull();
    const startX = box!.x + box!.width / 2;
    const startY = box!.y + box!.height / 2;
    if (holdAlt) await page.keyboard.down("Alt");
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + box!.width * fractionOfClip, startY, { steps: 8 });
    await page.mouse.up();
    if (holdAlt) await page.keyboard.up("Alt");
  };

  // A two-second clip shifted by half its width moves exactly one second.
  await moveBy(0.5);
  await expect.poll(readPositions).toEqual({ video: 1, sticker: 1.5, attachedTo: "parent-video" });
  await expect(page.getByText("已移动片段，1 个关联片段同步移动")).toBeVisible();
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(readPositions).toEqual({ video: 0, sticker: 0.5, attachedTo: "parent-video" });
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(readPositions).toEqual({ video: 1, sticker: 1.5, attachedTo: "parent-video" });

  // Move by 0.8 seconds while holding Alt; attachment remains at its own time.
  await moveBy(0.4, true);
  await expect.poll(readPositions).toEqual({ video: 1.8, sticker: 1.5, attachedTo: "parent-video" });
  await overlay.click();
  await page.getByLabel("跟随视频片段").selectOption("");
  await expect.poll(readPositions).toEqual({ video: 1.8, sticker: 1.5, attachedTo: null });
  await page.reload();
  await openProject(page, name);
  await expect.poll(readPositions).toEqual({ video: 1.8, sticker: 1.5, attachedTo: null });
});

test("timeline range export is queued and reports the selected duration", async ({ page, request }) => {
  test.setTimeout(90_000);
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("button", { name: "导出", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  await expect(dialog).toBeVisible();
  const outputPath = resolve(process.cwd(), "../../output", `${id}-range.mp4`);
  await dialog.getByLabel("输出文件路径").fill(outputPath);
  await dialog.getByLabel("画质").selectOption("low");
  await dialog.getByLabel("仅导出时间范围").check();
  await dialog.getByLabel("导出开始时间").fill("0.8");
  await dialog.getByLabel("导出结束时间").fill("2.2");
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog.getByText("导出完成", { exact: false }))
    .toBeVisible({ timeout: 75_000 });
  await expect(dialog.locator(".inspector__row").filter({ hasText: "时长" }))
    .toContainText("1.40 秒");
  expect(statSync(outputPath).size).toBeGreaterThan(0);
  const jobsResponse = await request.get(`${apiPath(id)}/exports`);
  expect(jobsResponse.ok()).toBeTruthy();
  const jobs = (await jobsResponse.json()).jobs as Array<{
    outPath: string; status: string; result: { duration: number } | null;
  }>;
  const completed = jobs.find((job) => job.outPath === outputPath);
  expect(completed?.status).toBe("succeeded");
  expect(completed?.result?.duration).toBeCloseTo(1.4, 1);
  unlinkSync(outputPath);
});

test("slow-motion interpolation can be enabled, undone, redone, and reopened", async ({ page, request }) => {
  const fixture = await readProject(request, "e2e-fixture");
  const realFootagePath = process.env.CUTVOKE_E2E_REAL_FOOTAGE_PATH?.trim();
  if (realFootagePath) test.setTimeout(180_000);
  const source = realFootagePath || fixture.sequence.tracks[0].clips[0].assetRef.sourcePath;
  const width = realFootagePath ? 1920 : 160;
  const height = realFootagePath ? 1080 : 90;
  const id = `slow-motion-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `慢动作补帧验收 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width, height, fps: 15 },
  });
  expect(created.status()).toBe(201);
  await apply(request, id, "track.add", { trackId: "v1", kind: "video" });
  await apply(request, id, "clip.insert", {
    trackId: "v1", clipId: "slow-clip", sourcePath: source,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 2, den: 1 },
  });
  await openProject(page, name);
  await page.locator(".clip-block").first().click();
  await page.getByRole("tab", { name: "变速" }).click();
  await page.getByLabel("精确倍速").fill("0.5");
  await page.getByRole("button", { name: "应用变速设置" }).click();

  const interpolation = page.getByLabel("慢动作补帧模式");
  await expect(interpolation).toBeEnabled();
  await interpolation.selectOption("motion");
  const readMode = async () => (await readProject(request, id))
    .sequence.tracks[0].clips[0].frameInterpolation ?? "none";
  await expect.poll(readMode).toBe("motion");

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(readMode).toBe("none");
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(readMode).toBe("motion");
  await page.reload();
  await openProject(page, name);
  await page.locator(".clip-block").first().click();
  await page.getByRole("tab", { name: "变速" }).click();
  await expect(page.getByLabel("慢动作补帧模式")).toHaveValue("motion");
  if (realFootagePath) {
    const outputPath = resolve(process.cwd(), "../../output", `${id}-real-slow-motion.mp4`);
    await page.getByRole("button", { name: "导出", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "导出成片" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("输出文件路径").fill(outputPath);
    await dialog.getByLabel("画质").selectOption("low");
    await dialog.getByRole("button", { name: "开始导出" }).click();
    await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 120_000 });
    await expect(dialog.locator(".inspector__row").filter({ hasText: "时长" })).toContainText("4.00 秒");
    await dialog.getByRole("button", { name: "关闭导出" }).click();
  }
  await page.getByLabel("精确倍速").fill("1");
  await page.getByRole("button", { name: "应用变速设置" }).click();
  await expect.poll(readMode).toBe("none");
  await expect(page.getByLabel("慢动作补帧模式")).toBeDisabled();
});

test("built-in composition backgrounds can be dragged onto an empty timeline", async ({ page, request }) => {
  const id = `background-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `构图背景验收 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 15 },
  });
  expect(created.status()).toBe(201);

  const listed = await request.get("/api/v1/assets");
  expect(listed.ok()).toBeTruthy();
  const assets = (await listed.json()).assets as Array<{ assetId: string; path: string; name: string }>;
  const whiteBackground = assets.find((asset) => asset.assetId === "builtin_background_bg_solid_white");
  expect(whiteBackground?.name).toContain("纯白背景");

  await openProject(page, name);
  await page.getByRole("tab", { name: /内置资源/ }).click();
  const backgroundCard = page.locator(".media-item").filter({ hasText: "纯白背景" });
  await expect(backgroundCard).toBeVisible();
  await backgroundCard.dragTo(page.locator(".timeline__empty-area"));

  await expect.poll(async () => (await readProject(request, id)).sequence.tracks.length).toBe(1);
  const project = await readProject(request, id);
  expect(project.sequence.tracks[0].clips).toHaveLength(1);
  expect(project.sequence.tracks[0].clips[0].assetRef.sourcePath).toBe(whiteBackground?.path);
});

test("crop canvas handles edit, undo, redo, and persist after reopening", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "effect.add", {
    clipId: "color-0", effectId: "cutvoke.fx.crop",
    params: { x: 0, y: 0, w: 1, h: 1 },
  });
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();

  const overlay = page.locator('svg[aria-label="画布裁切控件"]');
  await expect(overlay).toBeVisible();
  const cropParams = async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks[0].clips.find((clip) => clip.id === "color-0")!
      .effects.find((effect) => effect.effectId === "cutvoke.fx.crop")!.params!;
  };
  const expectCrop = async (expected: { x: number; y: number; w: number; h: number }) => {
    await expect.poll(async () => {
      const actual = await cropParams();
      return (["x", "y", "w", "h"] as const).every((key) =>
        typeof actual[key] === "number" && Math.abs((actual[key] as number) - expected[key]) <= 0.0021);
    }).toBe(true);
  };
  const expectCropCanvas = async (expected: { x: number; y: number; w: number; h: number }) => {
    await expect.poll(async () => {
      const actual = await overlay.locator(".player__crop-outline").evaluate((element) => {
        const rect = element as SVGRectElement;
        const viewBox = rect.ownerSVGElement!.viewBox.baseVal;
        return {
          x: Number(rect.getAttribute("x")),
          y: Number(rect.getAttribute("y")),
          w: Number(rect.getAttribute("width")),
          h: Number(rect.getAttribute("height")),
          viewWidth: viewBox.width,
          viewHeight: viewBox.height,
        };
      });
      return (["x", "y", "w", "h"] as const).every((key) =>
        Math.abs(actual[key] / (key === "x" || key === "w" ? actual.viewWidth : actual.viewHeight) - expected[key]) <= 0.0021);
    }).toBe(true);
  };
  const before = await cropParams();
  expect(before).toMatchObject({ x: 0, y: 0, w: 1, h: 1 });

  const southEast = page.getByRole("button", { name: "调整裁切右下角" });
  const handle = await southEast.boundingBox();
  const canvas = await overlay.boundingBox();
  expect(handle && canvas).toBeTruthy();
  await page.mouse.move(handle!.x + handle!.width / 2, handle!.y + handle!.height / 2);
  await page.mouse.down();
  await page.mouse.move(handle!.x + handle!.width / 2 - canvas!.width * 0.2,
    handle!.y + handle!.height / 2 - canvas!.height * 0.2, { steps: 8 });
  await page.mouse.up();
  await expectCrop({ x: 0, y: 0, w: 0.8, h: 0.8 });
  // The backend command can commit before the player rerenders its SVG. Wait for
  // the visible geometry before using that geometry as the next drag target.
  await expectCropCanvas({ x: 0, y: 0, w: 0.8, h: 0.8 });

  const outline = page.getByRole("button", { name: "移动裁切区域" });
  const outlineBox = await outline.boundingBox();
  expect(outlineBox).toBeTruthy();
  const moveBy = await overlay.evaluate((element) => {
    const svg = element as SVGSVGElement;
    const matrix = svg.getScreenCTM()!;
    const viewBox = svg.viewBox.baseVal;
    return { x: matrix.a * viewBox.width * 0.05, y: matrix.d * viewBox.height * 0.05 };
  });
  await page.mouse.move(outlineBox!.x + outlineBox!.width / 2,
    outlineBox!.y + outlineBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(outlineBox!.x + outlineBox!.width / 2 + moveBy.x,
    outlineBox!.y + outlineBox!.height / 2 + moveBy.y, { steps: 5 });
  await page.mouse.up();
  await expectCrop({ x: 0.05, y: 0.05, w: 0.8, h: 0.8 });

  await page.getByRole("button", { name: "调整裁切右下角" }).focus();
  await page.getByRole("button", { name: "调整裁切右下角" }).press("ArrowRight");
  await expectCrop({ x: 0.05, y: 0.05, w: 0.81, h: 0.8 });

  await page.getByRole("button", { name: "撤销" }).click();
  await expectCrop({ x: 0.05, y: 0.05, w: 0.8, h: 0.8 });
  await page.getByRole("button", { name: "重做" }).click();
  await expectCrop({ x: 0.05, y: 0.05, w: 0.81, h: 0.8 });

  await page.reload();
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();
  await expect(page.locator('svg[aria-label="画布裁切控件"]')).toBeVisible();
  await expectCrop({ x: 0.05, y: 0.05, w: 0.81, h: 0.8 });
});

test("text and freehand masks edit on canvas and persist through undo and reopening", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await apply(request, id, "track.add", { trackId: "v2", kind: "video" });
  await apply(request, id, "clip.insert", {
    trackId: "v2", clipId: "mask-overlay", sourcePath: imageFixture,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 5, den: 1 },
  });
  await apply(request, id, "effect.add", {
    clipId: "mask-overlay", effectId: "cutvoke.fx.mask",
    params: { shape: "text", x: 0.1, y: 0.15, content: "OPENCUT", fontSize: 0.2,
      feather: 0, invert: false },
  });
  await openProject(page, name);
  const overlay = page.locator('svg[aria-label="画布蒙版控件"]');
  await page.locator(".clip-block").filter({ hasText: "decor_sparkles.png" }).click();
  await expect(overlay).toBeVisible();

  const maskParams = async () => (await readProject(request, id)).sequence.tracks
    .find((track) => track.id === "v2")!.clips[0].effects
    .find((effect) => effect.effectId === "cutvoke.fx.mask")!.params!;
  await expect(page.getByLabel("蒙版文字")).toHaveValue("OPENCUT");
  const textOutline = page.getByRole("button", { name: "移动文字蒙版" });
  await textOutline.focus();
  await textOutline.press("ArrowRight");
  await expect.poll(async () => Number((await maskParams()).x)).toBeCloseTo(0.11, 3);

  await page.getByLabel("形状", { exact: true }).selectOption("freehand");
  await expect.poll(async () => (await maskParams()).shape).toBe("freehand");
  await expect(page.getByRole("button", { name: "取消绘制" })).toBeVisible();
  const canvas = await overlay.boundingBox();
  expect(canvas).toBeTruthy();
  const path = [
    [0.35, 0.6], [0.68, 0.6], [0.68, 0.82], [0.35, 0.82], [0.35, 0.6],
  ];
  await page.mouse.move(canvas!.x + canvas!.width * path[0][0], canvas!.y + canvas!.height * path[0][1]);
  await page.mouse.down();
  for (const [x, y] of path.slice(1)) {
    await page.mouse.move(canvas!.x + canvas!.width * x, canvas!.y + canvas!.height * y, { steps: 4 });
  }
  await page.mouse.up();
  await expect.poll(async () => {
    const points = (await maskParams()).points as Array<{ x: number; y: number }> | undefined;
    return points?.length || 0;
  }).toBeGreaterThanOrEqual(4);

  const pointsBeforeUndo = (await maskParams()).points;
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await maskParams()).points).not.toEqual(pointsBeforeUndo);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => (await maskParams()).points).toEqual(pointsBeforeUndo);

  await expect.poll(async () => (await maskParams()).pathMode).toBe("smooth");
  await page.getByRole("button", { name: "路径：平滑" }).click();
  await expect.poll(async () => (await maskParams()).pathMode).toBe("bezier");
  const bezierPoints = (await maskParams()).points as Array<{
    x: number; y: number; inHandle?: { x: number; y: number }; outHandle?: { x: number; y: number };
  }>;
  expect(bezierPoints.every((point) => point.inHandle && point.outHandle)).toBeTruthy();
  await page.getByRole("button", { name: "路径：贝塞尔" }).click();
  await expect.poll(async () => (await maskParams()).pathMode).toBe("linear");
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await maskParams()).pathMode).toBe("bezier");
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => (await maskParams()).pathMode).toBe("linear");
  await page.getByRole("button", { name: "路径：折线" }).click();
  await expect.poll(async () => (await maskParams()).pathMode).toBe("smooth");

  await page.getByRole("button", { name: "编辑路径点" }).click();
  const firstPointX = Number(((await maskParams()).points as Array<{ x: number }>)[0].x);
  const firstAnchor = page.getByRole("button", { name: "编辑钢笔路径点 1", exact: true });
  await firstAnchor.focus();
  await firstAnchor.press("ArrowRight");
  await expect.poll(async () => Number(((await maskParams()).points as Array<{ x: number }>)[0].x))
    .toBeCloseTo(firstPointX + 0.01, 3);
  const pointsAfterAnchorEdit = (await maskParams()).points;
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => Number(((await maskParams()).points as Array<{ x: number }>)[0].x))
    .toBeCloseTo(firstPointX, 3);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => (await maskParams()).points).toEqual(pointsAfterAnchorEdit);

  const anchorBox = await firstAnchor.boundingBox();
  expect(anchorBox).toBeTruthy();
  await page.mouse.move(anchorBox!.x + anchorBox!.width / 2, anchorBox!.y + anchorBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(anchorBox!.x + anchorBox!.width / 2 + 8,
    anchorBox!.y + anchorBox!.height / 2 + 4, { steps: 3 });
  await page.mouse.up();
  await expect.poll(async () => (await maskParams()).points).not.toEqual(pointsAfterAnchorEdit);
  const pointsAfterDrag = (await maskParams()).points;
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => (await maskParams()).points).toEqual(pointsAfterAnchorEdit);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => (await maskParams()).points).toEqual(pointsAfterDrag);

  await page.getByRole("button", { name: "路径：平滑" }).click();
  await expect.poll(async () => (await maskParams()).pathMode).toBe("bezier");
  await firstAnchor.focus();
  await expect(firstAnchor).toHaveAttribute("aria-pressed", "true");
  const outHandle = page.getByRole("button", { name: "编辑贝塞尔手柄 1 出柄" });
  await expect(outHandle).toBeVisible();
  const handleBox = await outHandle.boundingBox();
  expect(handleBox).toBeTruthy();
  const pointsBeforeHandleDrag = (await maskParams()).points as Array<{
    x: number; y: number; inHandle?: { x: number; y: number }; outHandle?: { x: number; y: number };
  }>;
  await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleBox!.x + handleBox!.width / 2 + 16,
    handleBox!.y + handleBox!.height / 2 - 12, { steps: 4 });
  await page.mouse.up();
  const changedBezierPoints = () => maskParams().then((params) => params.points) as Promise<typeof pointsBeforeHandleDrag>;
  await expect.poll(async () => JSON.stringify(await changedBezierPoints()))
    .not.toBe(JSON.stringify(pointsBeforeHandleDrag));
  const pointsAfterHandleDrag = await changedBezierPoints();
  expect(pointsAfterHandleDrag[0].inHandle).not.toEqual(pointsBeforeHandleDrag[0].inHandle);
  expect(pointsAfterHandleDrag[0].outHandle).not.toEqual(pointsBeforeHandleDrag[0].outHandle);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(changedBezierPoints).toEqual(pointsBeforeHandleDrag);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(changedBezierPoints).toEqual(pointsAfterHandleDrag);

  await page.getByRole("button", { name: "贝塞尔切线 1：联动" }).click();
  await expect.poll(async () => (await maskParams()).points[0].handlesLinked).toBe(false);
  const pointsBeforeIndependentDrag = await changedBezierPoints();
  const independentHandleBox = await outHandle.boundingBox();
  expect(independentHandleBox).toBeTruthy();
  await page.mouse.move(independentHandleBox!.x + independentHandleBox!.width / 2,
    independentHandleBox!.y + independentHandleBox!.height / 2);
  await page.mouse.down();
  await page.mouse.move(independentHandleBox!.x + independentHandleBox!.width / 2 + 14,
    independentHandleBox!.y + independentHandleBox!.height / 2 + 9, { steps: 4 });
  await page.mouse.up();
  await expect.poll(changedBezierPoints).not.toEqual(pointsBeforeIndependentDrag);
  const pointsAfterIndependentDrag = await changedBezierPoints();
  expect(pointsAfterIndependentDrag[0].inHandle).toEqual(pointsBeforeIndependentDrag[0].inHandle);
  expect(pointsAfterIndependentDrag[0].outHandle).not.toEqual(pointsBeforeIndependentDrag[0].outHandle);
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(changedBezierPoints).toEqual(pointsBeforeIndependentDrag);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(changedBezierPoints).toEqual(pointsAfterIndependentDrag);

  await page.reload();
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "decor_sparkles.png" }).click();
  await expect(overlay).toBeVisible();
  await expect.poll(async () => (await maskParams()).points).toEqual(pointsAfterIndependentDrag);
  await expect.poll(async () => (await maskParams()).pathMode).toBe("bezier");
  await expect(page.getByRole("button", { name: "移动钢笔蒙版" })).toBeVisible();
});

test("material texture atlas previews and adds a saved overlay sticker", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await category.selectOption("材质纹理");
  await expect(page.getByText("16 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "虹彩镭射箔" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const preview = card.locator("video");
  await expect(preview).toBeVisible();
  await preview.hover();
  await expect.poll(() => preview.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0.1);

  const screenshot = resolve(process.cwd(), "../../output/playwright/material-texture-library-20260925.png");
  mkdirSync(dirname(screenshot), { recursive: true });
  await page.screenshot({ path: screenshot, fullPage: false });
  await card.getByRole("button", { name: "添加贴纸 虹彩镭射箔" }).click();
  await expect(page.getByText("已添加贴纸「虹彩镭射箔」到独立叠加轨")).toBeVisible();
  const hasMaterialSticker = async () => (await readProject(request, id)).sequence.tracks
    .flatMap((track) => track.clips)
    .some((clip) => clip.assetRef.sourcePath.includes("material_holographic_foil.png"));
  await expect.poll(hasMaterialSticker).toBe(true);

  await page.reload();
  await openProject(page, name);
  expect(await hasMaterialSticker()).toBe(true);
});

test("person atmosphere overlays preview as approved and survive reopening", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await category.selectOption("人物氛围");
  await expect(page.getByText("16 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "青色霓虹光环" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const preview = card.locator("video");
  await expect(preview).toBeVisible();
  await preview.hover();
  await expect.poll(() => preview.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0.1);

  await card.getByRole("button", { name: "添加贴纸 青色霓虹光环" }).click();
  await expect(page.getByText("已添加贴纸「青色霓虹光环」到独立叠加轨")).toBeVisible();
  const hasPersonOverlay = async () => (await readProject(request, id)).sequence.tracks
    .flatMap((track) => track.clips)
    .some((clip) => clip.assetRef.sourcePath.includes("personfx_halo_cyan.png"));
  await expect.poll(hasPersonOverlay).toBe(true);

  await page.reload();
  await openProject(page, name);
  expect(await hasPersonOverlay()).toBe(true);
});

test("generated hand-drawn marker stickers preview as approved and survive reopening", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await category.selectOption("标记");
  await expect(page.getByText("28 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "手绘圈选" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const preview = card.locator("video");
  await expect(preview).toBeVisible();
  await preview.hover();
  await expect.poll(() => preview.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0.1);
  await card.getByRole("button", { name: "添加贴纸 手绘圈选" }).click();
  await expect(page.getByText("已添加贴纸「手绘圈选」到独立叠加轨")).toBeVisible();

  const hasMarkerSticker = async () => (await readProject(request, id)).sequence.tracks
    .flatMap((track) => track.clips)
    .some((clip) => clip.assetRef.sourcePath.includes("marker_doodle_circle.png"));
  await expect.poll(hasMarkerSticker).toBe(true);

  await page.reload();
  await openProject(page, name);
  expect(await hasMarkerSticker()).toBe(true);
});

test("generated background texture fills the canvas by default and survives reopening", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await category.selectOption("背景纹理");
  await expect(page.getByText("16 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "深海焦散" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  await card.getByRole("button", { name: "添加贴纸 深海焦散" }).click();
  await expect(page.getByText("已添加贴纸「深海焦散」到独立叠加轨")).toBeVisible();

  const textureTransform = async () => {
    const project = await readProject(request, id);
    const clip = project.sequence.tracks.flatMap((track) => track.clips)
      .find((item) => item.assetRef.sourcePath.includes("texture_bg_underwater_caustics.png"));
    const params = clip?.effects.find((effect) => effect.effectId === "cutvoke.transform")?.params;
    return params ? { scale: params.scale, position: params.position } : null;
  };
  await expect.poll(textureTransform).toEqual({ scale: 2.22, position: { x: -20, y: -55 } });

  await page.reload();
  await openProject(page, name);
  await expect.poll(textureTransform).toEqual({ scale: 2.22, position: { x: -20, y: -55 } });
});

test("generated educational stickers preview, apply and survive reopening", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const category = page.getByLabel("贴纸分类");
  await category.selectOption("学习科普");
  await expect(page.getByText("16 / 612")).toBeVisible();
  const card = page.locator(".sticker-card").filter({ hasText: "显微镜" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const preview = card.locator("video");
  await expect(preview).toBeVisible();
  await preview.hover();
  await expect.poll(() => preview.evaluate((element) => (element as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0.1);
  await card.getByRole("button", { name: "添加贴纸 显微镜" }).click();
  await expect(page.getByText("已添加贴纸「显微镜」到独立叠加轨")).toBeVisible();

  const hasEducationSticker = async () => (await readProject(request, id)).sequence.tracks
    .flatMap((track) => track.clips)
    .some((clip) => clip.assetRef.sourcePath.includes("learn_microscope.png"));
  await expect.poll(hasEducationSticker).toBe(true);
  await page.reload();
  await openProject(page, name);
  expect(await hasEducationSticker()).toBe(true);
});

test("mirror and flip presets expose editable axis controls that survive undo and reload", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();

  const effectPicker = page.locator("details.inspector__fx-picker");
  await expect(effectPicker).toBeVisible();
  await effectPicker.locator("summary").click();
  await page.getByRole("button", { name: "水平镜像" }).click();

  const axis = page.getByLabel("翻转轴向");
  await expect(axis).toHaveValue("horizontal");
  await expect(axis.locator("option")).toHaveText(["水平", "垂直", "水平 + 垂直"]);
  await axis.selectOption("vertical");

  const axisOf = async () => {
    const project = await readProject(request, id);
    return project.sequence.tracks[0].clips.find((clip) => clip.id === "color-0")
      ?.effects.find((effect) => effect.effectId === "cutvoke.fx.mirror")?.params?.axis;
  };
  await expect.poll(axisOf).toBe("vertical");
  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(axisOf).toBe("horizontal");
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(axisOf).toBe("vertical");

  await page.reload();
  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();
  await expect(page.getByLabel("翻转轴向")).toHaveValue("vertical");
  await expect.poll(axisOf).toBe("vertical");
});

test("local ASR review edits word timing and commits captions with undo and reload", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  const sourceClip = (await readProject(request, id)).sequence.tracks[0].clips[0];
  const signature = sourceSignatureForTest(sourceClip);
  const result = {
    clipId: sourceClip.id,
    sourceSignature: signature,
    model: "tiny",
    device: "cpu",
    computeType: "int8",
    language: "zh",
    duration: 5,
    segmentCount: 2,
    segments: [
      {
        index: 1, text: "今天阳光很好",
        start: { num: "1", den: "1" }, end: { num: "2", den: "1" },
        words: [
          { text: "今天", start: { num: "1", den: "1" }, end: { num: "7", den: "5" } },
          { text: "阳光很好", start: { num: "7", den: "5" }, end: { num: "2", den: "1" } },
        ],
      },
      {
        index: 2, text: "继续向前走",
        start: { num: "2", den: "1" }, end: { num: "3", den: "1" },
        words: [
          { text: "继续", start: { num: "2", den: "1" }, end: { num: "12", den: "5" } },
          { text: "向前走", start: { num: "12", den: "5" }, end: { num: "3", den: "1" } },
        ],
      },
    ],
  };
  const completed = {
    jobId: "asr-ui-e2e",
    projectId: id,
    clipId: sourceClip.id,
    model: "tiny",
    requestedLanguage: "zh",
    status: "completed",
    phase: "识别完成",
    progress: 100,
    error: "",
    result,
  };
  let startPayload: { clipId: string; model: string; language: string } | null = null;
  await page.route("**/api/v1/asr/status", (route) => route.fulfill({ json: {
    installed: true, ready: true, defaultModel: "base",
    models: ["tiny", "base", "small", "medium"],
    devicePreference: "auto", devices: ["auto", "cpu", "cuda"],
    languages: ["auto", "zh", "en", "ja", "ko"], message: "测试识别服务已就绪",
  } }));
  await page.route(`**/api/v1/projects/${id}/asr-jobs`, async (route) => {
    startPayload = route.request().postDataJSON();
    await route.fulfill({ json: {
      ...completed, status: "queued", phase: "排队中", progress: 0, result: null,
    } });
  });
  await page.route("**/api/v1/asr-jobs/asr-ui-e2e", (route) => route.fulfill({ json: completed }));

  await openProject(page, name);
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();
  const creativeTabs = page.getByRole("tablist", { name: "创作域" });
  await creativeTabs.getByRole("tab", { name: "字幕" }).click();
  const panel = page.getByRole("region", { name: "自动字幕" });
  await expect(panel.getByText("测试识别服务已就绪")).toBeVisible();
  await panel.getByLabel("识别语言").selectOption("zh");
  await panel.getByLabel("识别模型").selectOption("tiny");
  await panel.getByRole("button", { name: "开始识别" }).click();
  await expect(panel.locator(".cv-asr__review strong")).toContainText("识别结果：2 条 · 语言 zh · tiny · CPU int8");
  expect(startPayload).toEqual({ clipId: sourceClip.id, model: "tiny", language: "zh" });

  await panel.getByLabel("第 1 条识别文字").fill("人工校对后的字幕");
  await panel.getByLabel("第 2 条结束时间").fill("3.5");
  await panel.getByRole("checkbox", { name: "逐词高亮" }).check();
  await panel.getByLabel("逐词高亮颜色").fill("#00ff00");
  await panel.getByRole("button", { name: "添加 2 条到工程" }).click();

  const generatedCaptions = async () => (await readProject(request, id)).sequence.captions
    .filter((caption) => caption.text === "人工校对后的字幕" || caption.text === "继续向前走");
  await expect.poll(generatedCaptions).toHaveLength(2);
  const saved = await generatedCaptions();
  expect(saved.find((caption) => caption.text === "人工校对后的字幕")?.words).toEqual([]);
  const timed = saved.find((caption) => caption.text === "继续向前走")!;
  expect(timed.wordHighlightColor).toBe("#00FF00");
  expect(timed.words).toHaveLength(2);
  expect(seconds(timed.words![0].start)).toBeCloseTo(2);
  expect(seconds(timed.words![1].end)).toBeCloseTo(3.5);

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(generatedCaptions).toHaveLength(0);
  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(generatedCaptions).toHaveLength(2);
  await page.reload();
  await openProject(page, name);
  await creativeTabs.getByRole("tab", { name: "字幕" }).click();
  await expect(panel.getByText("自动字幕 · 本地语音识别")).toBeVisible();
  expect(await generatedCaptions()).toHaveLength(2);
});

test("scrapbook decor atlas previews, exposes provenance, and inserts a saved overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await expect(category).toBeVisible();
  await category.selectOption("装饰");
  await expect(page.getByText("28 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "鼠尾草折角纸片" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("scrapbook-decor-atlas-20260927-v2.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 鼠尾草折角纸片" }).click();
  await expect(page.getByText("已添加贴纸「鼠尾草折角纸片」到独立叠加轨")).toBeVisible();
  const stickerClip = () => readProject(request, id).then((project) => project.sequence.tracks
    .flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("scrapbook_corner_sprig.png")));
  await expect.poll(stickerClip).toMatchObject({
    assetRef: { resourceRef: {
      resourceId: "cutvoke.sticker.scrapbook_corner_sprig",
      packVersion: await getBuiltinPackVersion(request),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    } },
  });
  await expect.poll(async () => (await stickerClip())?.effects
    .find((effect) => effect.effectId === "cutvoke.transform")?.params?.scale).toBe(0.28);

  await page.reload();
  await openProject(page, name);
  expect(await stickerClip()).toBeTruthy();
});

test("natural-light atlas previews, exposes provenance, and inserts a full-frame overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await category.selectOption("自然光影");
  await expect(page.getByText("16 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "金色窗光光束" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("natural-light-shadow-overlay-atlas-20260927.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 金色窗光光束" }).click();
  await expect(page.getByText("已添加贴纸「金色窗光光束」到独立叠加轨")).toBeVisible();
  const stickerClip = () => readProject(request, id).then((project) => project.sequence.tracks
    .flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("natural_light_window_beams.png")));
  await expect.poll(stickerClip).toMatchObject({
    assetRef: { resourceRef: {
      resourceId: "cutvoke.sticker.natural_light_window_beams",
      packVersion: await getBuiltinPackVersion(request),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    } },
  });
  await expect.poll(async () => (await stickerClip())?.effects
    .find((effect) => effect.effectId === "cutvoke.transform")?.params?.scale).toBe(0.92);

  await page.reload();
  await openProject(page, name);
  expect(await stickerClip()).toBeTruthy();
});

test("video-guidance atlas previews, exposes provenance, and inserts a sticker", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await category.selectOption("指引");
  await expect(page.getByText("29 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "青色弧线指引箭头" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("video-annotation-guide-sticker-atlas-20260927.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 青色弧线指引箭头" }).click();
  await expect(page.getByText("已添加贴纸「青色弧线指引箭头」到独立叠加轨")).toBeVisible();
  const stickerClip = () => readProject(request, id).then((project) => project.sequence.tracks
    .flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("guide_pointer_arrow.png")));
  await expect.poll(stickerClip).toMatchObject({
    assetRef: { resourceRef: {
      resourceId: "cutvoke.sticker.guide_pointer_arrow",
      packVersion: await getBuiltinPackVersion(request),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    } },
  });

  await page.reload();
  await openProject(page, name);
  expect(await stickerClip()).toBeTruthy();
});

test("energy-light atlas previews, exposes provenance, and inserts a saved overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await category.selectOption("能量光效");
  await expect(page.getByText("16 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "青色电弧" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("energy-light-overlay-atlas-20260927.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 青色电弧" }).click();
  await expect(page.getByText("已添加贴纸「青色电弧」到独立叠加轨")).toBeVisible();
  const stickerClip = () => readProject(request, id).then((project) => project.sequence.tracks
    .flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("energy_arc_cyan.png")));
  await expect.poll(stickerClip).toMatchObject({
    assetRef: { resourceRef: {
      resourceId: "cutvoke.sticker.energy_arc_cyan",
      packVersion: await getBuiltinPackVersion(request),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    } },
  });
  await expect.poll(async () => (await stickerClip())?.effects
    .find((effect) => effect.effectId === "cutvoke.transform")?.params?.scale).toBe(0.92);

  await page.reload();
  await openProject(page, name);
  expect(await stickerClip()).toBeTruthy();
});

test("stage-light atlas previews, exposes provenance, and inserts a scaled overlay", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await category.selectOption("舞台灯光");
  await expect(page.getByText("16 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "青紫激光扇" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("concert-stage-light-atlas-20260927.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 青紫激光扇" }).click();
  await expect(page.getByText("已添加贴纸「青紫激光扇」到独立叠加轨")).toBeVisible();
  const stickerClip = () => readProject(request, id).then((project) => project.sequence.tracks
    .flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("stage_light_laser_fan_cyan.png")));
  await expect.poll(stickerClip).toMatchObject({
    assetRef: { resourceRef: {
      resourceId: "cutvoke.sticker.stage_light_laser_fan_cyan",
      packVersion: await getBuiltinPackVersion(request),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    } },
  });
  await expect.poll(async () => (await stickerClip())?.effects
    .find((effect) => effect.effectId === "cutvoke.transform")?.params?.scale).toBe(1.35);

  await page.reload();
  await openProject(page, name);
  expect(await stickerClip()).toBeTruthy();
});

test("fantasy-energy atlas previews, exposes provenance, and inserts a sticker", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await category.selectOption("特效贴图");
  await expect(page.getByText("95 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "紫晶传送门碎片" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("fantasy-energy-overlay-atlas-20260927.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 紫晶传送门碎片" }).click();
  await expect(page.getByText("已添加贴纸「紫晶传送门碎片」到独立叠加轨")).toBeVisible();
  const stickerClip = () => readProject(request, id).then((project) => project.sequence.tracks
    .flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("fxoverlay_fantasy_violet_portal.png")));
  await expect.poll(stickerClip).toMatchObject({
    assetRef: { resourceRef: {
      resourceId: "cutvoke.sticker.fxoverlay_fantasy_violet_portal",
      packVersion: await getBuiltinPackVersion(request),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    } },
  });
  await expect.poll(async () => (await stickerClip())?.effects
    .find((effect) => effect.effectId === "cutvoke.transform")?.params?.scale).toBe(0.92);

  await page.reload();
  await openProject(page, name);
  expect(await stickerClip()).toBeTruthy();
});

test("product-promo atlas previews, exposes provenance, and inserts a sticker", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "贴纸" }).click();

  const stickerLibrary = page.getByRole("region", { name: "贴纸库" });
  const category = stickerLibrary.getByLabel("贴纸分类");
  await category.selectOption("产品广告");
  await expect(page.getByText("16 / 612")).toBeVisible();

  const card = stickerLibrary.locator(".sticker-card").filter({ hasText: "珍珠轨道环" });
  await expect(card).toHaveCount(1);
  await expect(card.locator("small")).toContainText("合格");
  const provenance = card.locator(".sticker-card__provenance");
  await provenance.locator("summary").click();
  await expect(provenance).toContainText("product-promo-overlay-atlas-20260927.png");
  await expect(card.locator("video")).toBeVisible();

  await card.getByRole("button", { name: "添加贴纸 珍珠轨道环" }).click();
  await expect(page.getByText("已添加贴纸「珍珠轨道环」到独立叠加轨")).toBeVisible();
  const stickerClip = () => readProject(request, id).then((project) => project.sequence.tracks
    .flatMap((track) => track.clips)
    .find((clip) => clip.assetRef.sourcePath.includes("promo_orbit_rings.png")));
  await expect.poll(stickerClip).toMatchObject({
    assetRef: { resourceRef: {
      resourceId: "cutvoke.sticker.promo_orbit_rings",
      packVersion: await getBuiltinPackVersion(request),
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    } },
  });
  await expect.poll(async () => (await stickerClip())?.effects
    .find((effect) => effect.effectId === "cutvoke.transform")?.params?.scale).toBe(0.86);

  await page.reload();
  await openProject(page, name);
  expect(await stickerClip()).toBeTruthy();
});
