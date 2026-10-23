const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { readSettings, writeSettings, validateDestination, validateDirectory } = require("./settings.cjs");

test("setup preferences survive reopen, malformed preferences fail visibly", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cutvoke-settings-"));
  t.after(() => fs.rmSync(root, { recursive: true }));
  assert.equal(readSettings(root).setupComplete, false);
  writeSettings(root, { setupComplete: true, dataPath: path.join(root, "工程 库", "projects.sqlite") });
  assert.equal(readSettings(root).setupComplete, true);
  assert.match(readSettings(root).dataPath, /工程 库/);
  fs.writeFileSync(path.join(root, "settings.json"), "{}");
  assert.throws(() => readSettings(root), /Invalid/);
});

test("relocation rejects occupied targets, nested folders and path aliases", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cutvoke-settings-"));
  t.after(() => fs.rmSync(root, { recursive: true }));
  const source = path.join(root, "source");
  fs.mkdirSync(source);
  const db = path.join(source, "projects.sqlite");
  fs.writeFileSync(db, "original");
  assert.throws(() => validateDirectory("relative"), /absolute/);
  assert.throws(() => validateDestination(db, source), /separate/);
  assert.throws(() => validateDestination(db, path.join(source, "child")), /separate/);
  assert.throws(() => validateDestination(db, root), /separate/);
  const occupied = path.join(root, "occupied");
  fs.mkdirSync(occupied); fs.writeFileSync(path.join(occupied, "keep"), "existing");
  assert.throws(() => validateDestination(db, occupied), /empty/);
  assert.equal(validateDestination(db, path.join(root, "新 工程")), path.join(root, "新 工程"));
  assert.equal(fs.readFileSync(db, "utf8"), "original");
});

test("GPU defaults on for new and legacy profiles and preserves an explicit off preference", t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "cutvoke-gpu-settings-"));
  t.after(() => fs.rmSync(root, { recursive: true }));
  assert.equal(readSettings(root).gpuAcceleration, true);
  writeSettings(root, { setupComplete: true });
  assert.equal(readSettings(root).gpuAcceleration, true);
  writeSettings(root, { setupComplete: true, gpuAcceleration: false });
  assert.equal(readSettings(root).gpuAcceleration, false);
  writeSettings(root, { setupComplete: true, gpuAcceleration: true });
  assert.equal(readSettings(root).gpuAcceleration, true);
  writeSettings(root, { setupComplete: true, gpuAcceleration: "false" });
  assert.throws(() => readSettings(root), /Invalid/);
});
