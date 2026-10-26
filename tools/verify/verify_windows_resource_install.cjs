// Actual frozen backend, fresh databases, complete catalogue and byte hashes.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");

async function verifyWindowsResourceInstall({ root, out, executable, engine, electron, env }) {
  const packageRoot = path.join(path.dirname(engine), "_internal/cutvoke");
  const manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, "catalogs/builtin_resource_pack.json")));
  const pngs = manifest.files.filter(x => x.path.startsWith("assets/stickers/") && x.path.endsWith(".png"));
  const longest = pngs.reduce((a, b) => a.path.length > b.path.length ? a : b);
  const suffix = path.join("data/media/.builtin-resources", manifest.packId, manifest.version, longest.sha256, longest.path);
  const base = path.join(root, ".artifacts/p");
  function homeFor(maximum, marker) {
    const padding = maximum - suffix.length - 1 - base.length - 1 - marker.length;
    assert.ok(padding >= 0, "Runner checkout path must leave room for the controlled boundary fixture");
    return path.join(base, marker + "d".repeat(padding));
  }
  const boundaryHome = homeFor(231, "深路径_");
  const legacyHome = homeFor(231, "旧路径_");
  const output = path.join(out, "resource-path-capabilities.json");
  execFileSync(path.join(root, ".artifacts/venv-portable/Scripts/python.exe"),
    [path.join(root, "tools/verify/verify_windows_resource_paths.py"), "--engine", engine,
      "--package-root", packageRoot, "--legacy-home", legacyHome, "--output", output],
    { encoding: "utf8", timeout: 120_000 });
  const capabilities = JSON.parse(fs.readFileSync(output));
  assert.equal(capabilities.legacyStaging.atOrAbove260, 36);
  const results = [];
  for (const [label, home, required] of [["legacy-temporary-boundary", boundaryHome, true],
      ["final-path-over-260", homeFor(280, "超长路径_"), false]]) {
    const database = path.join(home, "data/projects.sqlite");
    assert.ok(!fs.existsSync(database), "The resource acceptance requires a genuinely fresh database");
    const app = await electron.launch({ executablePath: executable, cwd: out,
      env: { ...env, CUTVOKE_PORTABLE_HOME: home, CUTVOKE_DESKTOP_QA: "1", CUTVOKE_DESKTOP_QA_SHOW: "1" }, timeout: 120_000 });
    try {
      const page = await app.firstWindow();
      await page.getByTestId("language-select").waitFor({ timeout: 120_000 });
      const visible = await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].isVisible());
      assert.ok(visible);
      const origin = new URL(page.url()).origin;
      const get = async route => { const r = await fetch(origin + "/api/v1/" + route); assert.ok(r.ok); return r.json(); };
      const runtime = await get("runtime");
      assert.equal(path.normalize(runtime.dataPath), database);
      assert.equal(path.normalize(runtime.pythonExecutable), engine);
      const catalog = await get("stickers");
      const assets = new Map((await get("assets")).assets.map(a => [a.assetId, a]));
      fs.writeFileSync(path.join(out, "resource-install-current.json"), JSON.stringify({ label, home, runtime,
        catalog, assets: [...assets.values()] }, null, 2));
      assert.equal(catalog.stickers.length, 612);
      const unavailable = catalog.stickers.filter(s => !s.available || !s.previewAvailable);
      if (required || (capabilities.registryLongPathsEnabled === 1 && capabilities.frozenManifest.longPathAware))
        assert.deepEqual(unavailable, [], JSON.stringify(unavailable));
      for (const item of unavailable) {
        assert.ok(item.installationError?.destinationPathLength >= 260, JSON.stringify(item));
        assert.ok(item.installationError.stage && [3, 206].includes(item.installationError.winerror), JSON.stringify(item));
        assert.ok(item.availabilityMessage.includes("缩短应用数据目录"));
      }
      let verifiedImages = 0, verifiedPreviews = 0;
      for (const sticker of catalog.stickers) {
        assert.equal(sticker.previewAvailable, true);
        const resource = manifest.resources.find(r => r.resourceId === sticker.stickerId);
        const preview = manifest.files.find(f => resource.mediaFiles.includes(f.path) && f.path.startsWith("assets/sticker_previews/") && f.path.endsWith(".mp4"));
        assert.ok(preview);
        assert.equal(crypto.createHash("sha256").update(fs.readFileSync(path.join(packageRoot, preview.path))).digest("hex"), preview.sha256);
        verifiedPreviews++;
        if (sticker.kind === "static" && sticker.available) {
          const asset = assets.get(sticker.assetId);
          assert.ok(path.toNamespacedPath(asset.path).startsWith(path.toNamespacedPath(path.join(home, "data/media/.builtin-resources"))),
            JSON.stringify({ assetId: sticker.assetId, installedPath: asset.path, expectedRoot: path.join(home, "data/media/.builtin-resources") }));
          assert.equal(crypto.createHash("sha256").update(fs.readFileSync(asset.path)).digest("hex"), sticker.resourceRef.sha256);
          verifiedImages++;
        }
      }
      const lengths = [...assets.values()].filter(a => a.assetId.startsWith("builtin_sticker_")).map(a => a.path.length);
      const result = { label, home, freshDatabase: true, available: catalog.stickers.length - unavailable.length,
        previewAvailable: verifiedPreviews, verifiedImages, verifiedPreviews,
        installedMaxPathLength: Math.max(...lengths), unavailable };
      results.push(result);
      fs.writeFileSync(path.join(out, "resource-install-verification.json"), JSON.stringify({ capabilities, results }, null, 2));
      const closed = app.waitForEvent("close", { timeout: 30_000 });
      await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].close());
      await closed;
      await assert.rejects(fetch(origin + "/api/v1/runtime"));
    } finally { await app.close().catch(() => {}); }
  }
  return { capabilities, results };
}
module.exports = { verifyWindowsResourceInstall };
