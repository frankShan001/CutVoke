import { test, expect, type APIRequestContext } from "@playwright/test";
import { createRequire } from "node:module";
const { verifyNativePreview } = createRequire(import.meta.url)("../../../tools/verify/verify_native_preview.cjs");

async function fixture(request: APIRequestContext) {
  const sourceProject = await (await request.get("/api/v1/projects/e2e-fixture")).json();
  const id = `lookahead-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const name = `预览预取 ${id}`;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name, width: 160, height: 90, fps: 15 } })).status()).toBe(201);
  const apply = async (type: string, payload: object) => {
    const project = await (await request.get(`/api/v1/projects/${id}`)).json();
    const response = await request.post(`/api/v1/projects/${id}/commands`, { data: {
      type, payload, expectedRevision: project.revision, commandId: crypto.randomUUID(),
      actor: { kind: "human", id: "lookahead-test" },
    } });
    expect(response.ok(), await response.text()).toBeTruthy();
  };
  await apply("track.add", { trackId: "v", kind: "video" });
  for (const [index, clip] of sourceProject.sequence.tracks[0].clips.entries()) {
    await apply("clip.insert", { trackId: "v", clipId: `v${index}`, sourcePath: clip.assetRef.sourcePath,
      timelineStart: { num: index * 5, den: 1 }, timelineEnd: { num: (index + 1) * 5, den: 1 } });
  }
  return { id, name };
}

test("idle pause prepares a bounded playback lead and playback reuses it", async ({ page, request }) => {
  const { id, name } = await fixture(request);
  await page.route(`**/projects/${id}/preview-jobs`, route => route.fulfill({ status: 404, contentType: "application/json", body: "{}" }));
  const requests: URL[] = [];
  const completed = new Set<number>();
  page.on("request", outgoing => {
    const url = new URL(outgoing.url());
    if (url.pathname === `/api/v1/projects/${id}/preview-window`) requests.push(url);
  });
  page.on("response", response => {
    const url = new URL(response.url());
    if (url.pathname === `/api/v1/projects/${id}/preview-window` && response.ok()) completed.add(Number(url.searchParams.get("index")));
  });
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await expect.poll(() => completed.has(3), { timeout: 20_000 }).toBeTruthy();
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeVisible();
  expect(requests.filter(url => Number(url.searchParams.get("index")) > 0).every(url => url.searchParams.get("prefetch") === "1")).toBeTruthy();
  expect(Math.max(...requests.map(url => Number(url.searchParams.get("index"))))).toBe(3);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(async () => Number(await page.getByRole("slider", { name: "时间线播放头" }).getAttribute("aria-valuenow"))).toBeGreaterThan(2.5);
  expect(requests.filter(url => url.searchParams.get("index") === "1")).toHaveLength(1);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
});

test("visual edits rebuild continuous playback without reopening the editor", async ({ page, request }) => {
  test.setTimeout(90_000);
  const { id, name } = await fixture(request);
  const jobs: string[] = [];
  page.on("response", async response => {
    if (response.request().method() === "POST" && response.url().endsWith(`/projects/${id}/preview-jobs`) && response.ok()) {
      jobs.push((await response.json()).jobId);
    }
  });
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await expect.poll(() => jobs.length).toBe(1);
  const project = await (await request.get(`/api/v1/projects/${id}`)).json();
  const changed = await request.post(`/api/v1/projects/${id}/commands`, { data: {
    type: "sequence.background", payload: { color: "#123456" },
    expectedRevision: project.revision, commandId: crypto.randomUUID(), actor: { kind: "human", id: "preview-edit-test" },
  } });
  expect(changed.ok(), await changed.text()).toBeTruthy();
  await expect.poll(() => jobs.length, { timeout: 15_000 }).toBe(2);
  await expect.poll(async () => (await (await request.get(`/api/v1/preview-jobs/${jobs[1]}`)).json()).state,
    { timeout: 40_000 }).toBe("completed");
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();
  await expect.poll(() => page.locator(`video[data-preview-source='/api/v1/preview-jobs/${jobs[1]}/media']`).count()).toBeGreaterThan(0);
});

test("native acceptance waits for a delayed decoder before playing a short clip", async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1024, height: 681 }); // macOS hosted-runner window
  const { id, name } = await fixture(request);
  const project = await (await request.get(`/api/v1/projects/${id}`)).json();
  const edited = await request.post(`/api/v1/projects/${id}/commands`, { data: {
    type: "edit.batch", expectedRevision: project.revision, commandId: crypto.randomUUID(),
    payload: { commands: [
      { type: "clip.remove", payload: { clipId: "v1" } },
      { type: "clip.remove", payload: { clipId: "v2" } },
      { type: "clip.trim", payload: { clipId: "v0", timelineEnd: { num: 3, den: 1 } } },
    ] },
  } });
  expect(edited.ok(), await edited.text()).toBeTruthy();
  await page.route(`**/projects/${id}/preview-jobs`, route => route.fulfill({ status: 404, json: {} }));
  let release!: () => void, blocked = 0;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**/projects/${id}/preview-window?*`, async route => {
    blocked++;
    await gate;
    await route.continue().catch(() => {});
  });
  try {
    await page.goto("/");
    await page.getByRole("button", { name: `打开工程 ${name}` }).click();
    await expect(page.getByRole("heading", { name: "时间线", exact: true })).toBeVisible();
    await expect.poll(() => blocked).toBeGreaterThan(0);
    const acceptance = verifyNativePreview(page, expect);
    void acceptance.catch(() => {}); // The assertions below can fail before it is awaited.
    // Exceed the entire clip duration. Acceptance must still be paused; an
    // eager click lets the healthy source/audio clock finish before decoding.
    await page.waitForTimeout(3500);
    await expect(page.getByRole("slider", { name: "时间线播放头", exact: true })).toHaveAttribute("aria-valuenow", "0");
    await expect(page.getByRole("button", { name: "播放", exact: true })).toBeVisible();
    release();
    const samples = await acceptance;
    expect(samples).toHaveLength(3);
  } finally { release(); }
});
