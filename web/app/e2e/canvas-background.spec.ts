import { expect, test } from "@playwright/test";

test("canvas background applies, undoes, redoes, and survives reload", async ({ page, request }) => {
  const id = `canvas-background-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `画布背景 ${id}`;
  const created = await request.post("/api/v1/projects", {
    data: { projectId: id, name, width: 160, height: 90, fps: 30 },
  });
  expect(created.status()).toBe(201);

  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible();

  await page.getByRole("button", { name: "设置画布背景" }).click();
  const dialog = page.getByRole("dialog", { name: "画布背景设置" });
  await dialog.getByRole("button", { name: "暖米白 #EFE7D8" }).click();
  await dialog.getByRole("button", { name: "应用" }).click();
  await expect.poll(async () => {
    const response = await request.get(`/api/v1/projects/${encodeURIComponent(id)}`);
    return (await response.json()).sequence.backgroundColor;
  }).toBe("#EFE7D8");
  const frame = page.locator(".player__canvas-frame");
  await expect(frame).toHaveCSS("background-color", "rgb(239, 231, 216)");

  await page.getByRole("button", { name: "撤销" }).click();
  await expect.poll(async () => {
    const response = await request.get(`/api/v1/projects/${encodeURIComponent(id)}`);
    return (await response.json()).sequence.backgroundColor;
  }).toBe("#000000");
  await expect(frame).toHaveCSS("background-color", "rgb(0, 0, 0)");

  await page.getByRole("button", { name: "重做" }).click();
  await expect.poll(async () => {
    const response = await request.get(`/api/v1/projects/${encodeURIComponent(id)}`);
    return (await response.json()).sequence.backgroundColor;
  }).toBe("#EFE7D8");

  await page.reload();
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.locator(".player__canvas-frame"))
    .toHaveCSS("background-color", "rgb(239, 231, 216)");
});
