import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const endpoint = (id: string) => `/api/v1/projects/${id}`;
async function current(request: APIRequestContext, id: string) {
  return (await request.get(endpoint(id))).json();
}
async function fixture(request: APIRequestContext) {
  const id = `interactive-${crypto.randomUUID()}`;
  const source = (await (await request.get(endpoint("e2e-fixture"))).json()).sequence.tracks[0].clips[0].assetRef.sourcePath;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name: id, width: 160, height: 90, fps: 15 } })).ok()).toBeTruthy();
  for (const [type, payload] of [
    ["clip.insert", { trackId: "v", createTrackKind: "video", clipId: "clip", sourcePath: source,
      timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 2, den: 1 } }],
    ["effect.add", { clipId: "clip", effectId: "cutvoke.fx.punchIn", params: { zoom: 1.25 } }],
  ] as const) {
    const response = await request.post(`${endpoint(id)}/commands`, { data: {
      type, payload, expectedRevision: (await current(request, id)).revision, commandId: crypto.randomUUID(),
    } });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  return id;
}
async function open(page: Page, id: string) {
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${id}`, exact: true }).click();
  await expect(page.locator(".timeline")).toBeVisible();
}
async function pngs(page: Page) {
  return page.evaluate(() => Object.fromEntries(["red", "lime", "blue"].map(color => {
    const canvas = document.createElement("canvas"); canvas.width = 160; canvas.height = 90;
    const context = canvas.getContext("2d")!; context.fillStyle = color; context.fillRect(0, 0, 160, 90);
    return [color, canvas.toDataURL().split(",")[1]];
  })));
}
async function pixel(page: Page) {
  return page.locator(".player__interactive-scene").evaluate((canvas: HTMLCanvasElement) =>
    [...canvas.getContext("2d")!.getImageData(80, 45, 1, 1).data]);
}
const gate = () => {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
};

test("interactive rendering retries a failed latest frame with a real pointer click", async ({ page, request }) => {
  const id = await fixture(request);
  await page.goto("/");
  const images = await pngs(page);
  let failed = true, attempts = 0;
  await page.route(`**/projects/${id}/interactive-frame`, async route => {
    attempts++;
    if (failed) return route.fulfill({ status: 503, json: { error: { message: "recoverable renderer failure" } } });
    return route.fulfill({ contentType: "image/png", body: Buffer.from(images.lime, "base64") });
  });
  await open(page, id);
  await expect(page.locator(".player__interactive-status[role='alert']")).toContainText("recoverable renderer failure");
  await expect(page.locator(".player__interactive-scene")).toHaveCSS("pointer-events", "none");
  const before = await current(request, id), count = attempts;
  failed = false;
  await page.getByRole("button", { name: "重试预览", exact: true }).click();
  await expect.poll(() => attempts).toBeGreaterThan(count);
  await expect(page.locator(".player__interactive-scene")).toHaveAttribute("data-ready", "true");
  await expect.poll(() => pixel(page)).toEqual([0, 255, 0, 255]);
  await expect(page.locator(".player__interactive-status[role='alert']")).toHaveCount(0);
  expect(await current(request, id)).toEqual(before);
});

test("rapid edits abort obsolete work and a late response cannot replace the latest pixels", async ({ page, request }) => {
  const id = await fixture(request);
  await page.goto("/");
  const images = await pngs(page), obsolete = gate();
  const seen: number[] = [], failures: string[] = [];
  page.on("requestfailed", req => { if (req.url().endsWith("interactive-frame")) failures.push(req.failure()?.errorText || ""); });
  await page.route(`**/projects/${id}/interactive-frame`, async route => {
    const body = route.request().postDataJSON();
    const zoom = Number(body.commands.find((edit: { payload: { params?: { zoom?: number } } }) => edit.payload.params?.zoom)?.payload.params.zoom || 1.25);
    seen.push(zoom);
    if (zoom === 1.4) await obsolete.promise;
    await route.fulfill({ contentType: "image/png", body: Buffer.from(zoom === 1.6 ? images.lime : images.blue, "base64") }).catch(() => {});
  });
  try {
    await open(page, id);
    await expect.poll(() => pixel(page)).toEqual([0, 0, 255, 255]);
    await page.locator(".clip-block--video").click();
    const zoom = page.getByRole("spinbutton", { name: "推近倍率", exact: true });
    await zoom.fill("1.4");
    await expect.poll(() => seen.includes(1.4)).toBe(true);
    await zoom.fill("1.5");
    await zoom.fill("1.6");
    await expect.poll(() => seen.includes(1.6)).toBe(true);
    await expect.poll(() => failures.length).toBeGreaterThan(0);
    await expect.poll(() => pixel(page)).toEqual([0, 255, 0, 255]);
    obsolete.release();
    await page.waitForTimeout(150); // Let the deliberately late response finish.
    expect(await pixel(page)).toEqual([0, 255, 0, 255]);
    await expect.poll(async () => (await current(request, id)).sequence.tracks[0].clips[0].effects[0].params.zoom).toBe(1.6);
    await expect(page.locator(".player__interactive-status[role='alert']")).toHaveCount(0);
  } finally { obsolete.release(); }
});

for (const debounceElapsed of [false, true]) {
  test(`returning A to B to A ${debounceElapsed ? "after debounce reissues the aborted frame" : "cancels the stale debounce without aborting the needed frame"}`, async ({ page, request }) => {
    const id = await fixture(request);
    await page.goto("/");
    const images = await pngs(page), first = gate();
    let requests = 0, aborted = 0;
    page.on("requestfailed", req => { if (req.url().endsWith("interactive-frame")) aborted++; });
    await page.route(`**/projects/${id}/interactive-frame`, async route => {
      requests++;
      await first.promise;
      await route.fulfill({ contentType: "image/png", body: Buffer.from(images.blue, "base64") }).catch(() => {});
    });
    try {
      await open(page, id);
      await expect.poll(() => requests).toBe(1);
      // The return must occur inside the 60ms debounce. Browser automation and
      // a loaded CI runner cannot promise that two key actions take <60ms.
      await page.clock.install();
      await page.clock.pauseAt(new Date(Date.now() + 100));
      const ruler = page.getByRole("slider", { name: "时间线播放头" });
      await ruler.focus();
      await ruler.press("ArrowRight");
      await expect(ruler).toHaveAttribute("aria-valuenow", String(1 / 15));
      if (debounceElapsed) {
        await page.clock.runFor(75);
        await expect.poll(() => aborted).toBe(1);
      }
      await ruler.press("Home");
      await expect(ruler).toHaveAttribute("aria-valuenow", "0");
      await page.clock.runFor(150); // Exceed the debounce with A still in flight.
      expect(aborted).toBe(debounceElapsed ? 1 : 0);
      first.release();
      await page.clock.resume();
      await expect(page.locator(".player__interactive-scene")).toHaveAttribute("data-ready", "true");
      await expect.poll(() => pixel(page)).toEqual([0, 0, 255, 255]);
      expect(requests).toBe(debounceElapsed ? 2 : 1);
    } finally { first.release(); }
  });
}

test("leaving the editor interrupts an in-flight interactive request and discards its late result", async ({ page, request }) => {
  const id = await fixture(request);
  await page.goto("/");
  const images = await pngs(page), pending = gate();
  let started = false, aborted = false;
  page.on("requestfailed", req => { if (req.url().endsWith("interactive-frame")) aborted = true; });
  await page.route(`**/projects/${id}/interactive-frame`, async route => {
    started = true;
    await pending.promise;
    await route.fulfill({ contentType: "image/png", body: Buffer.from(images.red, "base64") }).catch(() => {});
  });
  try {
    await open(page, id);
    await expect.poll(() => started).toBe(true);
    const before = await current(request, id);
    await page.getByRole("button", { name: "返回工程列表", exact: true }).click();
    await expect.poll(() => aborted).toBe(true);
    pending.release();
    await expect(page.locator(".player__interactive-scene")).toHaveCount(0);
    await expect(page.getByRole("button", { name: `打开工程 ${id}`, exact: true })).toBeVisible();
    expect(await current(request, id)).toEqual(before);
  } finally { pending.release(); }
});

test("healthy native playback hides server-video buffering while keeping its pixels and geometry", async ({ page, request }) => {
  const id = await fixture(request);
  // A transform-only clip uses the real source decoder instead of an RPC frame.
  const project = await current(request, id);
  expect((await request.post(`${endpoint(id)}/commands`, { data: { type: "effect.remove",
    expectedRevision: project.revision, payload: { clipId: "clip", effectId: "cutvoke.fx.punchIn" } } })).ok()).toBeTruthy();
  await open(page, id);
  const scene = page.locator(".player__interactive-scene"), video = page.locator("video.player__video");
  await expect(scene).toHaveAttribute("data-ready", "true");
  const before = await scene.boundingBox();
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await video.evaluate((element: HTMLVideoElement) => element.play());
  await video.dispatchEvent("waiting");
  await page.waitForTimeout(250);
  await expect(page.getByText("播放缓冲中…", { exact: true })).toBeHidden();
  await expect(scene).toHaveAttribute("data-ready", "true");
  expect((await pixel(page))[0]).toBeGreaterThan(240);
  expect(await scene.boundingBox()).toEqual(before);
  await video.dispatchEvent("canplay");
});
