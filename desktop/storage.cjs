const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

function storagePlan({ packaged, executable, appData, home, env = process.env }) {
  const portable = packaged && fs.existsSync(path.join(path.dirname(executable), "portable.json"));
  const base = portable ? path.resolve(env.CUTVOKE_PORTABLE_HOME || path.join(path.dirname(executable), "CutVoke Data")) : null;
  const profile = env.CUTVOKE_DESKTOP_QA === "1" && env.CUTVOKE_DESKTOP_QA_HOME
    ? path.resolve(env.CUTVOKE_DESKTOP_QA_HOME) : base ? path.join(base, "profile") : path.join(appData, "CutVoke");
  const data = path.resolve(env.CUTVOKE_DATA || (base ? path.join(base, "data") : path.join(home, ".cutvoke", "data")));
  return { portable, base, profile, dataPath: /\.sqlite$/i.test(data) ? data : path.join(data, "projects.sqlite") };
}

function prepareStorage(plan, { profileOnly = false } = {}) {
  // Never switch to an empty database when the user's portable folder cannot
  // be written. Opening a newer build must preserve every existing project.
  for (const directory of new Set(profileOnly ? [plan.profile] : [plan.profile, path.dirname(plan.dataPath)])) {
    fs.mkdirSync(directory, { recursive: true });
    const probe = path.join(directory, `.write-check-${crypto.randomUUID()}`);
    fs.writeFileSync(probe, "", { flag: "wx" });
    fs.unlinkSync(probe);
  }
}

module.exports = { storagePlan, prepareStorage };
