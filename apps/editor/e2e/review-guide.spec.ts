import { execFileSync } from "node:child_process";
import { expect, test, type Page } from "@playwright/test";

function pixel(png: Buffer, x: number, y: number): number[] {
  return JSON.parse(execFileSync("python", ["-c",
    "from PIL import Image; from io import BytesIO; import sys,json; print(json.dumps(Image.open(BytesIO(sys.stdin.buffer.read())).convert('RGB').getpixel((int(sys.argv[1]),int(sys.argv[2])))))",
    String(x), String(y)], { input: png, encoding: "utf8" }));
}

async function png(page: Page, color: string) {
  const data = await page.evaluate((fill) => {
    const canvas = document.createElement("canvas"); canvas.width = 1080; canvas.height = 1920;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = fill;
    ctx.fillRect(790, 1516, 41, 41);
    return canvas.toDataURL("image/png").split(",")[1];
  }, color);
  return Buffer.from(data, "base64");
}

const magenta = [255, 0, 255], yellow = [255, 255, 0];

test("review guide follows visible portrait pixels and stays separate from the timeline", async ({ page, request }) => {
  const id = `guide-${crypto.randomUUID()}`;
  const project = `/api/v1/projects/${id}`;
  const fixture = (await (await request.get("/api/v1/projects/e2e-fixture")).json());
  const source = fixture.sequence.tracks[0].clips[0].assetRef.sourcePath;
  expect((await request.post("/api/v1/projects", { data: {
    name: id, projectId: id, width: 180, height: 320, fps: 12,
  } })).ok()).toBeTruthy();
  const revision = (await (await request.get(project)).json()).revision;
  expect((await request.post(`${project}/commands`, { data: {
    type: "clip.insert", expectedRevision: revision, commandId: crypto.randomUUID(),
    payload: { trackId: "v", createTrackKind: "video", clipId: "portrait", sourcePath: source,
      timelineStart: { num: "0", den: "1" }, timelineEnd: { num: "2", den: "1" } },
  } })).ok()).toBeTruthy();
  const original = await (await request.get(project)).json();
  await page.setViewportSize({ width: 1800, height: 1300 });
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${id}`, exact: true }).click();
  await page.getByRole("button", { name: "仅预览参考线", exact: true }).click();
  const overlay = page.getByRole("img", { name: "仅预览参考线", exact: true });
  const input = page.getByLabel("选择参考线 PNG", { exact: true });
  await expect(input).toBeEnabled();
  await expect(overlay).toHaveCount(0);
  const frame = page.locator(".player__canvas-frame");
  const dialog = page.getByRole("dialog", { name: "仅预览参考线", exact: true });
  async function setGuide(color: string) {
    const request = page.waitForResponse(response =>
      response.request().method() === "POST" && response.url().includes(`${project}/review-guide/`));
    await input.setInputFiles({ name: `${color}.png`, mimeType: "image/png", buffer: await png(page, color) });
    expect((await request).ok()).toBeTruthy();
    await expect(overlay).toBeVisible();
  }
  await setGuide("magenta");
  await page.getByRole("button", { name: "仅预览参考线", exact: true }).click();
  await expect(dialog).toHaveCount(0);

  let lastTestWidth = 0;
  for (const [w, h] of [[960, 540], [360, 800]]) {
    await frame.evaluate((node: HTMLElement, [width,height]) => {
      node.style.setProperty("width", width+"px", "important");
      node.style.setProperty("height", height+"px", "important");
      node.style.setProperty("max-width", "none", "important");
      node.style.setProperty("max-height", "none", "important");
      node.style.setProperty("flex-shrink", "0", "important");
    }, [w,h]);
    const bbox = (await frame.boundingBox())!;
    expect(Math.abs(bbox.width-w)).toBeLessThan(1);
    expect(Math.abs(bbox.height-h)).toBeLessThan(1);
    const scale = Math.min(w/180, h/320), visibleW = 180*scale, visibleH = 320*scale;
    const x = Math.round((w-visibleW)/2 + visibleW*.75);
    const y = Math.round((h-visibleH)/2 + visibleH*.8);
    await expect.poll(async () => pixel(await frame.screenshot(),x,y)).toEqual(magenta);
    expect(pixel(await frame.screenshot(),Math.round(w*.75),Math.round(h*.8))).not.toEqual(magenta);
    lastTestWidth = w;
  }
  expect(lastTestWidth).toBe(360);
  await page.getByRole("button", { name: "仅预览参考线", exact: true }).click();
  await setGuide("yellow");
  await page.getByRole("button", { name: "仅预览参考线", exact: true }).click();
  await expect.poll(async () => pixel(await frame.screenshot(),270,592)).toEqual(yellow);
  await page.getByRole("button", { name: "仅预览参考线", exact: true }).click();
  await page.getByRole("button", { name: "显示参考线", exact: true }).click();
  await expect(overlay).toHaveCount(0);
  await page.getByRole("button", { name: "移除参考线", exact: true }).click();
  await expect(page.getByRole("button", { name: "移除参考线", exact: true })).toBeDisabled();
  expect(await (await request.get(project)).json()).toEqual(original);
});
