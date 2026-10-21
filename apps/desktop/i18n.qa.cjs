// Runs only through the existing opt-in desktop QA hook, against isolated data.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { BrowserWindow, dialog, Menu } = require("electron");
const { locales, catalogs } = require("./i18n.cjs");
const output = process.env.CUTVOKE_DESKTOP_QA_HOME;
const scenario = process.env.CUTVOKE_DESKTOP_QA_CASE;
const resultPath = path.join(output, `${scenario}-result.json`);
const result = { scenario, loading: [], cacheDialogs: [], errorDialogs: [] };
const save = () => fs.writeFileSync(resultPath, JSON.stringify(result, null, 2));
async function capture(window) {
  if (!window.webContents.isOffscreen()) return window.webContents.capturePage();
  window.webContents.startPainting();
  await window.webContents.executeJavaScript("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))");
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { window.webContents.removeListener("paint", painted); reject(new Error("Nonempty offscreen paint timed out")); }, 10_000);
    function painted(_event, _dirty, image) {
      const pixels = image.toBitmap();
      let text = 0;
      for (let index = 0; index + 3 < pixels.length; index += 256) if (pixels[index] > 100 && pixels[index + 1] > 100 && pixels[index + 2] > 100) text++;
      if (image.isEmpty() || image.getSize().width < 800 || pixels[0] > 50 || pixels[1] > 50 || pixels[2] > 50 || text < 10) {
        setTimeout(() => { if (!window.isDestroyed()) window.webContents.invalidate(); }, 50);return;
      }
      clearTimeout(timer);window.webContents.removeListener("paint", painted);resolve(image);
    }
    window.webContents.on("paint", painted);
    window.webContents.invalidate();
  });
}
async function until(window, expression) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (await window.webContents.executeJavaScript(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error(`Desktop QA condition timed out: ${expression}`);
}
async function loading(window, label) {
  const state = await window.webContents.executeJavaScript(`({ lang:document.documentElement.lang,dir:document.documentElement.dir,title:document.title,text:document.body.innerText,progress:document.querySelector('[role=progressbar]').getAttribute('aria-label'),bridge:window.cutvokeDesktop.initialLanguage })`);
  assert.equal(state.lang, state.bridge.locale);
  assert.equal(state.title, catalogs[state.lang].loadingTitle);
  assert.ok(state.text.includes(catalogs[state.lang].loadingService));
  assert.equal(state.progress, catalogs[state.lang].loadingProgress);
  assert.equal(state.dir, state.lang === "ar" ? "rtl" : "ltr");
  result.loading.push({ locale: state.lang, title: state.title, dir: state.dir });
  fs.writeFileSync(path.join(output, `${label}-loading.png`), (await capture(window)).toPNG());
  save();
}
async function run({ window, origin, request }) {
  result.origin = origin;
  await until(window, `!!document.querySelector('[data-testid=language-select]')`);
  result.initialWebLocale = await window.webContents.executeJavaScript("document.documentElement.lang");
  assert.equal(result.initialWebLocale, scenario === "reopen" || scenario === "service" ? "ar" : "de");
  assert.equal(Menu.getApplicationMenu(), null);
  if (scenario === "service") {
    save();
    // This stops only this QA instance's real child service. Its exit handler
    // must present the localized error options before the app quits.
    // Stop the exact owned PID from the verified runtime descriptor, exercising
    // the genuine child exit path without exposing its private shutdown key.
    const runtime = await request("runtime");
    process.kill(runtime.processId);
    await new Promise(resolve => setTimeout(resolve, 5000));
    return;
  }
  const targets = scenario === "reopen" ? ["ar"] : locales;
  for (const locale of targets) {
    await window.webContents.executeJavaScript(`{ const select=document.querySelector('[data-testid=language-select]'); select.value=${JSON.stringify(locale)}; select.dispatchEvent(new Event('change',{bubbles:true})); }`);
    await until(window, `document.documentElement.lang===${JSON.stringify(locale)} && window.cutvokeDesktop.getLanguageState()?.locale===${JSON.stringify(locale)}`);
    const chosen = await window.webContents.executeJavaScript("window.cutvokeDesktop.chooseCacheDirectory()");
    assert.equal(chosen, null);
    assert.equal(result.cacheDialogs.at(-1).title, catalogs[locale].chooseCache);
    assert.equal(result.cacheDialogs.at(-1).buttonLabel, catalogs[locale].chooseFolder);
  }
  const before = fs.readFileSync(path.join(output, "ui-language.json"), "utf8");
  await assert.rejects(window.webContents.executeJavaScript(`window.cutvokeDesktop.setLanguagePreference('../en')`));
  assert.equal(fs.readFileSync(path.join(output, "ui-language.json"), "utf8"), before);
  const foreign = new BrowserWindow({ show: false, webPreferences: { preload: path.join(__dirname, "preload.cjs"), sandbox: true, contextIsolation: true, nodeIntegration: false } });
  await foreign.loadFile(path.join(__dirname, "loading.html"));
  assert.equal(await foreign.webContents.executeJavaScript("window.cutvokeDesktop.getLanguageState()"), null);
  await assert.rejects(foreign.webContents.executeJavaScript(`window.cutvokeDesktop.setLanguagePreference('de')`));
  foreign.destroy();
  result.invalidPreferenceRejected = true;
  result.foreignRendererRejected = true;
  fs.writeFileSync(path.join(output, `${scenario}-web-ar.png`), (await capture(window)).toPNG());
  await window.loadFile(path.join(__dirname, "loading.html"));
  await loading(window, `${scenario}-ar`);
  result.finalPreference = JSON.parse(fs.readFileSync(path.join(output, "ui-language.json"), "utf8")).preference;
  assert.equal(result.finalPreference, "ar");
  result.completed = true;
  save();
}
run.loading = async ({ window }) => {
  dialog.showOpenDialog = async (_owner, options) => {
    result.cacheDialogs.push(options);save();return { canceled: true, filePaths: [] };
  };
  dialog.showMessageBoxSync = (_owner, options) => {
    result.errorDialogs.push(options);save();return 0;
  };
  await loading(window, `${scenario}-initial`);
};
module.exports = run;
