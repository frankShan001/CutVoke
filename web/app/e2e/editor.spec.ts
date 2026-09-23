import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

type Project = {
  projectId: string;
  revision: string;
  sequence: {
    tracks: Array<{ id: string; clips: Array<{
      id: string;
      assetRef: { sourcePath: string };
      timelineStart: { num: string; den: string };
      timelineEnd: { num: string; den: string };
      effects: Array<{ effectId: string }>;
    }> }>;
    captions: Array<{ id: string; text: string }>;
  };
};

const apiPath = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;

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

async function createProject(request: APIRequestContext): Promise<{ id: string; name: string; sources: string[] }> {
  const fixture = await readProject(request, "e2e-fixture");
  const sources = fixture.sequence.tracks[0].clips.map((clip) => clip.assetRef.sourcePath);
  const id = `browser-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `回归 ${id}`;
  const response = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 15 },
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

async function openProject(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
}

async function clipOrder(request: APIRequestContext, id: string): Promise<string[]> {
  const project = await readProject(request, id);
  return [...project.sequence.tracks[0].clips]
    .sort((a, b) => Number(a.timelineStart.num) / Number(a.timelineStart.den)
      - Number(b.timelineStart.num) / Number(b.timelineStart.den))
    .map((clip) => clip.id);
}

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

test("short windows follow the ruler, stop at the end, and survive an Agent subtitle edit", async ({ page, request }) => {
  const { id, name } = await createProject(request);
  await openProject(page, name);
  const ruler = page.getByRole("slider", { name: "时间线播放头" });
  const bounds = await ruler.boundingBox();
  expect(bounds).toBeTruthy();
  const video = page.locator("video.player__video");
  await page.mouse.click(bounds!.x + 7.6 * 40, bounds!.y + bounds!.height / 2);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow"))).toBeGreaterThan(8.25);
  await expect(video).toHaveAttribute("src", /preview-window\?index=1/);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await page.mouse.click(bounds!.x + 9 * 40, bounds!.y + bounds!.height / 2);
  await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow"))).toBeCloseTo(9, 0);
  await expect(video).toHaveAttribute("src", /preview-window\?index=1/);
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
  expect(await video.evaluate((element) => (element as HTMLVideoElement).currentTime)).toBeGreaterThan(1.8);
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
  await expect(page.getByText("Agent 正在编辑")).toBeVisible();
  await expect.poll(async () => video.evaluate((element) => element ===
    (window as Window & { oldVideo?: Element }).oldVideo)).toBe(true);
  expect(await video.getAttribute("src")).toBe(before);
  await page.keyboard.press("Control+z");
  expect((await readProject(request, id)).revision).toBe((await patch.json()).revision);
  const release = await request.post(`${apiPath(id)}/edit-lock/release`, { data: { leaseId } });
  expect(release.ok()).toBeTruthy();
  await expect(page.getByText("Agent 正在编辑")).toBeHidden();

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
  await page.getByRole("tab", { name: "字幕" }).click();
  await expect(page.getByText("AI 已修正")).toBeVisible();
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
  if (await page.getByRole("button", { name: `打开工程 ${name}` }).isVisible()) {
    await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  }
  await expect(page.getByRole("button", { name: /转场，位于/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /自然增色.*位于/ })).toBeVisible();
});
