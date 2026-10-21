// Opt-in real Electron QA. Every path belongs to the test profile.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { dialog, BrowserWindow } = require("electron");
const profile = process.env.CUTVOKE_DESKTOP_QA_HOME;
const scenario = process.env.CUTVOKE_DESKTOP_QA_CASE;
const result = { scenario };
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(window, expression) {
  const end = Date.now() + 45000;
  while (Date.now() < end) {
    if (await window.webContents.executeJavaScript(expression)) return;
    await pause(50);
  }
  throw new Error(`Timed out: ${expression}`);
}
async function capture(window, name) {
  window.webContents.startPainting();
  const image = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("No painted frame")), 10000);
    window.webContents.once("paint", (_event, _rect, frame) => { clearTimeout(timer); resolve(frame); });
    window.webContents.invalidate();
  });
  fs.writeFileSync(path.join(profile, name + ".png"), image.toPNG());
}
async function input(window, selector, value) {
  await window.webContents.executeJavaScript(`{
    const input = document.querySelector(${JSON.stringify(selector)});
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }`);
}
async function clickText(window, text) {
  await window.webContents.executeJavaScript(`Array.from(document.querySelectorAll('dialog button')).find(x=>x.textContent===${JSON.stringify(text)}).click()`);
}
module.exports = async ({ window, app, request }) => {
  // Avoid spawning unattended processes; the test runner performs the next
  // real launch itself and verifies persisted settings on that fresh process.
  app.relaunch = () => { result.relaunchRequested = true; };
  dialog.showMessageBoxSync = (_window, options) => { result.shellError = options; return 0; };
  await until(window, "!!document.querySelector('.cache-settings-trigger')");
  result.initial = await window.webContents.executeJavaScript("window.cutvokeDesktop.settings()");
  const moved = path.join(profile, "新 工程库");
  if (scenario === "setup") {
    await until(window, "!!document.querySelector('dialog[open] input[type=number]')");
    assert.equal(result.initial.setupComplete, false);
    await capture(window, "first-run");
    window.setSize(900, 640);
    await pause(100);
    assert.equal(await window.webContents.executeJavaScript(`{
      const box=document.querySelector('dialog').getBoundingClientRect();
      box.left>=0 && box.right<=innerWidth && box.top>=0 && box.bottom<=innerHeight
    }`), true);
    await capture(window, "first-run-compact");
    window.setSize(1500, 950);
    await window.webContents.executeJavaScript(`{const s=document.querySelector('dialog select');s.value='en';s.dispatchEvent(new Event('change',{bubbles:true}));}`);
    await until(window, "document.documentElement.lang === 'en'");
    await window.webContents.executeJavaScript(`{const s=document.querySelector('dialog select');s.value='zh-CN';s.dispatchEvent(new Event('change',{bubbles:true}));}`);
    await input(window, "dialog input[type=number]", "0");
    await clickText(window, "保存并开始使用");
    await until(window, "document.querySelector('dialog [role=alert]')?.textContent.includes('0.25')");
    assert.equal((await window.webContents.executeJavaScript("window.cutvokeDesktop.settings()")).setupComplete, false);
    await input(window, "dialog input[type=number]", "2");
    dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [path.join(profile, "自定义 缓存")] });
    await window.webContents.executeJavaScript("document.querySelectorAll('dialog .cache-settings__directory button')[1].click()");
    await until(window, "document.querySelectorAll('dialog .cache-settings__directory input')[1].value.includes('自定义')");
    await clickText(window, "保存并开始使用");
    await until(window, "!document.querySelector('dialog[open]')");
    const cache = await request("preview-cache");
    assert.equal(cache.maxBytes, 2 * 1024 ** 3);
    assert.equal(cache.directory, path.join(profile, "自定义 缓存", ".cutvoke-preview-cache"));
    await request("projects", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ projectId: "settings-survivor", name: "设置迁移保留工程", width: 320, height: 180, fps: 30 }) });
    fs.writeFileSync(path.join(result.initial.projectDirectory, "media", "owned-fixture.txt"), "preserve my media");
    result.setup = await window.webContents.executeJavaScript("window.cutvokeDesktop.settings()");
    assert.equal(result.setup.setupComplete, true);
  } else {
    await pause(500);
    assert.equal(result.initial.setupComplete, true);
    assert.equal(await window.webContents.executeJavaScript("!!document.querySelector('dialog[open]')"), false);
    await window.webContents.executeJavaScript("document.querySelector('.cache-settings-trigger').click()");
    await until(window, "!!document.querySelector('dialog input[type=number]')");
    assert.equal((await request("preview-cache")).maxBytes, 2 * 1024 ** 3);
    if (scenario === "migrate") {
      const occupied = path.join(profile, "occupied"); fs.mkdirSync(occupied); fs.writeFileSync(path.join(occupied, "keep"), "keep");
      await assert.rejects(window.webContents.executeJavaScript(`window.cutvokeDesktop.saveSettings({
        projectDirectory:${JSON.stringify(occupied)},cacheDirectory:${JSON.stringify((await request("preview-cache")).directory)},maxBytes:2147483648})`));
      assert.equal(fs.readFileSync(path.join(occupied, "keep"), "utf8"), "keep");
      const foreign = new BrowserWindow({ show: false, webPreferences: { preload: path.join(__dirname, "preload.cjs"), sandbox: true, contextIsolation: true } });
      await foreign.loadFile(path.join(__dirname, "loading.html"));
      await assert.rejects(foreign.webContents.executeJavaScript("window.cutvokeDesktop.settings()"));
      foreign.destroy();
      await input(window, ".settings-section input", moved);
      await until(window, "!!document.querySelector('.settings-confirm input')");
      await window.webContents.executeJavaScript("document.querySelector('.settings-confirm input').click()");
      await capture(window, "settings-migration");
      await clickText(window, "迁移并重新打开");
      const end = Date.now() + 45000;
      while (!result.relaunchRequested && Date.now() < end) await pause(25);
      assert.equal(result.relaunchRequested, true);
      assert.ok(!result.shellError, JSON.stringify(result.shellError));
    } else {
      assert.equal(result.initial.projectDirectory, moved);
      const project = await request("projects/settings-survivor");
      assert.equal(project.name, "设置迁移保留工程");
      assert.equal(fs.readFileSync(path.join(moved, "media", "owned-fixture.txt"), "utf8"), "preserve my media");
      assert.ok(fs.existsSync(path.join(profile, "data", "projects.sqlite")));
      assert.equal((await request("preview-cache")).directory, path.join(profile, "自定义 缓存", ".cutvoke-preview-cache"));
      await capture(window, "settings-reopened");
    }
  }
  result.passed = true;
  fs.writeFileSync(path.join(profile, scenario + "-result.json"), JSON.stringify(result, null, 2));
};
