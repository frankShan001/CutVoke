const { app, BrowserWindow, dialog, ipcMain, shell, session, Menu } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const net = require("node:net");
const crypto = require("node:crypto");
const { fileURLToPath } = require("node:url");
const { createLanguageController, cacheDialogOptions, errorDialogOptions } = require("./i18n.cjs");
const { storagePlan, prepareStorage } = require("./storage.cjs");
const { readSettings, writeSettings, validateDirectory, validateDestination } = require("./settings.cjs");

app.setName("CutVoke");
const storage = storagePlan({ packaged: app.isPackaged, executable: process.execPath,
  appData: app.getPath("appData"), home: app.getPath("home") });
let storageFailure;
let preferences = { setupComplete: false };
try {
  prepareStorage(storage, { profileOnly: true });
  preferences = readSettings(storage.profile);
  if (!process.env.CUTVOKE_DATA && preferences.dataPath) storage.dataPath = preferences.dataPath;
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

if (preferences.gpuAcceleration === false ||
    (process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_OFFSCREEN === "1")) app.disableHardwareAcceleration();

let child, window, origin, qaHook, closing = false, windowClosing = false, ready = false, savingSettings = false;
const language = createLanguageController(path.join(app.getPath("userData"), "ui-language.json"),
  () => app.getPreferredSystemLanguages());
const shutdownKey = crypto.randomBytes(32).toString("hex");
const root = path.resolve(__dirname, "../..");
const dataPath = storage.dataPath;
const fontCache = path.resolve(process.env.CUTVOKE_FONT_CACHE || path.join(path.dirname(dataPath), "font-cache"));
const engineName = process.platform === "win32" ? "cutvoke-engine.exe" : "cutvoke-engine";
const engine = app.isPackaged ? path.join(process.resourcesPath, "engine", engineName)
  : path.join(root, ".artifacts", "build", "desktop-engine", "cutvoke-engine", engineName);
const engineExists = fs.existsSync(engine) && !(process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_PYTHON === "1");
const qaPython = process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_PYTHON === "1"
  ? process.env.CUTVOKE_DESKTOP_QA_PYTHON_EXE : undefined;
const executable = engineExists ? engine : qaPython || (process.platform === "win32"
  ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python"));
const prefix = engineExists ? [] : ["-m", "cutvoke"];

function mcpConfig() {
  return { mcpServers: { cutvoke: { command: executable, args: [...prefix, "mcp", "--data", dataPath],
    env: { CUTVOKE_HOME: storage.base || path.dirname(dataPath), CUTVOKE_FONT_CACHE: fontCache,
      CUTVOKE_GPU_ACCELERATION: preferences.gpuAcceleration === false ? "0" : "1",
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
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.error?.message || language.text("requestFailure", response.status));
  }
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
  console.error(`CutVoke ${kind}: ${detail}`);
  dialog.showMessageBoxSync(window, errorDialogOptions(language, kind, detail));
}
async function start() {
  prepareStorage(storage);
  const port = await vacantPort(); origin = `http://127.0.0.1:${port}`;
  if (closing || windowClosing || window.isDestroyed()) return;
  fs.mkdirSync(path.dirname(dataPath), { recursive: true });
  const logDir = path.join(app.getPath("userData"), "logs"); fs.mkdirSync(logDir, { recursive: true });
  const log = fs.createWriteStream(path.join(logDir, "engine.log"), { flags: "a" });
  const env = { ...process.env, CUTVOKE_DESKTOP_KEY: shutdownKey,
    CUTVOKE_GPU_ACCELERATION: preferences.gpuAcceleration === false ? "0" : "1",
    CUTVOKE_HOME: storage.base || path.dirname(dataPath), CUTVOKE_FONT_CACHE: fontCache,
    PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
  if (!engineExists) env.PYTHONPATH = [path.join(root, "src"), ...(qaPython ? [path.join(root, ".venv", "Lib", "site-packages")] : [])].join(path.delimiter);
  // Packaged FFmpeg is selected by the engine entry point, independent of PATH.
  child = spawn(executable, [...prefix, "serve", "--host", "127.0.0.1", "--port", String(port), "--data", dataPath],
    { env, cwd: path.dirname(executable), windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let failure;
  child.once("error", error => { failure = error; });
  child.stdout.pipe(log, { end: false }); child.stderr.pipe(log, { end: false });
  child.once("exit", (code) => {
    log.end();
    if (ready && !closing && !windowClosing) {
      showShellError("service", `${language.text("serviceExitCode", code)}\n${language.text("logLocation", logDir)}`);
      app.quit();
    }
  });
  let runtime;
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (closing || windowClosing) return;
    if (failure) throw failure;
    if (child.exitCode !== null) throw new Error(`${language.text("startupExit", child.exitCode)}\n${language.text("logLocation", logDir)}`);
    try { runtime = await request("runtime"); if (runtime.processId === child.pid) break; } catch { /* Startup still in progress. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  if (closing || windowClosing || window.isDestroyed()) return;
  if (!runtime || runtime.processId !== child.pid) throw new Error(`${language.text("startupTimeout")}\n${language.text("logLocation", logDir)}`);
  ready = true;
  await window.loadURL(origin);
  if (closing || windowClosing || window.isDestroyed()) return;
  fs.writeFileSync(path.join(app.getPath("userData"), "mcp-config.json"), JSON.stringify(mcpConfig(), null, 2));
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
async function stop(strict = false) {
  if (!child || child.exitCode !== null) return;
  try {
    await request("runtime/shutdown", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: shutdownKey }) });
    await Promise.race([new Promise(resolve => child.once("exit", resolve)), new Promise(resolve => setTimeout(resolve, 12000))]);
  } catch { /* The process may already have exited. */ }
  if (child.exitCode === null) {
    if (strict) throw new Error("The editor is still working. Try again later. / 编辑器仍在处理任务，请稍后重试。");
    child.kill(); // Only the process this client owns.
  }
}

function settingsState() {
  return { setupComplete: preferences.setupComplete, projectDirectory: path.dirname(dataPath),
    projectLocationManaged: !!process.env.CUTVOKE_DATA, gpuAcceleration: preferences.gpuAcceleration !== false };
}

async function saveSettings(value) {
  if (savingSettings) throw new Error("Settings are being saved / 正在保存设置");
  const directory = validateDirectory(value.projectDirectory);
  const cacheDirectory = validateDirectory(value.cacheDirectory);
  const maxBytes = value.maxBytes;
  if (value.gpuAcceleration !== undefined && typeof value.gpuAcceleration !== "boolean") {
    throw new Error("GPU acceleration must be enabled or disabled / GPU 加速设置必须为布尔值");
  }
  const gpuAcceleration = value.gpuAcceleration ?? (preferences.gpuAcceleration !== false);
  const gpuChanged = gpuAcceleration !== (preferences.gpuAcceleration !== false);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 256 * 1024 ** 2 || maxBytes > 1024 ** 4) {
    throw new Error("Cache capacity must be 0.25–1024 GiB / 缓存容量应为 0.25–1024 GiB");
  }
  const moving = directory !== path.dirname(dataPath);
  if (moving && process.env.CUTVOKE_DATA) throw new Error("CUTVOKE_DATA controls the project location / 工程位置由 CUTVOKE_DATA 指定");
  if (moving) validateDestination(dataPath, directory);
  savingSettings = true;
  try {
    if (moving) {
      await request("settings/preflight");
      // Suppress the expected service-exit dialog while draining our own engine.
      ready = false;
      await stop(true);
      const migrated = await new Promise((resolve, reject) => {
        const env = { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" };
        if (!engineExists) env.PYTHONPATH = [path.join(root, "src"), ...(qaPython ? [path.join(root, ".venv", "Lib", "site-packages")] : [])].join(path.delimiter);
        const worker = spawn(executable, [...prefix, "storage-migrate", "--source", dataPath,
          "--destination", directory, "--cache-directory", cacheDirectory, "--cache-bytes", String(maxBytes)],
          { env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
        let output = "", errors = "";
        worker.stdout.on("data", data => { output += data; });
        worker.stderr.on("data", data => { errors = (errors + data).slice(-8000); });
        worker.once("error", reject);
        worker.once("exit", code => {
          try {
            const result = JSON.parse(output);
            if (code !== 0 || !result.ok) throw new Error(result.error?.message || errors);
            resolve(result.dataPath);
          } catch (error) { reject(new Error(error.message || errors)); }
        });
      });
      writeSettings(storage.profile, { setupComplete: true, dataPath: migrated, gpuAcceleration });
      // The next launch regenerates the Agent config for the new library.
      app.relaunch();
      setTimeout(() => { savingSettings = false; app.quit(); }, 150);
      return { restartRequired: true };
    }
    if (gpuChanged) await request("settings/preflight");
    const previous = await request("preview-cache");
    await request("preview-cache", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ directory: cacheDirectory, maxBytes }) });
    try {
      writeSettings(storage.profile, { setupComplete: true, dataPath, gpuAcceleration });
    } catch (error) {
      await request("preview-cache", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ directory: previous.directory, maxBytes: previous.maxBytes }) });
      throw error;
    }
    preferences = { setupComplete: true, dataPath, gpuAcceleration };
    if (gpuChanged) {
      app.relaunch();
      setTimeout(() => { savingSettings = false; app.quit(); }, 150);
      return { restartRequired: true };
    }
    return { restartRequired: false };
  } catch (error) {
    if (!ready) {
      // Keep the original library selected after any migration/save failure.
      showShellError("startup", error.message);
      app.relaunch();
      setTimeout(() => { savingSettings = false; app.quit(); }, 150);
    }
    throw error;
  } finally {
    savingSettings = false;
  }
}

if (storageFailure) {
  app.whenReady().then(() => {
    dialog.showMessageBoxSync({ type: "error", title: "CutVoke — Storage / 存储位置",
      message: "CutVoke cannot write to its data folder. / 无法写入数据文件夹。",
      detail: "Check write permission and free space for the data location below, then reopen CutVoke. Keep your existing data. No projects were replaced.\n请检查下方数据位置的写入权限与剩余空间，保留原有数据后重新打开。工程未被替换。\n\n" + storage.profile + "\n" + storageFailure.message,
      buttons: ["Close / 关闭"] });
    app.quit();
  });
} else if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => { if (window) { if (window.isMinimized()) window.restore(); window.focus(); } });
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(process.platform === "darwin" ? Menu.buildFromTemplate([
      { role: "appMenu" }, { role: "editMenu" }, { role: "windowMenu" },
    ]) : null);
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    window = new BrowserWindow({ show: process.env.CUTVOKE_DESKTOP_QA !== "1" || process.env.CUTVOKE_DESKTOP_QA_SHOW === "1", width: 1500, height: 950, minWidth: 900, minHeight: 640, backgroundColor: "#101318", title: "CutVoke",
      icon: path.join(__dirname, "assets", "brand", "icon-256.png"),
      webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false,
        offscreen: process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_OFFSCREEN === "1" } });
    window.removeMenu();
    // A native close request can precede window destruction and before-quit.
    // Stop startup immediately while the existing quit path drains its child.
    window.on("close", event => { if (savingSettings) { event.preventDefault(); return; } windowClosing = true; });
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
      storage: { portable: storage.portable, dataPath, profile: storage.profile, fontCache } }; });
    ipcMain.handle("cutvoke:settings", event => { verifySender(event); return settingsState(); });
    ipcMain.handle("cutvoke:settings-save", (event, value) => { verifySender(event); return saveSettings(value); });
    ipcMain.handle("cutvoke:choose-project-directory", async event => {
      verifySender(event);
      const result = await dialog.showOpenDialog(window, { properties: ["openDirectory", "createDirectory"],
        defaultPath: path.dirname(dataPath) });
      return result.canceled ? null : result.filePaths[0];
    });
    ipcMain.handle("cutvoke:choose-extension-directory", async event => {
      verifySender(event);
      const result = await dialog.showOpenDialog(window, { properties: ["openDirectory", "createDirectory"] });
      return result.canceled ? null : result.filePaths[0];
    });
    ipcMain.handle("cutvoke:choose-extension-bundle", async event => {
      verifySender(event);
      const result = await dialog.showOpenDialog(window, { properties: ["openFile"], filters: [{ name: "ZIP", extensions: ["zip"] }] });
      return result.canceled ? null : result.filePaths[0];
    });
    ipcMain.handle("cutvoke:choose-export", async (event, suggested) => {
      verifySender(event);
      const value = typeof suggested === "string" ? suggested.slice(0, 4096) : "";
      const filename = path.basename(value || "CutVoke.mp4").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_");
      const extension = path.extname(value).toLowerCase() === ".mov" ? "mov" : "mp4";
      const defaultPath = path.isAbsolute(value) && [".mp4", ".mov"].includes(path.extname(value).toLowerCase())
        ? value : path.join(app.getPath("videos"), filename.toLowerCase().endsWith(`.${extension}`) ? filename : filename + `.${extension}`);
      const result = await dialog.showSaveDialog(window, { defaultPath, filters: [{ name: extension === "mov" ? "ProRes MOV" : "MP4", extensions: [extension] }],
        properties: ["createDirectory", "showOverwriteConfirmation"] });
      return result.canceled ? null : result.filePath;
    });
    ipcMain.handle("cutvoke:reveal-export", async (event, projectId, jobId) => {
      verifySender(event);
      if (typeof projectId !== "string" || typeof jobId !== "string") throw new Error("Invalid export task");
      const { jobs } = await request(`projects/${encodeURIComponent(projectId)}/exports`);
      const job = jobs.find(item => item.jobId === jobId && item.status === "succeeded");
      const output = job?.result?.output_path;
      if (!output || !path.isAbsolute(output) || !fs.statSync(output).isFile()) throw new Error("Export file unavailable");
      shell.showItemInFolder(output);
    });
    if (process.env.CUTVOKE_DESKTOP_QA === "1" && process.env.CUTVOKE_DESKTOP_QA_SCRIPT) {
      qaHook = require(path.resolve(process.env.CUTVOKE_DESKTOP_QA_SCRIPT));
    }
    await window.loadFile(path.join(__dirname, "loading.html"));
    try {
      if (qaHook?.loading) await qaHook.loading({ window, app });
      await start();
    } catch (error) { if (!closing && !windowClosing) showShellError("startup", error.message); app.quit(); }
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", event => {
    if (savingSettings) { event.preventDefault(); return; }
    if (closing) return;
    event.preventDefault(); closing = true;
    void stop().finally(() => app.quit());
  });
}
