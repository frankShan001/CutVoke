const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function readSettings(profile) {
  const file = path.join(profile, "settings.json");
  if (!fs.existsSync(file)) return { setupComplete: false };
  const value = JSON.parse(fs.readFileSync(file, "utf8"));
  if (value.version !== 1 || typeof value.setupComplete !== "boolean" ||
      (value.dataPath !== undefined && (typeof value.dataPath !== "string" || !path.isAbsolute(value.dataPath)))) {
    throw new Error("Invalid CutVoke settings.json");
  }
  return value;
}

function writeSettings(profile, value) {
  fs.mkdirSync(profile, { recursive: true });
  const temporary = path.join(profile, `settings-${crypto.randomUUID()}.tmp`);
  try {
    fs.writeFileSync(temporary, JSON.stringify({ ...value, version: 1 }, null, 2), { flag: "wx" });
    fs.renameSync(temporary, path.join(profile, "settings.json"));
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function validateDirectory(value) {
  if (typeof value !== "string" || !value.trim() || !path.isAbsolute(value.trim())) {
    throw new Error("Please choose an absolute folder path. / 请选择完整的文件夹路径。");
  }
  return path.resolve(value.trim());
}

function validateDestination(sourceFile, directory) {
  const destination = validateDirectory(directory);
  const source = fs.realpathSync(path.dirname(sourceFile));
  // Resolve the nearest existing ancestor to reject junction/symlink aliases too.
  let ancestor = destination;
  const suffix = [];
  while (!fs.existsSync(ancestor)) { suffix.unshift(path.basename(ancestor)); ancestor = path.dirname(ancestor); }
  const resolved = path.join(fs.realpathSync(ancestor), ...suffix);
  const inside = (a, b) => { const rel = path.relative(a, b); return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel)); };
  if (inside(source, resolved) || inside(resolved, source)) {
    throw new Error("Choose a separate empty folder. / 请选择与原目录分开的空文件夹。");
  }
  if (fs.existsSync(destination) && (!fs.statSync(destination).isDirectory() || fs.readdirSync(destination).length)) {
    throw new Error("The destination must be empty. / 目标文件夹必须为空，已有文件不会被覆盖。");
  }
  return destination;
}

module.exports = { readSettings, writeSettings, validateDirectory, validateDestination };
