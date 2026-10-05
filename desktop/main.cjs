const { app, BrowserWindow, dialog, ipcMain, shell, session } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const crypto = require("node:crypto");

app.setName("CutVoke");
app.setPath("userData", path.join(app.getPath("appData"), "CutVoke"));

// On Windows, the DirectComposition path dropped ~7% of preview frames even
// in an isolated video window. The alternative GPU surface retained hardware
// decode/compositing and reduced this to 4/1196 in the same project.
// Allow a diagnostic opt-out without changing the system's graphics settings.
if (process.platform === "win32" && process.env.CUTVOKE_DIRECT_COMPOSITION !== "1") {
  app.commandLine.appendSwitch("disable-direct-composition");
}

if (process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_HOME) {
  fs.mkdirSync(process.env.CUTVOKE_DESKTOP_QA_HOME, { recursive: true });
  app.setPath("userData", path.resolve(process.env.CUTVOKE_DESKTOP_QA_HOME));
}

let child, window, origin, closing = false, ready = false;
const shutdownKey = crypto.randomBytes(32).toString("hex");
const root = path.resolve(__dirname, "..");
const dataDir = path.resolve(process.env.CUTVOKE_DATA || path.join(app.getPath("home"), ".cutvoke", "data"));
const dataPath = /\.sqlite$/i.test(dataDir) ? dataDir : path.join(dataDir, "projects.sqlite");
const engine = app.isPackaged ? path.join(process.resourcesPath, "engine", "cutvoke-engine.exe")
  : path.join(root, "build", "desktop-engine", "cutvoke-engine", "cutvoke-engine.exe");
const engineExists = fs.existsSync(engine);
const executable = engineExists ? engine : path.join(root, ".venv", "Scripts", "python.exe");
const prefix = engineExists ? [] : ["-m", "cutvoke"];

function mcpConfig() {
  return { mcpServers: { cutvoke: { command: executable, args: [...prefix, "mcp", "--data", dataPath],
    ...(engineExists ? {} : { env: { PYTHONPATH: path.join(root, "src") } }) } } };
}
function vacantPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer(); server.once("error", reject);
    server.listen(0, "127.0.0.1", () => { const port = server.address().port; server.close(() => resolve(port)); });
  });
}
async function request(route, options) {
  const response = await fetch(origin + "/api/v1/" + route, { ...options, signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(`本地服务请求失败（${response.status}）`);
  return response.json();
}
function verifySender(event) {
  if (!ready || !window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
      new URL(event.senderFrame.url).origin !== origin) throw new Error("无效的客户端请求");
}
async function start() {
  const port = await vacantPort(); origin = `http://127.0.0.1:${port}`;
  fs.mkdirSync(path.dirname(dataPath), { recursive: true });
  const logDir = path.join(app.getPath("userData"), "logs"); fs.mkdirSync(logDir, { recursive: true });
  const log = fs.createWriteStream(path.join(logDir, "engine.log"), { flags: "a" });
  const env = { ...process.env, CUTVOKE_DESKTOP_KEY: shutdownKey };
  if (!engineExists) env.PYTHONPATH = path.join(root, "src");
  // Packaged FFmpeg is selected by the engine entry point, independent of PATH.
  child = spawn(executable, [...prefix, "serve", "--host", "127.0.0.1", "--port", String(port), "--data", dataPath],
    { env, cwd: path.dirname(executable), windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let failure;
  child.once("error", error => { failure = error; });
  child.stdout.pipe(log, { end: false }); child.stderr.pipe(log, { end: false });
  child.once("exit", (code) => {
    log.end();
    if (ready && !closing) {
      dialog.showErrorBox("本地服务已退出", `编辑服务退出（${code}）。请重新打开 CutVoke。日志：${logDir}`);
      app.quit();
    }
  });
  let runtime;
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (failure) throw failure;
    if (child.exitCode !== null) throw new Error(`本地服务启动失败（${child.exitCode}），日志：${logDir}`);
    try { runtime = await request("runtime"); if (runtime.processId === child.pid) break; } catch { /* Startup still in progress. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!runtime || runtime.processId !== child.pid) throw new Error(`本地服务启动超时，日志：${logDir}`);
  fs.writeFileSync(path.join(app.getPath("userData"), "mcp-config.json"), JSON.stringify(mcpConfig(), null, 2));
  ready = true;
  await window.loadURL(origin);
  // QA uses the same built client without opening a debug port on normal runs.
  if (process.env.CUTVOKE_DESKTOP_QA === "1") {
    fs.writeFileSync(path.join(app.getPath("userData"), "qa-runtime.json"), JSON.stringify({ origin, runtime, executable }));
    if (process.env.CUTVOKE_DESKTOP_QA_SCRIPT) {
      try {
        await require(path.resolve(process.env.CUTVOKE_DESKTOP_QA_SCRIPT))({ window, app, origin, request });
      } catch (error) {
        fs.writeFileSync(path.join(app.getPath("userData"), "qa-error.txt"), error.stack || error.message);
      } finally { app.quit(); }
    }
  }
}
async function stop() {
  if (!child || child.exitCode !== null) return;
  try {
    await request("runtime/shutdown", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: shutdownKey }) });
    await Promise.race([new Promise(resolve => child.once("exit", resolve)), new Promise(resolve => setTimeout(resolve, 12000))]);
  } catch { /* The process may already have exited. */ }
  if (child.exitCode === null) child.kill(); // Only the process this client owns.
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    window = new BrowserWindow({ show: process.env.CUTVOKE_DESKTOP_QA !== "1", width: 1500, height: 950, minWidth: 900, minHeight: 640, backgroundColor: "#101318", title: "CutVoke",
      webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false } });
    window.removeMenu();
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => { if (new URL(url).origin !== origin) event.preventDefault(); });
    window.webContents.on("will-attach-webview", event => event.preventDefault());
    ipcMain.handle("cutvoke:choose-cache", async event => {
      verifySender(event);
      const result = await dialog.showOpenDialog(window, { title: "选择缓存保存位置", properties: ["openDirectory", "createDirectory"] });
      return result.canceled ? null : path.join(result.filePaths[0], ".cutvoke-preview-cache");
    });
    ipcMain.handle("cutvoke:reveal-cache", async event => {
      verifySender(event); const status = await request("preview-cache");
      const error = await shell.openPath(status.directory); if (error) throw new Error(error);
    });
    ipcMain.handle("cutvoke:info", event => { verifySender(event); return { version: app.getVersion(), mcp: mcpConfig() }; });
    await window.loadFile(path.join(__dirname, "loading.html"));
    try { await start(); } catch (error) { dialog.showErrorBox("CutVoke 无法启动", error.message); app.quit(); }
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", event => {
    if (closing) return;
    event.preventDefault(); closing = true;
    void stop().finally(() => app.quit());
  });
}
