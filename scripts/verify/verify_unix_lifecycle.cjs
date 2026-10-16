const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawn, execFileSync } = require("node:child_process");
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(check, timeout = 90_000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await check(); if (result) return result; await delay(200); }
  throw new Error("Timed out waiting for native application lifecycle");
}
function alive(pid) { try { process.kill(pid, 0); return true; } catch { return false; } }
function children(pid) {
  return execFileSync("/bin/ps", ["-axo", "pid=,ppid=,command="], { encoding: "utf8" }).split("\n")
    .map(line => line.match(/^\s*(\d+)\s+(\d+)\s+(.+)$/)).filter(m => m && Number(m[2]) === pid)
    .map(m => ({ pid: Number(m[1]), command: m[3] }));
}
function start(executable, env, out, suffix) {
  const log = fs.openSync(path.join(out, `${suffix}.log`), "a");
  const child = spawn(executable, [], { env, stdio: ["ignore", log, log] });
  fs.closeSync(log); child.on("error", error => { child.launchError = error; });
  return child;
}
function closeWindow(child) {
  if (process.platform === "darwin") {
    execFileSync("/usr/bin/osascript", ["-e", 'tell application id "com.cutvoke.editor" to quit'], { timeout: 20_000 });
  } else {
    const ids = execFileSync("/usr/bin/xdotool", ["search", "--onlyvisible", "--pid", String(child.pid)], { encoding: "utf8" }).trim().split(/\s+/);
    execFileSync("/usr/bin/xdotool", ["windowactivate", "--sync", ids[0], "key", "alt+F4"], { timeout: 10_000 });
  }
}
async function verifyNormalLaunch({ executable, env, out, data, expectedProject, projectId, report }) {
  // No CUTVOKE_DESKTOP_QA, CDP, debugging, offscreen or sandbox-disable flags.
  const config = path.join(data, "profile/mcp-config.json");
  fs.rmSync(config, { force: true });
  const child = start(executable, env, out, "normal-startup");
  let backend;
  try {
    backend = await waitFor(async () => {
      if (child.launchError) throw child.launchError;
      assert.equal(child.exitCode, null, "Normal application exited before startup");
      const candidate = children(child.pid).find(p => p.command.includes("cutvoke-engine") && /--port \d+/.test(p.command));
      if (!candidate) return false;
      const origin = `http://127.0.0.1:${candidate.command.match(/--port (\d+)/)[1]}`;
      try {
        const identity = await (await fetch(origin + "/api/v1/runtime")).json();
        if (identity.processId !== candidate.pid) return false;
        return { ...candidate, origin, identity };
      } catch { return false; }
    });
    assert.equal(backend.identity.dataPath, path.join(data, "data/projects.sqlite"));
    await waitFor(() => fs.existsSync(config));
    assert.deepEqual(await (await fetch(backend.origin + `/api/v1/projects/${projectId}`)).json(), expectedProject);
    closeWindow(child);
    await waitFor(() => child.exitCode !== null, 30_000);
    await waitFor(() => !alive(backend.pid), 10_000);
    await assert.rejects(fetch(backend.origin + "/api/v1/runtime"));
    report.normalStartup = { passed: true, pid: child.pid, backend: backend.identity, flags: [] };
    report.checks.normalLaunchWithoutQaOrDebugFlags = true;
  } finally {
    if (child.exitCode === null && child.signalCode === null) terminateOwnedApplication(child.pid);
    if (backend && alive(backend.pid)) process.kill(backend.pid);
  }
}
async function verifyReadonlyStorage({ executable, env, out, data, profile, report }) {
  const database = path.join(data, "data/projects.sqlite");
  const original = fs.readFileSync(database);
  const mode = fs.statSync(profile).mode & 0o777;
  fs.chmodSync(profile, 0o555);
  const child = start(executable, env, out, "readonly-storage");
  try {
    await waitFor(async () => {
      if (child.launchError) throw child.launchError;
      assert.equal(child.exitCode, null, "Storage error must remain visible until acknowledged");
      if (process.platform === "darwin") {
        try {
          const text = execFileSync("/usr/bin/osascript", ["-e", 'tell application "System Events" to tell process "CutVoke" to get value of every static text of every window'], { encoding: "utf8", timeout: 10_000 });
          fs.writeFileSync(path.join(out, "readonly-dialog.txt"), text);
          return text.includes("cannot write") || text.includes("无法写入");
        } catch (error) {
          // The native process/dialog can appear after the first observation.
          fs.writeFileSync(path.join(out, "readonly-dialog-query-error.txt"), error.message);
          return false;
        }
      }
      try {
        const ids = execFileSync("/usr/bin/xdotool", ["search", "--onlyvisible", "--pid", String(child.pid)], { encoding: "utf8" }).trim().split(/\s+/);
        const title = execFileSync("/usr/bin/xdotool", ["getwindowname", ids[0]], { encoding: "utf8" });
        if (!title.includes("CutVoke") || !title.includes("Storage")) return false;
        execFileSync("/usr/bin/xdotool", ["windowactivate", "--sync", ids[0]], { timeout: 10_000 });
        await delay(400); // Wait for the native GTK dialog to finish painting.
        const capture = path.join(out, "readonly-dialog.png");
        execFileSync("/usr/bin/scrot", ["-u", "-o", capture], { timeout: 10_000 });
        const text = execFileSync("/usr/bin/tesseract", [capture, "stdout", "-l", "eng", "--psm", "6"], { encoding: "utf8", timeout: 20_000 });
        fs.writeFileSync(path.join(out, "readonly-dialog.txt"), text);
        const normalized = text.replace(/\s+/g, " ").toLowerCase();
        report.readonlyDialog = { pid: child.pid, windowId: ids[0], title: title.trim(),
          method: "Owned visible X11 window + actual GTK screenshot/OCR", text };
        return normalized.includes("cannot write") && normalized.includes("data folder");
      } catch (error) {
        fs.writeFileSync(path.join(out, "readonly-dialog-query-error.txt"), error.message);
        return false;
      }
    });
    assert.equal(children(child.pid).filter(p => p.command.includes("cutvoke-engine")).length, 0);
    if (process.platform === "darwin") {
      execFileSync("/usr/bin/osascript", ["-e", 'tell application "System Events" to tell process "CutVoke" to click button 1 of window 1'], { timeout: 10_000 });
    } else closeWindow(child);
    await waitFor(() => child.exitCode !== null, 30_000);
    assert.deepEqual(fs.readFileSync(database), original);
    report.checks.readonlyDataShowsNativeErrorAndPreservesDatabase = true;
  } finally {
    if (child.exitCode === null && child.signalCode === null) terminateOwnedApplication(child.pid);
    fs.chmodSync(profile, mode);
  }
}
async function verifyPlatformStorage({ executable, env, out, folder, report }) {
  const nativeEnv = { ...env };
  delete nativeEnv.CUTVOKE_HOME;
  delete nativeEnv.CUTVOKE_DATA;
  if (process.platform === "linux") nativeEnv.XDG_DATA_HOME = path.join(out, "XDG 系统 数据");
  const data = process.platform === "darwin" ? path.join(os.homedir(), "Library/Application Support/CutVoke")
    : path.join(nativeEnv.XDG_DATA_HOME, "CutVoke");
  assert.equal(fs.existsSync(data), false, "Use a fresh hosted-runner personal data location");
  // Make the entire extracted application read-only; only its external data is writable.
  const modes = [];
  function protect(file) {
    const stat = fs.lstatSync(file);
    if (stat.isSymbolicLink()) return;
    modes.push([file, stat.mode & 0o777]);
    if (stat.isDirectory()) for (const name of fs.readdirSync(file)) protect(path.join(file, name));
    fs.chmodSync(file, (stat.mode & 0o777) & ~0o222);
  }
  protect(folder);
  const child = start(executable, nativeEnv, out, "platform-default-storage");
  let backend;
  try {
    backend = await waitFor(async () => {
      if (child.launchError) throw child.launchError;
      assert.equal(child.exitCode, null);
      const candidate = children(child.pid).find(p => p.command.includes("cutvoke-engine") && /--port \d+/.test(p.command));
      if (!candidate) return false;
      const origin = `http://127.0.0.1:${candidate.command.match(/--port (\d+)/)[1]}`;
      try {
        const identity = await (await fetch(origin + "/api/v1/runtime")).json();
        return identity.processId === candidate.pid ? { pid: candidate.pid, origin, identity } : false;
      } catch { return false; }
    });
    assert.equal(backend.identity.dataPath, path.join(data, "data/projects.sqlite"));
    await waitFor(() => fs.existsSync(path.join(data, "profile/mcp-config.json")));
    closeWindow(child);
    await waitFor(() => child.exitCode !== null, 30_000);
    await waitFor(() => !alive(backend.pid), 10_000);
    report.platformDefaultStorage = { root: data, database: backend.identity.dataPath, applicationDirectoryReadOnly: true };
    report.checks.platformDefaultDataOutsideReadonlyApplication = true;
  } finally {
    if (child.exitCode === null && child.signalCode === null) terminateOwnedApplication(child.pid);
    if (backend && alive(backend.pid)) process.kill(backend.pid);
    for (const [file, mode] of modes) fs.chmodSync(file, mode);
    fs.rmSync(data, { recursive: true, force: true });
  }
}
function terminateOwnedApplication(pid) {
  const owned = children(pid).filter(p => p.command.includes("cutvoke-engine"));
  for (const child of owned) if (alive(child.pid)) process.kill(child.pid, "SIGKILL");
  if (alive(pid)) process.kill(pid, "SIGKILL");
}
async function verifyCloseDuringStartup({ executable, env, out, report }) {
  const hook = path.join(out, "close-during-loading.cjs");
  for (const delay of [0, 150]) {
    const data = path.join(out, `启动中关闭 数据 ${delay}`);
    fs.writeFileSync(hook, `module.exports = async () => new Promise(() => {});\nmodule.exports.loading = async ({ window }) => { ${delay ? `setTimeout(() => window.close(), ${delay});` : 'await new Promise(resolve => { window.once("close", resolve); window.close(); });'} };\n`);
    const child = start(executable, { ...env, CUTVOKE_HOME: data, CUTVOKE_DESKTOP_QA: "1",
      CUTVOKE_DESKTOP_QA_SHOW: "1", CUTVOKE_DESKTOP_QA_SCRIPT: hook }, out, `close-during-startup-${delay}`);
    try {
      await waitFor(() => { if (child.launchError) throw child.launchError; return child.exitCode !== null; }, 30_000);
      assert.equal(child.exitCode, 0);
      assert.equal(fs.existsSync(path.join(data, "profile/logs/engine.log")), delay > 0);
      const processes = execFileSync("/bin/ps", ["-axo", "command="], { encoding: "utf8" });
      assert.equal(processes.split("\n").some(line => line.includes("cutvoke-engine") && line.includes(data)), false);
    } finally {
      if (child.exitCode === null && child.signalCode === null) terminateOwnedApplication(child.pid);
      fs.rmSync(data, { recursive: true, force: true });
    }
  }
  report.checks.closeDuringLoadingDoesNotStartOrLeakBackend = true;
}
module.exports = { verifyNormalLaunch, verifyReadonlyStorage, verifyPlatformStorage, verifyCloseDuringStartup, terminateOwnedApplication };
