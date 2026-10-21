const { test } = require("node:test");
const assert = require("node:assert/strict");
const { spawn, execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "../..");

test("real desktop setup, persisted preferences, migration and reopen", { timeout: 240000 }, async () => {
  const output = path.join(root, ".artifacts", "acceptance", "settings");
  fs.mkdirSync(output, { recursive: true });
  const profile = fs.mkdtempSync(path.join(output, "session-"));
  fs.writeFileSync(path.join(profile, "ui-language.json"), JSON.stringify({ preference: "zh-CN" }));
  const python = execFileSync(path.join(root, ".venv", "Scripts", "python.exe"),
    ["-c", "import sys; print(sys._base_executable)"], { encoding: "utf8", windowsHide: true }).trim();
  for (const scenario of ["setup", "migrate", "reopen"]) {
    const env = { ...process.env, CUTVOKE_DESKTOP_QA: "1", CUTVOKE_DESKTOP_QA_PYTHON: "1",
      CUTVOKE_DESKTOP_QA_OFFSCREEN: "1", CUTVOKE_DESKTOP_QA_HOME: profile,
      CUTVOKE_DESKTOP_QA_CASE: scenario, CUTVOKE_DESKTOP_QA_PYTHON_EXE: python,
      CUTVOKE_DESKTOP_QA_SCRIPT: path.join(__dirname, "settings.qa.cjs") };
    delete env.ELECTRON_RUN_AS_NODE; delete env.CUTVOKE_DATA;
    const child = spawn(path.join(__dirname, "node_modules", "electron", "dist", "electron.exe"),
      [__dirname], { env, cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let log = "";
    child.stdout.on("data", data => { log += data; }); child.stderr.on("data", data => { log += data; });
    const timer = setTimeout(() => child.kill(), 70000);
    const code = await new Promise((resolve, reject) => { child.once("exit", resolve); child.once("error", reject); });
    clearTimeout(timer);
    fs.writeFileSync(path.join(profile, scenario + ".log"), log);
    const error = path.join(profile, "qa-error.txt");
    assert.ok(!fs.existsSync(error), fs.existsSync(error) ? fs.readFileSync(error, "utf8") : "");
    assert.equal(code, 0, log.slice(-4000));
    assert.equal(JSON.parse(fs.readFileSync(path.join(profile, scenario + "-result.json"), "utf8")).passed, true);
  }
  fs.writeFileSync(path.join(output, "verification.json"), JSON.stringify({ passed: true, profile,
    scope: "Real Electron and Python backend; UI setup/save, language switching, invalid capacity, forbidden IPC, occupied target rejection, copied library and preserved original, fresh process reopen. Relaunch dispatch intercepted; runner performs each new process launch." }, null, 2));
});
