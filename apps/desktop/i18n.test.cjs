const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { locales, catalogs, matchLocale, createLanguageController, cacheDialogOptions, errorDialogOptions } = require("./i18n.cjs");
const placeholders = text => [...text.matchAll(/\{\d+\}/g)].map(match => match[0]).sort();
function cleanup(directory) {
  const target = path.resolve(directory), temporaryRoot = path.resolve(os.tmpdir());
  assert.equal(path.dirname(target), temporaryRoot);
  assert.ok(path.basename(target).startsWith("cutvoke-shell-"));
  fs.rmSync(target, { recursive: true, force: true });
}

test("all shell locales cover every message and preserve placeholders", () => {
  for (const locale of locales) {
    assert.deepEqual(Object.keys(catalogs[locale]).sort(), Object.keys(catalogs.en).sort(), locale);
    for (const [key, value] of Object.entries(catalogs[locale])) {
      assert.ok(typeof value === "string" && value.trim(), `${locale}: ${key}`);
      assert.deepEqual(placeholders(value), placeholders(catalogs.en[key]), `${locale}: ${key}`);
      if (locale !== "en") assert.notEqual(value, catalogs.en[key], `${locale}: English copy of ${key}`);
    }
  }
});
test("shell matches regional and unsupported system languages", () => {
  assert.equal(matchLocale("zh-Hant-HK"), "zh-TW");
  assert.equal(matchLocale("pt-PT"), "pt-BR");
  assert.equal(matchLocale("de-AT"), "de");
  assert.equal(matchLocale("../../ar"), undefined);
});
test("desktop preference survives a new origin and process; system follows current languages", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "cutvoke-shell-i18n-"));
  t.after(() => cleanup(directory));
  const storage = path.join(directory, "ui-language.json");
  let system = ["it-IT", "de-DE"];
  const first = createLanguageController(storage, () => system);
  assert.equal(first.snapshot().locale, "de");
  first.setPreference("ar");
  assert.deepEqual(JSON.parse(fs.readFileSync(storage, "utf8")), { preference: "ar" });
  const reopened = createLanguageController(storage, () => system);
  assert.equal(reopened.snapshot().locale, "ar");
  reopened.setPreference("system");
  system = ["ja-JP"];
  assert.equal(reopened.snapshot().locale, "ja");
  assert.throws(() => reopened.setPreference("../en"));
  assert.equal(JSON.parse(fs.readFileSync(storage, "utf8")).preference, "system");
});
test("invalid stored preferences use the system without changing project data", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "cutvoke-shell-invalid-"));
  t.after(() => cleanup(directory));
  const storage = path.join(directory, "ui-language.json");
  fs.writeFileSync(storage, '{"preference":"invalid"}');
  assert.equal(createLanguageController(storage, () => ["ko-KR"]).snapshot().locale, "ko");
  fs.writeFileSync(storage, "broken JSON");
  assert.equal(createLanguageController(storage, () => ["xx"]).snapshot().locale, "en");
});
test("native dialogs localize titles, action buttons and summaries while retaining raw diagnostics", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "cutvoke-shell-dialogs-"));
  t.after(() => cleanup(directory));
  const language = createLanguageController(path.join(directory, "ui-language.json"), () => ["en"]);
  for (const locale of locales) {
    language.setPreference(locale);
    const cache = cacheDialogOptions(language);
    assert.equal(cache.title, catalogs[locale].chooseCache);
    assert.equal(cache.buttonLabel, catalogs[locale].chooseFolder);
    assert.deepEqual(cache.properties, ["openDirectory", "createDirectory"]);
    for (const kind of ["startup", "service"]) {
      const error = errorDialogOptions(language, kind, "External error: LIB_TEST (42) / 原文");
      assert.equal(error.buttons[0], catalogs[locale].close);
      assert.equal(error.message, catalogs[locale][kind === "startup" ? "startupSummary" : "serviceSummary"]);
      assert.ok(error.detail.endsWith("External error: LIB_TEST (42) / 原文"));
    }
  }
});
test("packaging includes shell catalogs, loading script and controller", () => {
  const { build } = require("./package.json");
  for (const file of ["i18n.cjs", "loading.js", "locales/*.json"]) assert.ok(build.files.includes(file), file);
  for (const locale of locales) assert.ok(fs.existsSync(path.join(__dirname, "locales", `${locale}.json`)));
});
