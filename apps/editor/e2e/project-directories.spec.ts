import { expect, test } from "@playwright/test";
import { mkdirSync, existsSync, readFileSync, renameSync } from "node:fs";
import { resolve, join } from "node:path";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";

test("folder creation, scoped media and current-format reopening after a move", async ({ page, request }) => {
  test.setTimeout(120000);
  const evidence = resolve("../../.artifacts/project-directories");
  mkdirSync(evidence, { recursive: true });
  await page.goto("/");
  const name = `独立目录-${Date.now()}`;
  const root = join(tmpdir(), name);
  await page.getByRole("textbox", { name: "新工程名称", exact: true }).fill(name);
  await page.getByRole("textbox", { name: "新工程目录", exact: true }).fill(root);
  await page.getByRole("button", { name: "创建工程", exact: true }).click();
  await expect(page.getByRole("heading", { name: "时间线", exact: true })).toBeVisible();
  await expect(page.locator(".project-directory")).toContainText(root);
  for (const folder of ["media", "cache", "exports", "backups"]) expect(existsSync(join(root, folder))).toBeTruthy();
  expect(existsSync(join(root, "project.cutvoke"))).toBeTruthy();
  await page.locator('input[type="file"][multiple]').first().setInputFiles({ name: "folder-only.png", mimeType: "image/png",
    buffer: readFileSync(resolve("../../src/cutvoke/assets/stickers/decor_sparkles.png")) });
  await expect(page.getByText("导入结束：成功 1，失败 0", { exact: true })).toBeVisible();
  const assets = await (await request.get(`/api/v1/assets?projectId=${encodeURIComponent(name)}`)).json();
  const imported = assets.assets.find((asset: { name: string }) => asset.name === "folder-only.png");
  expect(imported.path.startsWith(join(root, "media"))).toBeTruthy();
  await page.screenshot({ path: join(evidence, "new-project-directory.png") });
  await page.getByRole("button", { name: "返回工程列表", exact: true }).click();
  const other = `${name}-other`;
  await request.post("/api/v1/projects", { data: { projectId: other, name: other } });
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.getByRole("button", { name: `打开工程 ${other}`, exact: true }).click();
  await expect(page.getByRole("heading", { name: "时间线", exact: true })).toBeVisible();
  await expect(page.getByText("folder-only.png", { exact: true })).toHaveCount(0);
  const otherAssets = await (await request.get(`/api/v1/assets?projectId=${encodeURIComponent(other)}`)).json();
  expect(otherAssets.assets.some((asset: { assetId: string }) => asset.assetId === imported.assetId)).toBeFalsy();
  await page.getByRole("button", { name: "返回工程列表", exact: true }).click();
  // Version conversion is covered separately by project-format-conversion.
  // Move a consistent OFFLINE copy: do not rename an open Windows SQLite file.
  const copied = `${root}-offline-copy`;
  execFileSync("python", ["-c", [
    "import shutil,sqlite3,sys", "from pathlib import Path",
    "source,target=map(Path,sys.argv[1:])", "shutil.copytree(source,target)",
    "db='project-v2.sqlite'",
    "for suffix in ('','-wal','-shm'):",
    " p=target/(db+suffix)", " if p.exists():p.unlink()",
    "original=sqlite3.connect((source/db).as_uri()+'?mode=ro',uri=True)",
    "backup=sqlite3.connect(target/db)", "original.backup(backup)",
    "backup.close();original.close()",
  ].join("\n"), root, copied]);
  // A same-ID copy must not replace a still-registered original.
  const duplicate = await request.post("/api/v1/project-directories/open", { data: { path: copied } });
  expect(duplicate.status()).toBe(400);
  expect((await duplicate.json()).error.message).toContain("另一个副本");
  const originalDirectory = await (await request.get(`/api/v1/projects/${encodeURIComponent(name)}/directory`)).json();
  expect(originalDirectory.directory).toBe(root);

  // Create a genuinely unregistered current project, close its database, then
  // move it. This exercises rebasing without bypassing duplicate-copy safety.
  const offline = `${root}-unregistered`;
  const independent = JSON.parse(execFileSync("python", ["-c", [
    "import json,sys", "from cutvoke.storage.project_directory import DirectoryProjectStore",
    "from cutvoke.editing.service import EditService", "from cutvoke.domain.protocol import Command,Actor",
    "root,source,pid=sys.argv[1:]",
    "with DirectoryProjectStore(root,create_id=pid) as store:",
    " service=EditService(store);p=service.create_project(pid)",
    " service.execute(Command('clip.insert',{'clipId':'still','trackId':'v','createTrackKind':'video','sourcePath':source,'timelineStart':{'num':'0','den':'1'},'timelineEnd':{'num':'1','den':'1'}},'insert',pid,p.revision,Actor('test','move')))",
    " clip=service.get_project(pid).sequence.tracks[0].clips[0]",
    " print(json.dumps({'projectId':pid,'assetId':clip.asset_ref.asset_id}))",
  ].join("\n"), offline, imported.path, `${name}-unregistered`], { encoding: "utf8" }));
  const moved = `${offline}-moved`;
  renameSync(offline, moved);
  await page.getByRole("textbox", { name: "工程目录路径", exact: true }).fill(join(moved, "project.cutvoke"));
  await page.getByRole("button", { name: "打开工程目录", exact: true }).click();
  await expect(page.locator(".project-directory")).toContainText(moved);
  const projectResponse = await request.get(`/api/v1/projects/${encodeURIComponent(independent.projectId)}`);
  expect(projectResponse.ok(), await projectResponse.text()).toBeTruthy();
  expect((await projectResponse.json()).schemaVersion).toBe("2");
  const movedAssets = await (await request.get(`/api/v1/assets?projectId=${encodeURIComponent(independent.projectId)}`)).json();
  const movedImported = movedAssets.assets.find((asset: { assetId: string }) => asset.assetId === independent.assetId);
  expect(movedImported.path.startsWith(join(moved, "media"))).toBeTruthy();
  expect(readFileSync(movedImported.path)).toEqual(readFileSync(imported.path));
  await page.screenshot({ path: join(evidence, "moved-project-reopened.png") });
  expect(JSON.parse(readFileSync(join(moved, "project.cutvoke"), "utf8")).rootPath).toBe(moved);
});
