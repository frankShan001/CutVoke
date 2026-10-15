const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { storagePlan, prepareStorage } = require("./storage.cjs");

test("portable storage preserves existing data, supports Unicode/spaces and fails without fallback", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "CutVoke 便携 "));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  fs.writeFileSync(path.join(directory, "portable.json"), "{}");
  const options = { packaged: true, executable: path.join(directory, "CutVoke.exe"), appData: directory, home: directory, env: {} };
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
