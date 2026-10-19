import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

test("Vlog effects edit, preview, localize and retain authored transition parameters", async ({ page, request }) => {
  test.setTimeout(120_000);
  const id = `vlog-${crypto.randomUUID()}`;
  const name = `Vlog controls ${id}`;
  const route = `/api/v1/projects/${id}`;
  const current = async () => (await request.get(route)).json();
  const command = async (type: string, payload: object) => {
    const response = await request.post(`${route}/commands`, { data: {
      type, payload, expectedRevision: (await current()).revision,
      commandId: crypto.randomUUID(), actor: { kind: "human", id: "vlog-browser" },
    } });
    expect(response.ok(), await response.text()).toBeTruthy();
  };
  const fixture = await (await request.get("/api/v1/projects/e2e-fixture")).json();
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name, width: 160, height: 90, fps: 15 } })).ok()).toBeTruthy();
  await command("track.add", { trackId: "v", kind: "video" });
  for (let i = 0; i < 2; i++) await command("clip.insert", {
    clipId: `shot-${i}`, trackId: "v", sourcePath: fixture.sequence.tracks[0].clips[i].assetRef.sourcePath,
    timelineStart: { num: i*3, den: 1 }, timelineEnd: { num: (i+1)*3, den: 1 },
  });
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}`, exact: true }).click();
  await page.locator(".clip-block").filter({ hasText: "red.mp4" }).click();
  await page.locator(".inspector__fx-picker summary").click();
  await page.getByRole("button", { name: "强调推近并返回", exact: true }).click();
  const zoom = page.getByRole("spinbutton", { name: "推近倍率", exact: true });
  await expect(zoom).toHaveAttribute("max", "2.5");
  await zoom.fill("1.6"); await zoom.blur();
  const effect = async (clip: number, effectId: string) =>
    (await current()).sequence.tracks[0].clips[clip].effects.find((entry: { effectId: string }) => entry.effectId === effectId);
  await expect.poll(async () => (await effect(0, "cutvoke.fx.punchIn"))?.params.zoom).toBe(1.6);
  await page.getByTestId("language-select").selectOption("en");
  await expect(page.getByRole("spinbutton", { name: "Punch zoom", exact: true })).toHaveValue("1.6");
  await page.getByRole("button", { name: "Manual subject lock", exact: true }).click();
  const from = page.getByRole("spinbutton", { name: "Start X", exact: true });
  await from.fill("0.3"); await from.blur();
  await expect.poll(async () => (await effect(0, "cutvoke.fx.subjectLock"))?.params.fromX).toBe(.3);
  await page.getByTestId("language-select").selectOption("zh-CN");
  await page.getByRole("button", { name: "在 3.00 秒的片段接缝添加转场" }).click();
  await page.locator(".transition-options").getByRole("button", { name: "内容亮度揭示", exact: true }).click();
  await page.getByRole("checkbox", { name: "暗部优先", exact: true }).check();
  await page.getByRole("spinbutton", { name: "亮度软边", exact: true }).fill("0.14");
  await page.getByRole("combobox", { name: "亮度来源", exact: true }).selectOption("incoming");
  const before = await current();
  const previewRequest = page.waitForRequest(req => new URL(req.url()).pathname.endsWith("resource-preview") &&
    new URL(req.url()).searchParams.has("params"));
  await page.getByRole("button", { name: "预览当前转场", exact: true }).click();
  const query = new URL((await previewRequest).url()).searchParams;
  expect(JSON.parse(query.get("params")!)).toMatchObject({ softness: .14, reverse: true, keySource: "incoming" });
  await expect(page.locator(".resource-preview img")).toBeVisible();
  expect(await current()).toEqual(before);
  await page.getByRole("button", { name: "应用转场", exact: true }).click();
  await expect.poll(async () => (await effect(1, "cutvoke.transition.lumaReveal"))?.params)
    .toMatchObject({ softness: .14, reverse: true, keySource: "incoming" });
  await page.locator(".transition-options").getByRole("button", { name: "锚点匹配转场", exact: true }).click();
  await page.getByRole("spinbutton", { name: "前镜头锚点 X", exact: true }).fill("0.4");
  await page.getByRole("spinbutton", { name: "后镜头匹配角度", exact: true }).fill("-12");
  await page.getByRole("button", { name: "切换转场", exact: true }).click();
  await expect.poll(async () => (await effect(1, "cutvoke.transition.matchAlign"))?.params)
    .toMatchObject({ aX: .4, bAngle: -12 });
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect.poll(async () => (await effect(1, "cutvoke.transition.lumaReveal"))?.params.reverse).toBe(true);
  for (const lang of ["en", "de", "ar", "zh-CN"]) {
    await page.getByTestId("language-select").selectOption(lang);
    const catalog = await (await request.get(`/api/v1/effects?lang=${lang}`)).json();
    const spec = catalog.effects.find((entry: { effectId: string }) => entry.effectId === "cutvoke.transition.lumaReveal");
    await expect(page.getByRole("checkbox", { name: spec.parameters.properties.reverse["x-cutvoke-label"][lang], exact: true })).toBeChecked();
  }
  const evidence = resolve(process.cwd(), "../../.artifacts/acceptance/vlog-browser");
  mkdirSync(evidence, { recursive: true });
  await page.screenshot({ path: resolve(evidence, "transition-controls.png") });
  await page.reload();
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}`, exact: true }).click();
  expect((await effect(0, "cutvoke.fx.punchIn")).params.zoom).toBe(1.6);
  expect((await effect(0, "cutvoke.fx.subjectLock")).params.fromX).toBe(.3);
  expect((await effect(1, "cutvoke.transition.lumaReveal")).params.reverse).toBe(true);
});
