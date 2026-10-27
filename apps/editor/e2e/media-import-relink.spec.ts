import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const evidence = resolve("../../.artifacts/basic-fixes-20261024/import");
const large = join(evidence, "large-valid.mp4");
const sticker = fileURLToPath(new URL("../../../src/cutvoke/assets/stickers/decor_sparkles.png", import.meta.url));
test.beforeAll(() => {
  mkdirSync(evidence, { recursive: true });
  if (!existsSync(large)) execFileSync("ffmpeg", ["-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=2.5",
    "-vf", "noise=alls=100:allf=t+u", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0", "-pix_fmt", "yuv420p", large]);
  expect(statSync(large).size).toBeGreaterThan(16 * 1024 * 1024);
});

async function open(page: Page, request: APIRequestContext) {
  const id = `import-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const name = `素材恢复 ${id}`;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name, width: 320, height: 180, fps: 24 } })).ok()).toBeTruthy();
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: "时间线", exact: true })).toBeVisible({ timeout: 45000 });
  return { id, name };
}
const list = async (request: APIRequestContext, id: string) => (await (await request.get(`/api/v1/assets?projectId=${encodeURIComponent(id)}`)).json()).assets as {
  assetId: string; path: string; name: string; size: number; available: boolean; sha256?: string;
}[];

test("real upload reports bytes and filename then aborts the current and remaining queue", async ({ page, request }) => {
  test.setTimeout(90000);
  const project = await open(page, request);
  const session = await page.context().newCDPSession(page);
  await session.send("Network.enable");
  await session.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: 10 * 1024 * 1024, uploadThroughput: 256 * 1024 });
  const next = join(evidence, "must-not-upload.png"); copyFileSync(sticker, next);
  await page.locator('input[type="file"][multiple]').first().setInputFiles([large, next]);
  const progress = page.locator(".media-import-progress");
  await expect(progress).toContainText(`当前文件：${basename(large)}`);
  const bytes = progress.getByRole("progressbar", { name: "当前文件上传进度" });
  await expect.poll(async () => Number(await bytes.getAttribute("value")), { timeout: 15000 }).toBeGreaterThan(0);
  const loaded = Number(await bytes.getAttribute("value"));
  expect(loaded).toBeLessThan(statSync(large).size);
  await page.screenshot({ path: join(evidence, "real-upload-byte-progress.png") });
  await progress.getByRole("button", { name: "取消当前及后续上传", exact: true }).click();
  await expect(progress).toContainText("上传已取消，后续文件未上传");
  await session.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  const directory = await (await request.get(`/api/v1/projects/${project.id}/directory`)).json();
  expect(directory.storageKind).toBe("directory");
  const staging = join(directory.mediaDirectory, ".uploads");
  await expect.poll(() => existsSync(staging) ? readdirSync(staging).length : 0).toBe(0);
  expect((await list(request, project.id)).some(asset => [basename(large), basename(next)].includes(asset.name))).toBe(false);
  writeFileSync(join(evidence, "cancel-verification.json"), JSON.stringify({ loaded, total: statSync(large).size,
    stagingFilesAfterCancel: existsSync(staging) ? readdirSync(staging) : [], remainingFileUploaded: false,
    builtResources: await page.evaluate(() => performance.getEntriesByType("resource").map(item => item.name).filter(name => /Workspace-.*\.js|index-.*\.js/.test(name))),
  }, null, 2));
});

test("a valid video above 16 MiB uploads and relinks through the Web controls", async ({ page, request }) => {
  test.setTimeout(120000);
  const project = await open(page, request);
  await page.locator('input[type="file"][multiple]').first().setInputFiles(large);
  await expect(page.getByText("导入结束：成功 1，失败 0", { exact: true })).toBeVisible({ timeout: 45000 });
  const asset = (await list(request, project.id)).find(item => item.name === basename(large))!;
  expect(asset.size).toBe(statSync(large).size);
  expect(asset.sha256).toMatch(/^[a-f0-9]{64}$/);
  unlinkSync(asset.path);
  await page.reload();
  await page.getByRole("button", { name: `打开工程 ${project.name}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: "时间线", exact: true })).toBeVisible({ timeout: 45000 });
  await page.getByRole("button", { name: /^全部素材 / }).click();
  const picker = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: `重新链接 ${basename(large)}`, exact: true }).click();
  await (await picker).setFiles(large);
  await expect(page.getByText("重新链接结束：成功 1，失败 0", { exact: true })).toBeVisible({ timeout: 45000 });
  expect(statSync(asset.path).size).toBe(statSync(large).size);
  await page.screenshot({ path: join(evidence, "large-video-import-and-relink.png") });
  writeFileSync(join(evidence, "large-web-verification.json"), JSON.stringify({ size: asset.size, assetId: asset.assetId,
    samePathRestored: asset.path, sha256: asset.sha256, completeUpload: true, completeRelink: true }, null, 2));
});

test("folder scan previews ambiguity and legacy confirmation before restoring selected media", async ({ page, request }) => {
  test.setTimeout(120000);
  const project = await open(page, request);
  const upload = async (name: string) => {
    const response = await request.post(`/api/v1/assets?name=${name}&projectId=${encodeURIComponent(project.id)}`, { data: readFileSync(sticker), headers: { "Content-Type": "application/octet-stream" } });
    expect(response.ok(), await response.text()).toBeTruthy(); return response.json();
  };
  const known = await upload("known.png"), legacy = await upload("legacy.png");
  const directory = await (await request.get(`/api/v1/projects/${project.id}/directory`)).json();
  expect(directory.storageKind).toBe("directory");
  const database = join(directory.directory, "project.sqlite");
  expect(existsSync(database)).toBe(true);
  execFileSync("python", ["-c", "import sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=rw',uri=True); c.execute('UPDATE assets SET sha256=NULL WHERE asset_id=?',(sys.argv[2],)); c.commit(); c.close()", database, legacy.assetId]);
  unlinkSync(known.path); unlinkSync(legacy.path);
  const folder = join(evidence, "relink-folder");
  mkdirSync(join(folder, "a"), { recursive: true }); mkdirSync(join(folder, "b"), { recursive: true });
  copyFileSync(sticker, join(folder, "a", "known.png")); copyFileSync(sticker, join(folder, "b", "known.png"));
  copyFileSync(sticker, join(folder, "legacy.png"));
  await page.reload();
  await page.getByRole("button", { name: `打开工程 ${project.name}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: "时间线", exact: true })).toBeVisible();
  const before = await (await request.get(`/api/v1/projects/${project.id}`)).json();
  const panel = page.locator(".media-folder-relink");
  await panel.locator("summary").click();
  await panel.getByLabel("选择批量重连文件夹", { exact: true }).setInputFiles(folder);
  await expect(panel.getByText("候选有歧义，请明确选择文件", { exact: true })).toBeVisible();
  const knownRow = panel.getByText("known.png", { exact: true }).locator("..");
  const legacyRow = panel.getByText("legacy.png", { exact: true }).locator("..");
  await expect(legacyRow.getByRole("checkbox")).not.toBeChecked();
  await expect(knownRow.getByRole("checkbox")).not.toBeChecked();
  await knownRow.getByRole("combobox").selectOption({ label: "relink-folder/a/known.png" });
  await knownRow.getByRole("checkbox").check();
  await legacyRow.getByRole("combobox").selectOption({ label: "relink-folder/legacy.png" });
  await legacyRow.getByText("预览并核对候选文件", { exact: true }).click();
  await expect(legacyRow.getByAltText("候选文件预览")).toBeVisible();
  await expect.poll(() => legacyRow.getByAltText("候选文件预览").evaluate((image: HTMLImageElement) => image.naturalWidth)).toBeGreaterThan(0);
  await legacyRow.getByRole("checkbox", { name: "已人工核对该候选，确认恢复", exact: true }).check();
  await page.screenshot({ path: join(evidence, "folder-preview-and-manual-confirmation.png") });
  await panel.getByRole("button", { name: "恢复已确认的 2 个素材", exact: true }).click();
  await expect(panel.getByText("恢复结束：成功 2，失败 0", { exact: true })).toBeVisible({ timeout: 45000 });
  expect(readFileSync(known.path)).toEqual(readFileSync(sticker));
  expect(readFileSync(legacy.path)).toEqual(readFileSync(sticker));
  const after = await (await request.get(`/api/v1/projects/${project.id}`)).json();
  expect(after.revision).toBe(before.revision);
  await page.screenshot({ path: join(evidence, "folder-restored.png") });
  writeFileSync(join(evidence, "folder-web-verification.json"), JSON.stringify({ restored: [known.assetId, legacy.assetId],
    ambiguousCandidateRequiredChoice: true, legacyDefaultUnchecked: true, legacyPreviewShown: true, revisionUnchanged: true,
    sameOriginalPaths: [known.path, legacy.path] }, null, 2));
});
