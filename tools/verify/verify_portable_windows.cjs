// Runs the shipped ZIP's CutVoke.exe on Windows, with no development runtimes
// on the app's PATH. Playwright drives its actual, visible Electron window.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn, execFileSync } = require("node:child_process");
const { createInterface } = require("node:readline");
const { createRequire } = require("node:module");
const net = require("node:net");
const crypto = require("node:crypto");
const root = path.resolve(__dirname, "../..");
const { _electron, expect } = createRequire(path.join(root, "apps/editor/package.json"))("@playwright/test");
const out = path.join(root, ".artifacts/acceptance/windows-portable");
fs.mkdirSync(out, { recursive: true });
const report = { passed: false, platform: process.platform, scope: "Real packaged Windows executable; visible GUI driven automatically on a hosted runner. Human desktop usability and SmartScreen are not manually accepted.", checks: {} };
const save = () => fs.writeFileSync(path.join(out, "verification.json"), JSON.stringify(report, null, 2));
function run(exe, args, env) { return execFileSync(exe, args, { env, encoding: "utf8", windowsHide: true, timeout: 120_000, maxBuffer: 10 * 1024 * 1024 }); }
function cleanEnvironment() {
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (/^(CUTVOKE_|PYTHON|VIRTUAL_ENV$|ELECTRON_RUN_AS_NODE$|__COMPAT_LAYER$)/i.test(key)) delete env[key];
  env.PATH = `${process.env.SystemRoot}\\System32;${process.env.SystemRoot}`;
  return env;
}
function exited(child, timeout = 30_000) {
  if (child.exitCode !== null) return Promise.resolve(child.exitCode);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error("Owned process did not exit")); }, timeout);
    child.once("error", error => { clearTimeout(timer); reject(error); });
    child.once("exit", code => { clearTimeout(timer); resolve(code); });
  });
}
async function mcp(config, env) {
  const child = spawn(config.command, config.args, { env: { ...env, ...config.env }, windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
  const stderr = fs.createWriteStream(path.join(out, "mcp.stderr.log"), { flags: "a" });
  child.stderr.pipe(stderr);
  const lines = createInterface({ input: child.stdout })[Symbol.asyncIterator]();
  let serial = 0;
  async function rpc(method, params = {}) {
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: ++serial, method, params }) + "\n");
    let timer;
    const line = await Promise.race([lines.next(), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(`MCP timeout: ${method}`)), 60_000); })]).finally(() => clearTimeout(timer));
    assert.ok(!line.done, "MCP closed unexpectedly");
    const response = JSON.parse(line.value);
    fs.appendFileSync(path.join(out, "mcp-transcript.jsonl"), JSON.stringify({ method, params, response }) + "\n");
    assert.equal(response.id, serial);
    assert.ok(!response.error, JSON.stringify(response));
    return response.result;
  }
  async function call(name, args) {
    const response = await rpc("tools/call", { name, arguments: args });
    assert.ok(!response.isError, JSON.stringify(response));
    return JSON.parse(response.content.find(x => x.type === "text").text);
  }
  return { rpc, call, close: async () => { child.stdin.end(); assert.equal(await exited(child), 0); } };
}
async function main() {
  assert.equal(process.platform, "win32", "This acceptance must run on Windows");
  const download = JSON.parse(fs.readFileSync(path.join(root, "dist/portable/download-info.json")));
  report.download = download;
  const zip = path.join(root, "dist/portable", download.filename);
  assert.equal(crypto.createHash("sha256").update(fs.readFileSync(zip)).digest("hex"), download.sha256);
  const extracted = path.join(out, "中文 空格路径");
  fs.mkdirSync(extracted, { recursive: true });
  run("pwsh.exe", ["-NoProfile", "-Command", "Expand-Archive -LiteralPath $env:CUTVOKE_VERIFY_ZIP -DestinationPath $env:CUTVOKE_VERIFY_DEST -Force"],
    { ...process.env, CUTVOKE_VERIFY_ZIP: zip, CUTVOKE_VERIFY_DEST: extracted });
  const folder = path.join(extracted, path.basename(download.filename, ".zip"));
  const executable = path.join(folder, "CutVoke.exe");
  const data = path.join(folder, "CutVoke Data");
  const profile = path.join(data, "profile");
  const engine = path.join(folder, "resources/engine/cutvoke-engine.exe");
  const bin = path.join(folder, "resources/engine/_internal/bin");
  const ffmpeg = path.join(bin, "ffmpeg.exe"), ffprobe = path.join(bin, "ffprobe.exe");
  const env = cleanEnvironment();
  for (const [name, hash] of Object.entries(JSON.parse(fs.readFileSync(path.join(folder, "file-manifest.json"))))) {
    assert.equal(crypto.createHash("sha256").update(fs.readFileSync(path.join(folder, name))).digest("hex"), hash, name);
  }
  report.checks.extractedZipIntegrity = true;
  const doctor = JSON.parse(run(engine, ["doctor", "--json"], env));
  assert.equal(doctor.ok, true, JSON.stringify(doctor));
  assert.equal(doctor.optionalDependencies.asr, false);
  assert.equal(doctor.optionalDependencies.person, false);
  assert.ok(doctor.ffmpeg.startsWith(bin));
  report.doctor = doctor;
  fs.writeFileSync(path.join(out, "ffmpeg-version.txt"), run(ffmpeg, ["-version"], env));
  const imports = run("pwsh.exe", ["-NoProfile", "-Command", "Get-AuthenticodeSignature -LiteralPath $env:CUTVOKE_VERIFY_EXE | Select-Object Status,StatusMessage | ConvertTo-Json"], { ...process.env, CUTVOKE_VERIFY_EXE: executable });
  report.signature = JSON.parse(imports);
  // A signature on the unmodified upstream Electron runtime, if present,
  // is not a publisher signature authenticating CutVoke's packaged app.asar.
  report.cutvokePublisherSignature = false;
  report.checks.noSystemPythonNodeOrFFmpegRequired = true;
  const media = path.join(out, "中文 素材.mp4");
  run(ffmpeg, ["-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-y", media], env);
  const occupied = net.createServer(socket => socket.end("unrelated occupied port"));
  await new Promise((resolve, reject) => { occupied.once("error", reject); occupied.listen(8787, "127.0.0.1", resolve); });
  let electronApp, origin, runtime, page;
  async function launch() {
    electronApp = await _electron.launch({ executablePath: executable, cwd: out,
      env: { ...env, CUTVOKE_DESKTOP_QA: "1", CUTVOKE_DESKTOP_QA_SHOW: "1" }, timeout: 120_000 });
    page = await electronApp.firstWindow();
    page.on("console", message => fs.appendFileSync(path.join(out, "renderer.log"), `${message.type()}: ${message.text()}\n`));
    page.on("pageerror", error => fs.appendFileSync(path.join(out, "renderer-errors.log"), error.stack + "\n"));
    await page.getByTestId("language-select").waitFor({ timeout: 120_000 });
    origin = new URL(page.url()).origin;
    runtime = await (await fetch(origin + "/api/v1/runtime")).json();
    assert.equal(path.normalize(runtime.dataPath), path.join(data, "data/projects.sqlite"));
    assert.equal(path.normalize(runtime.pythonExecutable), engine);
    assert.notEqual(new URL(origin).port, "8787");
    const native = await electronApp.evaluate(({ BrowserWindow }) => {
      const win = BrowserWindow.getAllWindows()[0];
      return { visible: win.isVisible(), offscreen: win.webContents.isOffscreen(), handle: win.getNativeWindowHandle().toString("hex"), bounds: win.getBounds() };
    });
    assert.ok(native.visible && !native.offscreen && native.handle);
    report.nativeWindow = native;
  }
  async function api(route, body) {
    const response = await fetch(origin + "/api/v1/" + route, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const value = await response.json();
    assert.ok(response.ok, JSON.stringify(value)); return value;
  }
  async function close() {
    const stoppedPid = runtime.processId, previousOrigin = origin;
    // This closes the real window and exercises the product's owned-backend shutdown.
    const closed = electronApp.waitForEvent("close", { timeout: 30_000 });
    await electronApp.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
    await closed;
    electronApp = null;
    await assert.rejects(fetch(previousOrigin + "/api/v1/runtime", { signal: AbortSignal.timeout(2000) }));
    assert.throws(() => process.kill(stoppedPid, 0));
  }
  try {
    await launch();
    report.checks.visibleGUIAndHealthyFrozenBackend = true;
    const presets = await api("presets");
    assert.equal(presets.qualifiedCount, 192);
    assert.equal(presets.resourcePack.offlineAvailable, true, JSON.stringify(presets.resourcePack));
    assert.deepEqual(presets.resourcePack.missingFiles, []);
    assert.deepEqual(presets.resourcePack.invalidFiles, []);
    const stickers = await api("stickers");
    assert.equal(stickers.stickers.filter(item => item.qualified).length, 612);
    report.builtinResources = { qualifiedPresets: 192, qualifiedStickers: 612,
      resourceCount: presets.resourcePack.resourceCount, version: presets.resourcePack.version, offlineAvailable: true };
    report.checks.builtinResourcesQualifiedAndOffline = true;
    // Block outbound browser traffic and record requests during the entire edit.
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (["127.0.0.1", "localhost"].includes(url.hostname) || url.protocol === "data:") return route.continue();
      (report.blockedExternalRequests ||= []).push(url.origin); return route.abort();
    });
    // Fresh profiles follow the runner's system language. Choose Chinese via
    // the visible setup control before using localized accessibility names.
    await page.locator("dialog.cache-settings").getByRole("combobox").selectOption("zh-CN");
    const setup = page.getByRole("dialog", { name: "欢迎使用 CutVoke" });
    await expect(setup).toBeVisible();
    await setup.getByRole("button", { name: "保存并开始使用", exact: true }).click();
    await expect(setup).not.toBeVisible();
    assert.equal((await page.evaluate(() => window.cutvokeDesktop.settings())).setupComplete, true);
    report.checks.firstRunSettingsSavedThroughGUI = true;
    const locales = ["zh-CN", "zh-TW", "en", "ja", "ko", "es", "fr", "de", "pt-BR", "ru", "ar"];
    for (const locale of locales) {
      await page.getByTestId("language-select").selectOption(locale);
      await expect.poll(() => page.locator("html").getAttribute("lang")).toBe(locale);
      await expect.poll(() => page.evaluate(() => window.cutvokeDesktop.getLanguageState().locale)).toBe(locale);
    }
    report.checks.elevenInterfaceLanguages = locales;
    await page.getByTestId("language-select").selectOption("en");
    await page.screenshot({ path: path.join(out, "01-english-first-launch.png") });
    await page.getByTestId("language-select").selectOption("zh-CN");
    const projectId = "portable-windows-acceptance";
    await page.getByLabel("新工程名称", { exact: true }).fill(projectId);
    await page.getByRole("button", { name: "创建工程", exact: true }).click();
    await page.getByRole("heading", { name: "时间线", exact: true }).waitFor();
    const showMedia = page.getByRole("button", { name: "显示素材面板", exact: true });
    if (await showMedia.isVisible()) await showMedia.click();
    await expect(page.locator("#creative-domain-content").getByRole("button", { name: "导入素材", exact: true })).toBeEnabled();
    // Electron owns the native OS picker; CDP's browser filechooser interception
    // is not reliable there. Drive the same file input and real upload handler.
    await page.locator('input[type="file"][accept="video/*,audio/*,image/*"][multiple]').setInputFiles(media);
    let asset;
    await expect.poll(async () => {
      asset = (await api("assets")).assets.find(x => x.name === path.basename(media));
      return Boolean(asset?.available);
    }, { timeout: 30_000 }).toBe(true);
    assert.ok(asset.path.startsWith(path.join(data, "data")));
    const uploaded = await fetch(origin + `/api/v1/assets/${asset.assetId}/media`);
    assert.ok(uploaded.ok);
    assert.deepEqual(Buffer.from(await uploaded.arrayBuffer()), fs.readFileSync(media));
    const desktopInfo = await page.evaluate(() => window.cutvokeDesktop.info());
    assert.equal(desktopInfo.storage.portable, true);
    assert.equal(path.normalize(desktopInfo.storage.fontCache), path.join(data, "data/font-cache"));
    const config = JSON.parse(fs.readFileSync(path.join(profile, "mcp-config.json"))).mcpServers.cutvoke;
    const client = await mcp(config, env);
    try {
      await client.rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "portable-Windows-acceptance", version: "1" } });
      const identity = await client.call("runtime", {});
      assert.equal(identity.dataPath, runtime.dataPath);
      const imported = await client.call("media_import", { paths: [media] });
      assert.ok(imported.assets.length);
      const project = await api(`projects/${projectId}`);
      const lease = (await client.call("edit_lock", { projectId, action: "acquire", owner: "portable-qa" })).lease.leaseId;
      await client.call("command_apply", { projectId, type: "edit.batch", expectedRevision: project.revision,
        editLeaseId: lease, commandId: "portable-first-edit", payload: { commands: [
          { type: "clip.insert", payload: { trackId: "video", createTrackKind: "video", clipId: "shot", assetId: asset.assetId,
            timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 3, den: 1 } } },
          { type: "caption.add", payload: { captionId: "caption", text: "便携版 Windows 实际验收 / Offline edit", start: { num: 0, den: 1 }, end: { num: 3, den: 1 } } },
          // Exercise the frozen Pillow/font renderer, not just FFmpeg video.
          { type: "clip.insert", payload: { trackId: "title-track", createTrackKind: "text", clipId: "title-clip",
            text: { content: "OFFLINE TITLE / 离线标题", fontSize: 40, color: "#ffffff", x: .5, y: .3 },
            timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 3, den: 1 } } },
        ] } });
      await client.call("edit_lock", { projectId, action: "release", leaseId: lease });
    } finally { await client.close(); }
    report.checks.guiMediaImportAndSharedStdioMCP = true;
    await page.reload();
    await page.getByRole("button", { name: `打开工程 ${projectId}`, exact: true }).click();
    await page.getByRole("heading", { name: "时间线", exact: true }).waitFor();
    await page.getByRole("button", { name: "播放", exact: true }).click();
    // The decoder pool retains hidden, paused videos. Only the active player
    // proves visible playback; DOM order is not the playback identity.
    const previewVideo = page.locator("video.player__video");
    await expect(previewVideo).toBeVisible({ timeout: 90_000 });
    await expect.poll(() => previewVideo.evaluate(v => v.currentTime), { timeout: 90_000 }).toBeGreaterThan(.25)
      .catch(async error => {
        report.previewVideos = await page.locator("video").evaluateAll(videos => videos.map(v => ({
          className: v.className, hidden: v.hidden, currentTime: v.currentTime,
          paused: v.paused, readyState: v.readyState, error: v.error?.message, src: v.currentSrc,
        })));
        save(); throw error;
      });
    await page.getByRole("button", { name: "暂停", exact: true }).click();
    report.checks.actualPreviewPlayback = true;
    await page.screenshot({ path: path.join(out, "02-chinese-import-preview.png") });
    await page.getByRole("button", { name: "导出", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "导出成片" });
    const exported = path.join(out, "中文 导出.mp4");
    await dialog.getByLabel("输出文件路径").fill(exported);
    await dialog.getByLabel("画质", { exact: true }).selectOption("low");
    await dialog.getByRole("button", { name: "开始导出", exact: true }).click();
    await expect(dialog.getByText("导出完成", { exact: false })).toBeVisible({ timeout: 120_000 });
    await page.screenshot({ path: path.join(out, "03-export-completed.png") });
    const probe = JSON.parse(run(ffprobe, ["-v", "error", "-show_streams", "-show_format", "-of", "json", exported], env));
    assert.ok(probe.streams.some(x => x.codec_name === "h264"));
    assert.ok(probe.streams.some(x => x.codec_name === "aac"));
    assert.ok(Number(probe.format.duration) >= 2.9);
    run(ffmpeg, ["-v", "error", "-nostdin", "-i", exported, "-f", "null", "-"], env);
    run(ffmpeg, ["-v", "error", "-nostdin", "-ss", "1", "-i", exported, "-frames:v", "1", "-update", "1", "-y", path.join(out, "04-decoded-export-frame.png")], env);
    report.exportProbe = probe;
    report.checks.guiExportAndFullVideoAudioDecode = true;
    const before = await api(`projects/${projectId}`);
    const pid = runtime.processId;
    const duplicate = spawn(executable, [], { env: { ...env, CUTVOKE_DESKTOP_QA: "1", CUTVOKE_DESKTOP_QA_SHOW: "1" }, stdio: "ignore" });
    assert.equal(await exited(duplicate), 0);
    assert.equal((await api("runtime")).processId, pid);
    assert.deepEqual(await api(`projects/${projectId}`), before);
    report.checks.duplicateLaunchAndOccupiedDefaultPort = true;
    await close();
    await launch();
    assert.deepEqual(await api(`projects/${projectId}`), before);
    assert.equal((await page.evaluate(() => window.cutvokeDesktop.settings())).setupComplete, true);
    await expect(page.getByRole("dialog", { name: "欢迎使用 CutVoke" })).not.toBeVisible();
    assert.equal(await page.locator("html").getAttribute("lang"), "zh-CN");
    await page.getByRole("button", { name: `打开工程 ${projectId}`, exact: true }).click();
    const cache = await api("preview-cache");
    assert.ok(cache.directory.startsWith(path.join(data, "data")));
    report.previewCache = cache;
    report.checks.restartPreservesProjectAndLanguage = true;
    await close();
    report.checks.windowCloseStopsOwnedBackend = true;
    report.storage = { database: path.join(data, "data/projects.sqlite"), profile, cache: cache.directory };
    run("pwsh.exe", ["-NoProfile", "-File", path.join(root, "tools/verify/verify_portable_windows.ps1"), "-App", executable, "-Output", out, "-Normal"]);
    const normal = JSON.parse(fs.readFileSync(path.join(out, "normal-startup-result.json")));
    assert.equal(normal.passed, true);
    report.checks.normalLaunchWithoutQaOrDebugFlags = true;
    // Actual Windows ACL denial + native error dialog, preserving the same DB.
    // Windows' UI Automation framework is a .NET Framework component. Isolate
    // Windows PowerShell's modules from the runner's PowerShell 7 module path.
    run("powershell.exe", ["-NoProfile", "-File", path.join(root, "tools/verify/verify_portable_windows.ps1"), "-App", executable, "-Output", out],
      { ...process.env, PSModulePath: path.join(process.env.SystemRoot, "System32/WindowsPowerShell/v1.0/Modules") });
    const readonly = JSON.parse(fs.readFileSync(path.join(out, "readonly-result.json")));
    assert.equal(readonly.passed, true);
    report.checks.readonlyFolderShowsNativeErrorAndPreservesDatabase = true;
    report.passed = true;
  } finally {
    if (electronApp) await electronApp.close().catch(() => {});
    occupied.close();
    const log = path.join(profile, "logs/engine.log");
    if (fs.existsSync(log)) fs.copyFileSync(log, path.join(out, "engine.log"));
    save();
  }
  fs.copyFileSync(path.join(out, "verification.json"), path.join(root, "dist/portable/windows-verification.json"));
  console.log(JSON.stringify(report.checks, null, 2));
}
main().catch(error => {
  report.error = error.stack; save(); console.error(error);
  // Keep remote failures diagnosable even when artifact downloads are unavailable.
  for (const name of fs.readdirSync(out, { recursive: true })) {
    if (name.endsWith("engine.log") || name.endsWith("renderer-errors.log") || name.endsWith("-result.json")) {
      const file = path.join(out, name);
      if (fs.statSync(file).isFile()) console.error(`${name}\n${fs.readFileSync(file, "utf8").slice(-12000)}`);
    }
  }
  process.exitCode = 1;
});
