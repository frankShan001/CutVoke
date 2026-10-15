const { app, BrowserWindow, dialog, ipcMain, shell, session, Menu } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const crypto = require("node:crypto");
const { fileURLToPath } = require("node:url");
const { createLanguageController, cacheDialogOptions, errorDialogOptions } = require("./i18n.cjs");
const { storagePlan, prepareStorage } = require("./storage.cjs");

app.setName("CutVoke");
const storage = storagePlan({ packaged: app.isPackaged, executable: process.execPath,
  appData: app.getPath("appData"), home: app.getPath("home") });
let storageFailure;
try {
  prepareStorage(storage);
  app.setPath("userData", storage.profile);
  app.setPath("sessionData", path.join(storage.profile, "browser"));
  app.setAppLogsPath(path.join(storage.profile, "logs"));
  // Crash reports and browser caches belong to this portable profile too.
  app.setPath("crashDumps", path.join(storage.profile, "crashes"));
} catch (error) { storageFailure = error; }

// On Windows, the DirectComposition path dropped ~7% of preview frames even
// in an isolated video window. The alternative GPU surface retained hardware
// decode/compositing and reduced this to 4/1196 in the same project.
// Allow a diagnostic opt-out without changing the system's graphics settings.
if (process.platform === "win32" && process.env.CUTVOKE_DIRECT_COMPOSITION !== "1") {
  app.commandLine.appendSwitch("disable-direct-composition");
}

if (process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_OFFSCREEN === "1") app.disableHardwareAcceleration();

let child, window, origin, qaHook, closing = false, ready = false;
const language = createLanguageController(path.join(app.getPath("userData"), "ui-language.json"),
  () => app.getPreferredSystemLanguages());
const shutdownKey = crypto.randomBytes(32).toString("hex");
const root = path.resolve(__dirname, "..");
const dataPath = storage.dataPath;
const engine = app.isPackaged ? path.join(process.resourcesPath, "engine", "cutvoke-engine.exe")
  : path.join(root, ".artifacts", "build", "desktop-engine", "cutvoke-engine", "cutvoke-engine.exe");
const engineExists = fs.existsSync(engine) && !(process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_PYTHON === "1");
const qaPython = process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_PYTHON === "1"
  ? process.env.CUTVOKE_DESKTOP_QA_PYTHON_EXE : undefined;
const executable = engineExists ? engine : qaPython || path.join(root, ".venv", "Scripts", "python.exe");
const prefix = engineExists ? [] : ["-m", "cutvoke"];

function mcpConfig() {
  return { mcpServers: { cutvoke: { command: executable, args: [...prefix, "mcp", "--data", dataPath],
    env: { CUTVOKE_HOME: storage.base || path.dirname(dataPath),
      ...(engineExists ? {} : { PYTHONPATH: path.join(root, "src") }) } } } };
}
function vacantPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer(); server.once("error", reject);
    server.listen(0, "127.0.0.1", () => { const port = server.address().port; server.close(() => resolve(port)); });
  });
}
async function request(route, options) {
  const response = await fetch(origin + "/api/v1/" + route, { ...options, signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error(language.text("requestFailure", response.status));
  return response.json();
}
function verifySender(event) {
  if (!ready || !window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame ||
      new URL(event.senderFrame.url).origin !== origin) throw new Error(language.text("invalidClient"));
}
function verifyLanguageSender(event) {
  if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) {
    throw new Error(language.text("invalidClient"));
  }
  const url = new URL(event.senderFrame.url);
  const loading = url.protocol === "file:" && fileURLToPath(url) === path.join(__dirname, "loading.html");
  if (!loading && url.origin !== origin) throw new Error(language.text("invalidClient"));
}
function showShellError(kind, detail) {
  dialog.showMessageBoxSync(window, errorDialogOptions(language, kind, detail));
}
async function start() {
  const port = await vacantPort(); origin = `http://127.0.0.1:${port}`;
  fs.mkdirSync(path.dirname(dataPath), { recursive: true });
  const logDir = path.join(app.getPath("userData"), "logs"); fs.mkdirSync(logDir, { recursive: true });
  const log = fs.createWriteStream(path.join(logDir, "engine.log"), { flags: "a" });
  const env = { ...process.env, CUTVOKE_DESKTOP_KEY: shutdownKey,
    CUTVOKE_HOME: storage.base || path.dirname(dataPath), PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
  if (!engineExists) env.PYTHONPATH = [path.join(root, "src"), ...(qaPython ? [path.join(root, ".venv", "Lib", "site-packages")] : [])].join(path.delimiter);
  // Packaged FFmpeg is selected by the engine entry point, independent of PATH.
  child = spawn(executable, [...prefix, "serve", "--host", "127.0.0.1", "--port", String(port), "--data", dataPath],
    { env, cwd: path.dirname(executable), windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let failure;
  child.once("error", error => { failure = error; });
  child.stdout.pipe(log, { end: false }); child.stderr.pipe(log, { end: false });
  child.once("exit", (code) => {
    log.end();
    if (ready && !closing) {
      showShellError("service", `${language.text("serviceExitCode", code)}\n${language.text("logLocation", logDir)}`);
      app.quit();
    }
  });
  let runtime;
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (failure) throw failure;
    if (child.exitCode !== null) throw new Error(`${language.text("startupExit", child.exitCode)}\n${language.text("logLocation", logDir)}`);
    try { runtime = await request("runtime"); if (runtime.processId === child.pid) break; } catch { /* Startup still in progress. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (!runtime || runtime.processId !== child.pid) throw new Error(`${language.text("startupTimeout")}\n${language.text("logLocation", logDir)}`);
  fs.writeFileSync(path.join(app.getPath("userData"), "mcp-config.json"), JSON.stringify(mcpConfig(), null, 2));
  ready = true;
  await window.loadURL(origin);
  // QA uses the same built client without opening a debug port on normal runs.
  if (process.env.CUTVOKE_DESKTOP_QA === "1") {
    fs.writeFileSync(path.join(app.getPath("userData"), "qa-runtime.json"), JSON.stringify({ origin, runtime, executable }));
    if (process.env.CUTVOKE_DESKTOP_QA_SCRIPT) {
      try {
        await qaHook({ window, app, origin, request });
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

if (storageFailure) {
  app.whenReady().then(() => {
    dialog.showMessageBoxSync({ type: "error", title: "CutVoke — Storage / 存储位置",
      message: "CutVoke cannot write to its data folder. / 无法写入数据文件夹。",
      detail: "Extract the entire CutVoke folder to a writable location, such as Documents, then reopen CutVoke.exe. Keep your existing CutVoke Data folder. No projects were replaced.\n请将整个 CutVoke 文件夹解压到可写位置（如文档），保留原有 CutVoke Data 文件夹后重新打开。工程未被替换。\n\n" + storageFailure.message,
      buttons: ["Close / 关闭"] });
    app.quit();
  });
} else if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    window = new BrowserWindow({ show: process.env.CUTVOKE_DESKTOP_QA !== "1" || process.env.CUTVOKE_DESKTOP_QA_SHOW === "1", width: 1500, height: 950, minWidth: 900, minHeight: 640, backgroundColor: "#101318", title: "CutVoke",
      webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false,
        offscreen: process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_OFFSCREEN === "1" } });
    window.removeMenu();
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    window.webContents.on("will-navigate", (event, url) => { if (new URL(url).origin !== origin) event.preventDefault(); });
    window.webContents.on("will-attach-webview", event => event.preventDefault());
    ipcMain.on("cutvoke:language-state", event => {
      try { verifyLanguageSender(event); event.returnValue = language.snapshot(); }
      catch { event.returnValue = null; }
    });
    ipcMain.handle("cutvoke:language-set", (event, value) => {
      verifyLanguageSender(event); return language.setPreference(value);
    });
    ipcMain.handle("cutvoke:choose-cache", async event => {
      verifySender(event);
      const result = await dialog.showOpenDialog(window, cacheDialogOptions(language));
      return result.canceled ? null : path.join(result.filePaths[0], ".cutvoke-preview-cache");
    });
    ipcMain.handle("cutvoke:reveal-cache", async event => {
      verifySender(event); const status = await request("preview-cache");
      const error = await shell.openPath(status.directory); if (error) throw new Error(error);
    });
    ipcMain.handle("cutvoke:info", event => { verifySender(event); return { version: app.getVersion(), mcp: mcpConfig(),
      storage: { portable: storage.portable, dataPath, profile: storage.profile } }; });
    if (process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_SCRIPT) {
      qaHook = require(path.resolve(process.env.CUTVOKE_DESKTOP_QA_SCRIPT));
    }
    await window.loadFile(path.join(__dirname, "loading.html"));
    try {
      if (qaHook?.loading) await qaHook.loading({ window, app });
      await start();
    } catch (error) { showShellError("startup", error.message); app.quit(); }
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", event => {
    if (closing) return;
    event.preventDefault(); closing = true;
    void stop().finally(() => app.quit());
  });
}
