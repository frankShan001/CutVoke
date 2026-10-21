const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { catalogs } = require("./i18n.cjs");
const root = path.resolve(__dirname, "../..");
const output = path.join(root, ".artifacts", "acceptance", "i18n-shell");
const executable = path.join(__dirname, "node_modules", "electron", "dist", "electron.exe");
// Windows' venv redirector spawns a second PID. Use its matching base runtime
// directly in QA so the product's owned-process identity check remains intact.
const python = execFileSync(path.join(root, ".venv", "Scripts", "python.exe"), ["-c", "import sys; print(sys._base_executable)"], { encoding: "utf8", windowsHide: true }).trim();
async function run(scenario, profile, data) {
  const env = {
    ...process.env, CUTVOKE_DESKTOP_QA: "1", CUTVOKE_DESKTOP_QA_PYTHON: "1", CUTVOKE_DESKTOP_QA_OFFSCREEN: "1",
    CUTVOKE_DESKTOP_QA_HOME: profile, CUTVOKE_DESKTOP_QA_CASE: scenario,
    CUTVOKE_DESKTOP_QA_PYTHON_EXE: python,
    CUTVOKE_DESKTOP_QA_SCRIPT: path.join(__dirname, "i18n.qa.cjs"), CUTVOKE_DATA: data,
    PYTHONIOENCODING: "utf-8",
  };
  delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(executable, [__dirname], { cwd: root, windowsHide: true, env, stdio: ["ignore", "pipe", "pipe"] });
  let log = "";
  const append = chunk => { log += chunk; fs.writeFileSync(path.join(profile, `${scenario}-electron.log`), log); };
  child.stdout.on("data", append);child.stderr.on("data", append);
  const timeout = setTimeout(() => child.kill(), 120_000);
  const code = await new Promise((resolve, reject) => { child.once("error", reject);child.once("exit", resolve); });
  clearTimeout(timeout);
  fs.writeFileSync(path.join(profile, `${scenario}-electron.log`), log);
  assert.equal(code, 0, log.slice(-6000));
  const errorPath = path.join(profile, "qa-error.txt");
  assert.ok(!fs.existsSync(errorPath), fs.existsSync(errorPath) ? fs.readFileSync(errorPath, "utf8") : "");
  return JSON.parse(fs.readFileSync(path.join(profile, `${scenario}-result.json`), "utf8"));
}
test("real Electron shell localizes loading, IPC dialogs and restart preference; rejects foreign senders", { timeout: 360_000 }, async () => {
  fs.mkdirSync(output, { recursive: true });
  const profile = fs.mkdtempSync(path.join(output, "session-"));
  fs.writeFileSync(path.join(profile, "ui-language.json"), JSON.stringify({ preference: "de" }));
  const data = path.join(profile, "data");
  const first = await run("switch", profile, data);
  assert.equal(first.completed, true);assert.equal(first.cacheDialogs.length, 11);
  assert.equal(first.loading[0].locale, "de");assert.equal(first.loading.at(-1).locale, "ar");
  assert.ok(first.invalidPreferenceRejected && first.foreignRendererRejected);
  const reopened = await run("reopen", profile, data);
  assert.equal(reopened.completed, true);assert.equal(reopened.initialWebLocale, "ar");
  assert.notEqual(reopened.origin, first.origin);
  const stopped = await run("service", profile, data);
  assert.equal(stopped.errorDialogs.length, 1);
  assert.equal(stopped.errorDialogs[0].title, catalogs.ar.serviceTitle);
  assert.equal(stopped.errorDialogs[0].buttons[0], catalogs.ar.close);
  const failureProfile = fs.mkdtempSync(path.join(output, "startup-"));
  fs.writeFileSync(path.join(failureProfile, "ui-language.json"), JSON.stringify({ preference: "de" }));
  const occupied = path.join(failureProfile, "occupied");fs.writeFileSync(occupied, "isolated startup failure fixture");
  const failed = await run("startup", failureProfile, occupied);
  assert.equal(failed.errorDialogs.length, 1);
  assert.equal(failed.errorDialogs[0].title, catalogs.de.startupTitle);
  assert.equal(failed.errorDialogs[0].buttons[0], catalogs.de.close);
  assert.match(failed.errorDialogs[0].detail, /EEXIST|ENOTDIR/);
  fs.writeFileSync(path.join(output, "verification.json"), JSON.stringify({ passed: true, actualElectron: true,
    scope: "Actual Electron with QA-only software offscreen rendering: loading, built Web UI, language IPC, process restart on a new origin, rejected foreign renderer, real startup failure and real child service exit. Native dialog options intercepted; OS dialog chrome is not visually verified.",
    profile, failureProfile, localesVerified: 11, scenarios: ["switch", "reopen", "service", "startup"] }, null, 2));
});
