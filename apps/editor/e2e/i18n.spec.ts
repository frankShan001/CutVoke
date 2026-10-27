import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";

const image = fileURLToPath(new URL("../../../src/cutvoke/assets/stickers/decor_sparkles.png", import.meta.url));
const evidence = resolve(process.cwd(), "../../.artifacts/acceptance/i18n");
const api = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;
const locales = ["zh-CN", "zh-TW", "en", "ja", "ko", "es", "fr", "de", "pt-BR", "ru", "ar"];
async function current(request: APIRequestContext, id: string) {
  const response = await request.get(api(id));
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function fixture(request: APIRequestContext) {
  const id = `i18n-${crypto.randomUUID()}`;
  const name = `刷新 / Export / مشروع ${id}`;
  expect((await request.post("/api/v1/projects", { data: { projectId: id, name, width: 320, height: 180, fps: 30 } })).status()).toBe(201);
  async function command(type: string, payload: object) {
    const project = await current(request, id);
    const response = await request.post(`${api(id)}/commands`, { data: {
      type, payload, expectedRevision: project.revision, commandId: crypto.randomUUID(), actor: { kind: "human", id: "i18n-test" },
    } });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  const upload = await request.post("/api/v1/assets?name=" + encodeURIComponent("界面语言-مشهد.png"), {
    headers: { "Content-Type": "application/octet-stream" }, data: readFileSync(image),
  });
  expect(upload.ok()).toBeTruthy();
  const asset = await upload.json();
  await command("track.add", { trackId: "video", kind: "video" });
  await command("clip.insert", { trackId: "video", clipId: "i18n-clip", sourcePath: asset.path, assetId: asset.assetId,
    timelineStart: { num: 0, den: 1 }, timelineEnd: { num: 5, den: 1 } });
  await command("caption.add", { captionId: "i18n-caption", text: "关闭 / Export / العربية", start: { num: 0, den: 1 }, end: { num: 4, den: 1 } });
  return { id, name, asset };
}
async function switchTo(page: Page, locale: string) {
  await page.getByTestId("language-select").selectOption(locale);
  await expect(page.locator("html")).toHaveAttribute("lang", locale);
  await expect(page.locator("html")).toHaveAttribute("dir", locale === "ar" ? "rtl" : "ltr");
}
async function noPageOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  const overflow = await page.locator(".topbar,.topbar > *,.home__create-row,.cv-panel,.right-tabs,.resource-categories,.ctx-menu,.export-dialog__panel").evaluateAll(nodes =>
    nodes.filter(node => { const box = node.getBoundingClientRect(); return box.width && (box.left < -1 || box.right > innerWidth + 1); }).map(node => node.className));
  expect(overflow).toEqual([]);
  const overlaps = await page.locator(".topbar").evaluateAll(nodes => nodes.flatMap(node => {
    const children = [...node.children].map(child => ({ name: child.className, box: child.getBoundingClientRect() })).filter(item => item.box.width && item.box.height);
    return children.flatMap((a, index) => children.slice(index + 1).filter(b =>
      Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left) > 1 &&
      Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top) > 1).map(b => `${a.name}/${b.name}`));
  }));
  expect(overlaps).toEqual([]);
}

test.describe("system language", () => {
  test.use({ locale: "de-DE" });
  test("matches navigator languages and persists only explicit preference", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("lang", "de");
    await expect(page.getByTestId("language-select")).toHaveValue("system");
    await switchTo(page, "en");
    await expect(page.getByRole("button", { name: "Create project", exact: true })).toBeVisible();
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("lang", "en");
    await expect(page.getByTestId("language-select")).toHaveValue("en");
    await page.getByTestId("language-select").selectOption("system");
    await expect(page.locator("html")).toHaveAttribute("lang", "de");
  });
});

test("all languages switch without changing project, captions or media", async ({ page, request }) => {
  test.setTimeout(120_000);
  const item = await fixture(request);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${item.name}`, exact: true }).click();
  await expect(page.locator(".timeline")).toBeVisible({ timeout: 75_000 });
  const before = await current(request, item.id);
  for (const locale of [...locales, "en", "zh-CN", "ar", "de", "en"]) {
    await switchTo(page, locale);
    await expect(page.locator(".statusbar__project b")).toHaveText(item.name);
    await expect(page.locator(".caption-timeline-clip").first()).toContainText("关闭 / Export / العربية");
    await noPageOverflow(page);
  }
  expect(await current(request, item.id)).toEqual(before);
  expect(await page.evaluate(() => localStorage.getItem("cutvoke.ui.language"))).toBe("en");
  expect(errors).toEqual([]);
});

for (const locale of ["zh-CN", "en", "de", "ar"]) {
  test(`${locale}: menus, dialog, validation, drafts and layout`, async ({ page, request }) => {
    test.setTimeout(120_000);
    const item = await fixture(request);
    await page.goto("/");
    await page.getByRole("button", { name: `打开工程 ${item.name}`, exact: true }).click();
    await expect(page.locator(".timeline")).toBeVisible({ timeout: 75_000 });
    await switchTo(page, locale);
    const before = await current(request, item.id);
    await page.locator(".clip-block").first().click({ button: "right" });
    await expect(page.locator(".ctx-menu")).toBeVisible();
    await noPageOverflow(page);
    await page.keyboard.press("Escape");
    await expect(page.locator(".ctx-menu,.ctx-menu__backdrop")).toHaveCount(0);
    await expect(page.locator(".clip-block--selected")).toHaveCount(1);
    await page.locator(".tool-btn--export").click();
    const dialog = page.locator(".export-dialog");
    await expect(dialog).toBeVisible();
    await noPageOverflow(page);
    mkdirSync(evidence, { recursive: true });
    await page.screenshot({ path: resolve(evidence, `${locale}-export.png`) });
    await page.locator(".export-dialog__close").click();
    await page.locator("#creative-domain-caption").click();
    await page.locator(".caption-timeline-clip").first().click();
    const text = page.locator(".cv-style-edit textarea");
    await expect(text).toHaveCount(1);
    await text.fill("保存草稿 / keep draft / مسودة");
    await switchTo(page, locale === "en" ? "ar" : "en");
    await expect(text).toHaveValue("保存草稿 / keep draft / مسودة");
    await switchTo(page, locale);
    // Valid edits now auto-save after the typing gesture settles. Language
    // changes must preserve the exact edit and leave all other content intact.
    const saved = structuredClone(before);
    saved.revision = String(BigInt(before.revision) + 1n);
    for (const sequence of [saved.sequence, ...saved.sequences])
      sequence.captions.find((caption: { id: string }) => caption.id === "i18n-caption").text = "保存草稿 / keep draft / مسودة";
    await expect.poll(() => current(request, item.id)).toEqual(saved);
    await page.screenshot({ path: resolve(evidence, `${locale}-editor.png`) });
    // Numeric/media geometry stays LTR while side panels mirror for Arabic.
    expect(await page.locator(".timeline").evaluate(node => getComputedStyle(node).direction)).toBe("ltr");
    if (locale === "ar") {
      const left = await page.locator(".zone-left").boundingBox();
      const right = await page.locator(".zone-right").boundingBox();
      expect(left!.x).toBeGreaterThan(right!.x);
      await page.locator("#creative-domain-media").click();
      await page.locator("#creative-domain-media").press("ArrowLeft");
      await expect(page.locator("#creative-domain-music")).toHaveAttribute("aria-selected", "true");
    }
    // Check a real rejected operation, with an empty path, in this locale.
    await page.locator(".tool-btn--export").click();
    await page.locator(".export-dialog__actions .cv-btn--primary").first().click();
    const error = page.getByRole("alertdialog");
    await expect(error).toBeVisible();
    const headings: Record<string, string> = { "zh-CN": "操作未完成", en: "Operation incomplete", de: "Vorgang nicht abgeschlossen", ar: "لم تكتمل العملية" };
    await expect(error).toHaveAccessibleName(headings[locale]);
    expect(await current(request, item.id)).toEqual(saved);
    await page.screenshot({ path: resolve(evidence, `${locale}-validation.png`) });
  });
}

test("long translations fit a narrow home screen", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.goto("/");
  await switchTo(page, "de");
  await noPageOverflow(page);
  await page.getByTestId("language-select").selectOption("ar");
  await noPageOverflow(page);
  await page.setViewportSize({ width: 600, height: 900 });
  await noPageOverflow(page);
});

test("German editor shell keeps toolbar controls inside the viewport when revealing status", async ({ page, request }) => {
  const item = await fixture(request);
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${item.name}`, exact: true }).click();
  await expect(page.locator(".timeline")).toBeVisible({ timeout: 75_000 });
  await switchTo(page, "de");
  for (const width of [1600, 1500, 1400, 1280, 1024, 760]) {
    await page.setViewportSize({ width, height: 960 });
    // Native reveal/focus scrolling must stay within intentional scroll areas.
    await page.locator(".topbar__sync").evaluate(node => node.scrollIntoView({ block: "nearest", inline: "nearest" }));
    await expect(page.locator(".app-shell")).toHaveJSProperty("scrollLeft", 0);
    await noPageOverflow(page);
    for (const name of ["Exportieren", "Einstellungen", "Aktualisieren"])
      await expect(page.getByRole("button", { name, exact: true })).toBeInViewport({ ratio: 1 });
  }
});

test("cross-tab language changes preserve an open dialog and its draft", async ({ page, request }) => {
  const item = await fixture(request);
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${item.name}`, exact: true }).click();
  await expect(page.locator(".timeline")).toBeVisible({ timeout: 75_000 });
  const before = await current(request, item.id);
  await switchTo(page, "en");
  await page.locator(".tool-btn--export").click();
  await page.getByLabel("Output file path", { exact: true }).fill("keep-this-name-ملف.mp4");
  const other = await page.context().newPage();
  await other.goto("/");
  await switchTo(other, "de");
  await expect(page.getByRole("dialog", { name: "Video exportieren", exact: true })).toBeVisible();
  await expect(page.locator(".export-dialog input").first()).toHaveValue("keep-this-name-ملف.mp4");
  await switchTo(other, "ar");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("dialog", { name: "تصدير الفيديو", exact: true })).toBeVisible();
  await noPageOverflow(page);
  await page.locator(".export-dialog__close").click();
  await page.setViewportSize({ width: 1200, height: 900 });
  await noPageOverflow(page);
  await switchTo(other, "de");
  await expect(page.locator("html")).toHaveAttribute("lang", "de");
  await noPageOverflow(page);
  expect(await current(request, item.id)).toEqual(before);
  await other.close();
});

test("effect metadata requests follow the locale and keep registry IDs stable", async ({ page, request }) => {
  const item = await fixture(request);
  const requests = new Set<string>();
  page.on("request", req => {
    const url = new URL(req.url());
    if (url.pathname.endsWith("/effects") || url.pathname.endsWith("/resources")) requests.add(`${url.pathname.endsWith("/effects") ? "effects" : "resources"}:${url.searchParams.get("lang")}`);
  });
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${item.name}`, exact: true }).click();
  await expect(page.locator(".timeline")).toBeVisible({ timeout: 75_000 });
  const before = await current(request, item.id);
  await page.locator("#transition-tab").click();
  await page.locator("#creative-domain-transition").click();
  for (const locale of ["en", "ar", "zh-CN"]) {
    await switchTo(page, locale);
    await expect.poll(() => requests.has(`effects:${locale}`) && requests.has(`resources:${locale}`)).toBeTruthy();
    const response = await request.get(`/api/v1/effects?lang=${locale}`);
    const catalog = await response.json();
    const effect = catalog.effects.find((entry: { effectId: string }) => entry.effectId === "cutvoke.transition.crossfade");
    const card = page.locator(".resource-effect-library:visible .resource-card").filter({ has: page.getByText(effect.name, { exact: true }) });
    await expect(card).toBeVisible();
    await expect(card.locator(".resource-card__desc")).toHaveText(effect.description);
  }
  expect(await current(request, item.id)).toEqual(before);
});

test("owned progress follows locale while raw recognition diagnostics and project data stay unchanged", async ({ page, request }) => {
  const item = await fixture(request);
  let failed = false;
  await page.route("**/api/v1/asr/status", route => route.fulfill({ json: { ready: true, message: "首次识别会下载所选模型；也可设置 CUTVOKE_ASR_MODEL_DIR 指定模型缓存。" } }));
  const job = () => ({ jobId: "i18n-progress", projectId: item.id, clipId: "i18n-clip", model: "base", requestedLanguage: "auto",
    status: failed ? "failed" : "running", phase: failed ? "识别失败" : "GPU 初始化失败，正在回退到 CPU", progress: 8,
    error: failed ? "CUDA_ERROR_FIXTURE / 原始诊断" : "", result: null });
  await page.route("**/api/v1/projects/*/asr-jobs", route => route.fulfill({ status: 202, json: job() }));
  await page.route("**/api/v1/asr-jobs/i18n-progress", route => route.fulfill({ json: job() }));
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${item.name}`, exact: true }).click();
  await expect(page.locator(".timeline")).toBeVisible({ timeout: 75_000 });
  await page.locator(".clip-block").first().click();
  await page.locator("#creative-domain-caption").click();
  await switchTo(page, "de");
  const before = await current(request, item.id);
  await page.getByRole("button", { name: "Spracherkennung starten", exact: true }).click();
  await expect(page.locator(".cv-asr__job")).toContainText("GPU-Initialisierung fehlgeschlagen; Wechsel zur CPU");
  await switchTo(page, "ar");
  await expect(page.locator(".cv-asr__job")).toContainText("تعذرت تهيئة GPU");
  failed = true;
  await expect(page.locator(".cv-asr__error")).toContainText("لم يكتمل التعرف على الكلام");
  await expect(page.locator(".cv-asr__error")).toContainText("CUDA_ERROR_FIXTURE / 原始诊断");
  await switchTo(page, "de");
  await expect(page.locator(".cv-asr__error")).toContainText("Die Spracherkennung wurde nicht abgeschlossen");
  await expect(page.locator(".cv-asr__error")).toContainText("CUDA_ERROR_FIXTURE / 原始诊断");
  expect(await current(request, item.id)).toEqual(before);
});

test("preflight localizes owned summaries and exposes unchanged external diagnostics", async ({ page, request }) => {
  const item = await fixture(request);
  const raw = "EXTERNAL_FIXTURE: 原始诊断 42";
  await page.route("**/api/v1/projects/*/commands", route => {
    const command = route.request().postDataJSON();
    if (command.type !== "project.preflight") return route.fallback();
    return route.fulfill({ json: { commandId: command.commandId, previousRevision: command.expectedRevision,
      revision: command.expectedRevision, transactionId: "i18n-read-only", warnings: [], changedEntities: [{ type: "project_preflight", report: {
        projectId: item.id, revision: command.expectedRevision, readyToRender: false, errors: [{ code: "MISSING_FILE", message: raw,
          path: item.asset.path, resourceKind: "media", clipIds: ["i18n-clip"], trackIds: ["video"] }], warnings: [], files: [],
      } }] } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: `打开工程 ${item.name}`, exact: true }).click();
  await expect(page.locator(".timeline")).toBeVisible({ timeout: 75_000 });
  const before = await current(request, item.id);
  await switchTo(page, "de");
  await page.locator(".tool-btn--export").click();
  const dialog = page.locator(".export-dialog");
  await page.getByLabel("Pfad der Ausgabedatei", { exact: true }).fill("i18n-local.mp4");
  await page.locator(".export-dialog__actions .cv-btn--primary").first().click();
  await expect(dialog.getByText("Die Mediendatei ist nicht verfügbar. Verknüpfe sie in der Medienbibliothek erneut.", { exact: true })).toBeVisible();
  await dialog.getByText("Ursprüngliche Diagnose", { exact: true }).click();
  await expect(dialog.getByText(raw, { exact: true })).toBeVisible();
  await switchTo(page, "ar");
  await expect(dialog.getByText("ملف الوسائط غير متاح. أعد ربطه في مكتبة الوسائط.", { exact: true })).toBeVisible();
  await expect(dialog.getByText(raw, { exact: true })).toBeVisible();
  expect(await current(request, item.id)).toEqual(before);
});
