const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { storagePlan, prepareStorage } = require("./storage.cjs");

test("macOS never writes inside an app bundle and Linux honors its XDG data root", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "CutVoke native "));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const bin = path.join(directory, "CutVoke.app/Contents/MacOS");
  fs.mkdirSync(bin, { recursive: true });
  fs.writeFileSync(path.join(bin, "portable.json"), "{}");
  const options = { packaged: true, executable: path.join(bin, "CutVoke"),
    appData: path.join(directory, "Library/Application Support"), home: directory, env: {} };
  const mac = storagePlan({ ...options, platform: "darwin" });
  assert.equal(mac.portable, false);
  assert.equal(mac.base, path.join(options.appData, "CutVoke"));
  prepareStorage(mac);
  assert.equal(fs.existsSync(path.join(bin, "CutVoke Data")), false);
  const linux = storagePlan({ ...options, executable: path.join(directory, "cutvoke"), platform: "linux",
    env: { XDG_DATA_HOME: path.join(directory, "XDG 数据") } });
  assert.equal(linux.base, path.join(directory, "XDG 数据/CutVoke"));
  prepareStorage(linux);
  assert.equal(linux.dataPath, path.join(linux.base, "data/projects.sqlite"));
});

test("portable storage preserves existing data, supports Unicode/spaces and fails without fallback", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "CutVoke 便携 "));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.writeFileSync(path.join(directory, "portable.json"), "{}");
  const options = { packaged: true, platform: "win32", executable: path.join(directory, "CutVoke.exe"), appData: directory, home: directory, env: {} };
  const plan = storagePlan(options);
  assert.ok(plan.portable);
  assert.equal(plan.dataPath, path.join(directory, "CutVoke Data", "data", "projects.sqlite"));
  prepareStorage(plan);
  const existing = Buffer.from("an existing project database must never be rewritten by startup");
  fs.writeFileSync(plan.dataPath, existing);
  prepareStorage(storagePlan(options));
  assert.deepEqual(fs.readFileSync(plan.dataPath), existing);
  const blocked = path.join(directory, "blocked");
  fs.writeFileSync(blocked, "an existing file");
  assert.throws(() => prepareStorage(storagePlan({ ...options, env: { CUTVOKE_PORTABLE_HOME: blocked } })));
  assert.deepEqual(fs.readFileSync(plan.dataPath), existing);
  assert.equal(storagePlan({ ...options, env: { CUTVOKE_PORTABLE_HOME: path.join(directory, "Custom") } }).profile,
    path.join(directory, "Custom", "profile"));
});
