import { expect, test } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdirSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

test("stdio MCP imports stay visible when a human edits, previews and exports", async ({ page, request }) => {
  test.setTimeout(90_000);
  const runtime = await (await request.get("/api/v1/runtime")).json();
  // Only the dedicated, disposable browser-test server's database is shared.
  expect(runtime.dataDirectory).toContain("cutvoke-e2e-");
  const id = `mcp-handoff-${Date.now()}`;
  const name = `Agent 接手 ${id}`;
  const image = fileURLToPath(new URL("../../../src/cutvoke/assets/stickers/decor_sparkles.png", import.meta.url));
  // A shared library can already contain an upload with the same filename.
  // Locate the actual MCP asset by identity, never by name alone.
  const duplicate = await request.post("/api/v1/assets?name=decor_sparkles.png", {
    headers: { "Content-Type": "application/octet-stream" }, data: readFileSync(image),
  });
  expect(duplicate.ok()).toBeTruthy();
  const python = process.env.CUTVOKE_TEST_PYTHON || "python";
  const child = spawn(python, ["-m", "cutvoke", "mcp", "--data", runtime.dataDirectory], {
    stdio: ["pipe", "pipe", "pipe"], env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  let diagnostics = "";
  child.stderr.on("data", (chunk) => { diagnostics = (diagnostics + chunk).slice(-2000); });
  const closed = new Promise<number | null>((resolve, reject) => {
    child.once("exit", resolve);
    child.once("error", reject);
  });
  const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  let serial = 0;
  async function rpc(method: string, params: object) {
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: ++serial, method, params }) + "\n");
    const line = await lines.next();
    expect(line.done, diagnostics).toBe(false);
    const message = JSON.parse(line.value!);
    expect(message.error, JSON.stringify(message)).toBeUndefined();
    return message.result;
  }
  async function call(name: string, args: object) {
    const wire = await rpc("tools/call", { name, arguments: args });
    const data = JSON.parse(wire.content.find((c: { type: string }) => c.type === "text").text);
    expect(wire.isError, JSON.stringify(data)).toBeFalsy();
    expect(data.ok, JSON.stringify(data)).not.toBe(false);
    return data;
  }
  let asset: { assetId: string; path: string; name: string };
  let moduleId = "";
  try {
    const initialized = await rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {},
      clientInfo: { name: "browser-handoff", version: "1.0.0" } });
    expect(initialized.instructions).toContain("不包含您的视频、图片、音频、文字或字幕正文");
    asset = (await call("media_import", { paths: [image] })).assets[0];
    const repeat = (await call("media_import", { paths: [image] })).assets[0];
    expect(repeat.assetId).toBe(asset.assetId);
    await call("create_project", { projectId: id, name, width: 320, height: 180, fps: 30 });
    const lease = (await call("edit_lock", { projectId: id, action: "acquire", owner: "handoff-test" })).lease.leaseId;
    await call("command_apply", { projectId: id, expectedRevision: "0", editLeaseId: lease,
      commandId: `${id}-edit`, type: "edit.batch", payload: { commands: [
        { type: "clip.insert", payload: { trackId: "v1", createTrackKind: "video", clipId: "imported-shot",
          assetId: asset.assetId, timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 2, den: 1 } } },
        { type: "caption.add", payload: { captionId: "agent-caption", text: "Agent 原字幕",
          start: { num: 0, den: 1 }, end: { num: 2, den: 1 } } },
      ] } });
    const saved = await call("effect_module_save", { recipe: {
      effects: [{ effectId: "cutvoke.fx.vibrance", params: { intensity: 0.4 } }],
      motion: [{ param: "opacity", at: 0, value: 0.3 }, { param: "opacity", at: 0.25, value: 1 }],
    } });
    moduleId = saved.moduleId;
    const applyModule = { projectId: id, expectedRevision: "1", type: "effectModule.apply",
      payload: { clipId: "imported-shot", moduleId } };
    await call("command_preview", applyModule);
    await call("command_apply", { ...applyModule, editLeaseId: lease, commandId: `${id}-module` });
    await call("edit_lock", { projectId: id, action: "release", leaseId: lease });
  } finally {
    child.stdin.end();
    expect(await closed, diagnostics).toBe(0);
  }
  const entries = (await (await request.get("/api/v1/assets")).json()).assets;
  expect(entries.filter((a: { name: string }) => a.name === asset.name).length).toBeGreaterThanOrEqual(2);
  expect(entries.find((a: { assetId: string }) => a.assetId === asset.assetId).available).toBe(true);
  const media = await request.get(`/api/v1/assets/${asset.assetId}/media`);
  expect(media.status()).toBe(200);
  expect(await media.body()).toEqual(readFileSync(image));

  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${name}` }).click();
  await expect(page.getByRole("heading", { name: "时间线" })).toBeVisible({ timeout: 45_000 });
  await page.getByRole("tab", { name: /我的素材/ }).click();
  await page.getByLabel("搜索素材").fill(asset.name);
  const card = page.locator(".media-item").filter({ has: page.locator(`img[src*="${asset.assetId}"]`) });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText(asset.name);
  await expect(card).not.toContainText("文件丢失");
  await expect(card.locator("img")).toBeVisible();
  await expect.poll(() => card.locator("img").evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  await page.getByRole("tablist", { name: "创作域" }).getByRole("tab", { name: "字幕", exact: true }).click();
  await page.getByRole("button", { name: "选择字幕 agent-caption", exact: true }).click();
  const field = page.getByRole("textbox", { name: "编辑字幕文字" });
  await field.fill("人工接手修改");
  await page.getByRole("button", { name: "保存字幕文字与时间" }).click();
  await expect.poll(async () => (await (await request.get(`/api/v1/projects/${id}`)).json())
    .sequence.captions[0].text).toBe("人工接手修改");
  const edited = (await (await request.get(`/api/v1/projects/${id}`)).json()).sequence;
  expect(edited.tracks[0].clips[0].effects.find((effect: { effectId: string }) =>
    effect.effectId === "cutvoke.fx.vibrance").params.intensity).toBe(0.4);
  const preview = page.locator("video.player__video");
  await expect.poll(() => preview.evaluate((v: HTMLVideoElement) => v.readyState),
    { timeout: 30_000 }).toBeGreaterThan(1);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => preview.evaluate((v: HTMLVideoElement) => v.currentTime),
    { timeout: 30_000 }).toBeGreaterThan(.1);
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  const evidence = resolve("../../.artifacts/acceptance/repo-repair/handoff");
  mkdirSync(evidence, { recursive: true });
  const shared = await (await request.post("/api/v1/agent/effect_module_export", { data: {
    moduleId, outPath: resolve(evidence, `${id}-effect.zip`),
  } })).json();
  expect(shared.ok).toBe(true);
  expect(shared.containsUserContent).toBe(false);
  expect(shared.uploaded).toBe(false);
  expect(shared.privacyNotice["zh-CN"]).toContain("不包含您的视频、图片、音频、文字或字幕正文");
  // Inspect decompressed members, not raw ZIP bytes which could hide private text.
  execFileSync(python, ["-c", [
    "import sys,zipfile",
    "z=zipfile.ZipFile(sys.argv[1])",
    "assert len(z.namelist())==4",
    "data=b''.join(z.read(n) for n in z.namelist())",
    "assert all(s.encode('utf-8') not in data for s in sys.argv[2:])",
  ].join(";"), shared.path, id, name, "Agent 原字幕", "人工接手修改", asset.path, asset.assetId],
  { timeout: 30_000 });
  await page.screenshot({ path: resolve(evidence, "mcp-human-edit.png") });
  await page.getByRole("button", { name: "导出", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导出成片" });
  const output = resolve(evidence, `${id}.mp4`);
  await dialog.getByLabel("输出文件路径").fill(output);
  await dialog.getByLabel("画质").selectOption("low");
  await dialog.getByRole("button", { name: "开始导出" }).click();
  await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 30_000 });
  expect(statSync(output).size).toBeGreaterThan(0);
  execFileSync("ffmpeg", ["-v", "error", "-i", output, "-f", "null", "-"], { timeout: 30_000 });
});
