/** Capture the real editor in both README languages from an isolated demo.
 * Build web/app and run the documented demo/server first. No UI is rewritten.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, isAbsolute, relative, resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const { values } = parseArgs({ options: {
  data: { type: "string" }, url: { type: "string", default: "http://127.0.0.1:8798" },
  browser: { type: "string" },
} });
assert(values.data, "Pass --data with the new demo's data directory.");
const data = resolve(values.data);
const withinArtifacts = relative(resolve(repo, ".artifacts"), data);
assert(withinArtifacts && !withinArtifacts.startsWith("..") && !isAbsolute(withinArtifacts),
  "Only an isolated demo under this checkout's .artifacts is accepted.");
const base = new URL(values.url);
assert(base.protocol === "http:" && ["127.0.0.1", "localhost", "[::1]"].includes(base.hostname),
  "Use the demo's loopback server.");
const require = createRequire(resolve(repo, "web/app/package.json"));
const { chromium, expect } = require("@playwright/test");
const assetRoot = resolve(repo, "docs/assets/readme");
const sourceId = "trail-notes";
const effectId = "cutvoke.transition.community.soft-reveal";
const locales = [
  { code: "en", id: "readme-en", name: "Trail Notes · English demo", open: "Open project",
    caption: "Leave the noise behind.", transition: "Soft Reveal", effective: "0.60s effective duration" },
  { code: "zh-CN", id: "readme-zh-cn", name: "山野手记 · 简体中文演示", open: "打开工程",
    caption: "暂别喧嚣。", transition: "柔和揭幕", effective: "0.60s 实际生效" },
];
async function api(route, body) {
  const response = await fetch(new URL(`/api/v1/${route}`, base), {
    ...(body === undefined ? {} : { method: "POST", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" } }), signal: AbortSignal.timeout(30_000),
  });
  const value = await response.json();
  assert(response.ok && value.ok !== false && !value.error, JSON.stringify(value));
  return value;
}
const runtime = await api("runtime");
assert.equal(runtime.application, "cutvoke");
assert.equal(resolve(runtime.dataPath), resolve(data, "projects.sqlite"), "Server uses a different database.");
const projects = await api("agent/project_list", {});
assert.equal(projects.projects.length, projects.total, "Refusing a partially listed project library.");
assert(projects.projects.every(p => [sourceId, ...locales.map(l => l.id)].includes(p.id)),
  "Refusing to use a library containing other projects.");
const source = await api(`projects/${sourceId}`);
assert.equal(source.name, "Trail Notes · editable demo");
assert.equal(source.sequence.captions.length, 3);
assert.equal(source.sequence.tracks[0].clips.length, 3);
assert.equal(source.sequence.tracks[0].clips[1].effects[0].effectId, effectId);
for (const command of ["edit.batch", "effect.update", "caption.update"]) {
  await api("agent/editor_help", { topic: "command", command });
}
for (const locale of locales) {
  if (projects.projects.some(p => p.id === locale.id)) {
    assert.equal((await api(`projects/${locale.id}`)).name, locale.name);
  } else {
    await api("agent/project_clone", { projectId: sourceId, expectedRevision: source.revision,
      newProjectId: locale.id, name: locale.name });
  }
  if (locale.code !== "zh-CN") continue;
  const project = await api(`projects/${locale.id}`);
  if (project.sequence.captions[0].text === locale.caption) continue;
  assert.equal(project.revision, "0", "Refusing to overwrite an edited demo clone.");
  const lease = await api(`projects/${locale.id}/edit-lock`, { owner: "readme-capture" });
  try {
    const commands = [
      ...[["headline", "山野手记"], ["subhead", "慢下来，过个周末"]].map(([clipId, content]) => ({
        type: "effect.update", payload: { clipId, effectId: "cutvoke.text", params: { content } },
      })),
      ...["暂别喧嚣。", "跟随舒缓的节奏。", "把片刻宁静带回家。"].map((text, index) => ({
        type: "caption.update", payload: { captionId: `caption-${index + 1}`, text },
      })),
    ];
    const args = { type: "edit.batch", payload: { commands }, expectedRevision: project.revision,
      editLeaseId: lease.lease.leaseId, actor: { kind: "agent", id: "readme-capture" },
      commandId: "localize-readme-demo" };
    await api(`projects/${locale.id}/commands`, { ...args, dryRun: true });
    await api(`projects/${locale.id}/commands`, args);
  } finally {
    await api(`projects/${locale.id}/edit-lock/release`, { leaseId: lease.lease.leaseId });
  }
}

const browser = await chromium.launch({
  ...(values.browser ? { executablePath: values.browser } : {}),
  args: ["--disable-dev-shm-usage"],
});
const screenshots = [];
const errors = [];
try {
  for (const locale of locales) {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1160 },
      deviceScaleFactor: 1, locale: "en-US" });
    const page = await context.newPage();
    page.setDefaultTimeout(20_000);
    page.on("pageerror", error => errors.push(error.message));
    const before = await api(`projects/${locale.id}`);
    await page.goto(base.href);
    // Both captures explicitly use the normal selector, independently of browser language.
    await page.getByTestId("language-select").selectOption(locale.code);
    await expect(page.locator("html")).toHaveAttribute("lang", locale.code);
    await page.getByRole("button", { name: `${locale.open} ${locale.name}`, exact: true }).click();
    await page.locator(".timeline").waitFor({ timeout: 180_000 });
    await page.locator(".project-loading").waitFor({ state: "hidden", timeout: 180_000 });
    await expect(page.locator(".media-item__thumb")).toHaveCount(3);
    await page.waitForFunction(() => [...document.querySelectorAll(".media-item__thumb")]
      .every(img => img.complete && img.naturalWidth > 0));
    // Use the real accessible divider and ruler. Do not inject styles or application state.
    const divider = await page.locator(".tl-resizer").boundingBox();
    assert(divider);
    await page.mouse.move(divider.x + divider.width / 2, divider.y + divider.height / 2);
    await page.mouse.down();
    await page.mouse.move(divider.x + divider.width / 2, divider.y - 245, { steps: 12 });
    await page.mouse.up();
    await expect(page.locator(".caption-timeline-clip").first()).toBeInViewport();
    await page.locator(".timeline-ruler").press("Home");
    for (let i = 0; i < 3; i++) await page.locator(".timeline-ruler").press("PageUp");

    async function capture(scene) {
      await page.waitForFunction(() => {
        const video = document.querySelector(".player video");
        return video && video.readyState >= 2 && !video.seeking && !video.error;
      });
      await page.evaluate(() => document.fonts.ready);
      await page.mouse.move(1590, 1150);
      await expect(page.getByTestId("language-select")).toHaveValue(locale.code);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const file = `${locale.code}/${scene}.png`;
      await mkdir(resolve(assetRoot, locale.code), { recursive: true });
      const pixels = await page.screenshot({ path: resolve(assetRoot, file) });
      screenshots.push({ file, locale: locale.code, scene, projectName: locale.name,
        bytes: pixels.length, width: pixels.readUInt32BE(16), height: pixels.readUInt32BE(20),
        sha256: createHash("sha256").update(pixels).digest("hex"),
        playheadSeconds: Number(await page.locator(".timeline-ruler").getAttribute("aria-valuenow")) });
      console.log(`Captured ${file} (${pixels.length} bytes)`);
    }
    await expect(page.locator(".caption-overlay__text")).toHaveText(locale.caption);
    await capture("editor-overview");
    await page.locator("#creative-domain-caption").click();
    await page.locator(".caption-timeline-clip").first().click();
    await expect(page.locator(".cv-style-edit textarea")).toHaveValue(locale.caption);
    await expect(page.locator(".caption-overlay__text")).toHaveText(locale.caption);
    await page.locator(".zone-left__scroll").hover();
    await page.mouse.wheel(0, 600);
    await expect(page.locator(".cv-style-edit input[type=number]").nth(1)).toBeInViewport();
    await capture("caption-editing");
    await page.locator("#creative-domain-media").click();
    await page.locator(".zone-left__scroll").hover();
    await page.mouse.wheel(0, -1800);
    await page.locator(".transition-marker__select").first().click();
    await expect(page.locator(".transition-marker--selected")).toContainText(locale.transition);
    await expect(page.getByText(locale.effective, { exact: true })).toBeVisible();
    await capture("community-transition");
    assert.deepEqual(await api(`projects/${locale.id}`), before, "UI capture modified project data.");
    await context.close();
  }
  assert.deepEqual(errors, [], "Browser page errors occurred.");
  assert.deepEqual(await api(`projects/${sourceId}`), source, "Source demo changed.");
  const report = { repositoryRevision: execFileSync("git", ["rev-parse", "HEAD"], { cwd: repo, encoding: "utf8" }).trim(),
    browser: browser.version(), viewport: { width: 1600, height: 1160, deviceScaleFactor: 1 },
    interfaceSelection: "Normal language selector: en / zh-CN; html lang and selected value checked",
    projectDataUnchangedDuringCapture: true, originalDemoUnchanged: true, pageErrors: errors, screenshots };
  await writeFile(resolve(data, "../screenshot-capture.json"), JSON.stringify(report, null, 2) + "\n");
  console.log("Inspect all six PNGs visually before publishing; capture metadata is beside the demo data directory.");
} finally {
  await browser.close();
}
