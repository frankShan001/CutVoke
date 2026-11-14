import { expect, test, type APIRequestContext } from "@playwright/test";

async function fixture(request: APIRequestContext) {
  const seed = await (await request.get("/api/v1/projects/e2e-fixture")).json();
  const id = `resource-race-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name: id, width: 160, height: 90, fps: 15 } })).ok()).toBeTruthy();
  const read = async () => (await request.get(`/api/v1/projects/${id}`)).json();
  for (let index = 0; index < 2; index++) {
    const project = await read();
    const response = await request.post(`/api/v1/projects/${id}/commands`, { data: {
      type: "clip.insert", payload: { clipId: `shot-${index + 1}`, trackId: "v1", ...(index === 0 ? { createTrackKind: "video" } : {}),
        sourcePath: seed.sequence.tracks[0].clips[index].assetRef.sourcePath,
        timelineStart: { num: String(index * 2), den: "1" }, timelineEnd: { num: String(index * 2 + 2), den: "1" } },
      expectedRevision: project.revision, commandId: `seed-${index}`, actor: { kind: "human", id: "resource-race" },
    } });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  return { id, read };
}

for (const changeSelection of [false, true]) {
test(`late resource completion keeps transition seams scoped to the initiating selection (${changeSelection ? "superseded" : "current"})`, async ({ page, request }) => {
  const { id, read } = await fixture(request);
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${id}`, exact: true }).click();
  const first = page.locator('.clip-block[data-clip-id="shot-1"]');
  const second = page.locator('.clip-block[data-clip-id="shot-2"]');
  await first.click();
  let release!: () => void, committed!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const stored = new Promise<void>(resolve => { committed = resolve; });
  await page.route(`**/api/v1/projects/${id}/commands`, async route => {
    if (route.request().postDataJSON().type !== "effect.setTransition") return route.continue();
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    committed(); await gate; await route.fulfill({ response });
  });
  try {
    const dataTransfer = await page.evaluateHandle(() => {
      const data = new DataTransfer();
      data.setData("application/x-cutvoke-transition", JSON.stringify({ effectId: "cutvoke.transition.crossfade", duration: .5 }));
      return data;
    });
    await page.getByRole("button", { name: "在 2.00 秒的片段接缝添加转场" }).dispatchEvent("drop", { dataTransfer });
    await stored;
    const tabs = page.getByRole("tablist", { name: "工程编辑面板" });
    if (changeSelection) {
      await first.click(); // Even reselecting the same clip is newer explicit intent.
      await tabs.getByRole("tab", { name: "变速", exact: true }).click();
    }
    release();
    await expect(page.getByText("已应用转场", { exact: true })).toBeVisible();
    // A transition selects its own marker; its owning video block is not selected.
    await expect(page.locator(".transition-marker__select")).toHaveAttribute("aria-pressed", String(!changeSelection));
    if (changeSelection) {
      await expect(first).toHaveClass(/clip-block--selected/);
      await expect(second).not.toHaveClass(/clip-block--selected/);
    } else await expect(page.getByLabel("后一段片段（转场挂此段）")).toHaveValue("shot-2");
    await expect(tabs.getByRole("tab", { name: changeSelection ? "变速" : "转场参数", exact: true })).toHaveAttribute("aria-selected", "true");
    expect((await read()).sequence.tracks[0].clips[1].effects.some((effect: { effectId: string }) => effect.effectId === "cutvoke.transition.crossfade")).toBe(true);
    await page.getByRole("button", { name: "撤销", exact: true }).click();
    await expect.poll(async () => (await read()).sequence.tracks[0].clips[1].effects.length).toBe(0);
  } finally { release(); }
});
}

for (const usePreset of [false, true]) for (const delayPoint of ["command", "sync"] as const) {
test(`late resource completion preserves a newer clip selection and animation target (${usePreset ? "preset" : "effect"}, ${delayPoint})`, async ({ page, request }) => {
  const { id, read } = await fixture(request);
  // Catalog preparation is fixture setup; the race uses real saved commands.
  expect((await request.get("/api/v1/presets")).ok()).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${id}`, exact: true }).click();
  const first = page.locator('.clip-block[data-clip-id="shot-1"]');
  const second = page.locator('.clip-block[data-clip-id="shot-2"]');
  await first.click();
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "滤镜" }).click();
  const cards = page.getByRole("region", { name: "效果库", exact: true }).locator(".resource-effect-card");
  const card = usePreset ? cards.filter({ has: page.locator('option:not([value=""]):not([disabled])') }).first() : cards.first();
  await expect(card).toBeVisible();
  const style = card.locator("select");
  const presetId = usePreset ? await style.locator('option:not([value=""]):not([disabled])').first().getAttribute("value") : "";
  await style.selectOption(presetId!);

  let release!: () => void, committed!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const stored = new Promise<void>(resolve => { committed = resolve; });
  let held = false, refreshing = false, released = false;
  let animationTarget: string | undefined;
  await page.route(`**/api/v1/projects/${id}/sync**`, async route => {
    if (delayPoint === "sync" && refreshing && !released) {
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      committed();
      await gate;
      await route.fulfill({ response });
    } else await route.continue();
  });
  await page.route(`**/api/v1/projects/${id}/commands`, async route => {
    const body = route.request().postDataJSON();
    if (body.type === "effect.setAnimation") animationTarget = body.payload.clipId;
    if (!held && (body.type === "builtinPreset.apply" || body.type === "effect.add")) {
      held = true;
      expect(body.type).toBe(usePreset ? "builtinPreset.apply" : "effect.add");
      const response = await route.fetch();
      expect(response.ok()).toBeTruthy();
      refreshing = true;
      if (delayPoint === "command") { committed(); await gate; }
      await route.fulfill({ response });
    } else await route.continue();
  });
  try {
    await card.press("Enter");
    await stored;
    expect((await read()).sequence.tracks[0].clips[0].effects.some((effect: { effectId: string }) => effect.effectId.startsWith("cutvoke.fx."))).toBe(true);
    await second.click();
    await expect(second).toHaveClass(/clip-block--selected/);
    const acknowledged = page.waitForResponse(response => delayPoint === "sync"
      ? response.url().includes(`/projects/${id}/sync`)
      : response.url().endsWith(`/projects/${id}/commands`) && ["builtinPreset.apply", "effect.add"].includes(response.request().postDataJSON()?.type));
    released = true;
    release();
    await (await acknowledged).finished();
    await expect(page.locator(".statusbar__save")).toHaveClass(/statusbar__save--saved/);
    await page.getByRole("tablist", { name: "工程编辑面板" }).getByRole("tab", { name: "片段属性" }).click();
    await page.getByLabel("入场动画", { exact: true }).selectOption("cutvoke.anim.rotateIn");
    await expect.poll(() => animationTarget).toBe("shot-2");
    await expect(second).toHaveClass(/clip-block--selected/);
    await expect.poll(async () => (await read()).sequence.tracks[0].clips[1].effects
      .some((effect: { effectId: string }) => effect.effectId === "cutvoke.anim.rotateIn")).toBe(true);
    expect((await read()).sequence.tracks[0].clips[0].effects.some((effect: { effectId: string }) => effect.effectId.startsWith("cutvoke.anim."))).toBe(false);
  } finally { release(); }
});
}
