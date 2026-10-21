import { test } from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
import { appRoot, checkCatalogs, localeRoot } from "./check-i18n.mjs";

function load(relative, globals = {}) {
  const file = join(appRoot, relative), module = { exports: {} };
  const { outputText } = ts.transpileModule(readFileSync(file, "utf8"), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true,
  } });
  vm.runInNewContext(outputText, { module, exports: module.exports, require: createRequire(file), ...globals }, { filename: file });
  return module.exports;
}
const messages = load("src/i18n/messages.ts");
const navigation = load("src/i18n/navigation.ts");

test("every locale covers source keys and preserves every placeholder", () => {
  assert.deepEqual(checkCatalogs().errors, []);
});
test("technical identifiers in interface instructions stay literal", () => {
  const english = JSON.parse(readFileSync(join(localeRoot, "en.json"), "utf8"));
  for (const [locale, catalog] of Object.entries(messages.catalogs)) for (const [source, text] of Object.entries(english)) {
    for (const token of text.match(/CUTVOKE_[A-Z_]+|cutvoke\[\w+\]|cutvoke-person-model|sourcePath|effectId|num\/den|history\.undo/g) ?? []) {
      assert.ok(catalog[source].includes(token), `${locale}: ${token} in ${source}`);
    }
  }
});
test("RTL arrows follow visual tab order without reversing time controls", () => {
  assert.equal(navigation.readingOrderKey("ArrowLeft", "ar"), "ArrowRight");
  assert.equal(navigation.readingOrderKey("ArrowDown", "ar"), "ArrowDown");
  assert.equal(navigation.readingOrderKey("ArrowRight", "de"), "ArrowRight");
});
test("fallback is selected language, English, then authored source", () => {
  assert.equal(messages.translate("测试 {0}", "ja", ["字幕"], { ja: {}, en: { "测试 {0}": "Test {0}" } }), "Test 字幕");
  assert.equal(messages.translate("测试 {0}", "ja", ["User text"], { ja: {}, en: {} }), "测试 User text");
  assert.equal(messages.translate("刷新", "zh-CN", [], { en: { "刷新": "Refresh" } }), "刷新");
  assert.equal(messages.translate("测试 {0}", "ar", ["刷新 / {1} / العربية"], { ar: { "测试 {0}": "اختبار {0}" } }), "اختبار 刷新 / {1} / العربية");
});
test("stored UI statuses follow language while raw user values remain unchanged", () => {
  const value = messages.msg("已重命名为 {0}", "刷新 / العربية / Export");
  assert.match(messages.formatMessage(value, "en"), /^Renamed to 刷新 \/ العربية \/ Export$/);
  assert.equal(messages.formatMessage(value, "zh-CN"), "已重命名为 刷新 / العربية / Export");
  assert.equal(messages.formatMessage("刷新", "en"), "刷新");
});
test("catalogs contain real localized main UI text", () => {
  const expected = { "zh-TW": "建立專案", ja: "プロジェクトを作成", ko: "프로젝트 만들기", es: "Crear proyecto", fr: "Créer un projet", de: "Projekt erstellen", "pt-BR": "Criar projeto", ru: "Создать проект", ar: "إنشاء مشروع" };
  for (const [locale, value] of Object.entries(expected)) assert.equal(messages.translate("创建工程", locale), value, locale);
  const en = JSON.parse(readFileSync(join(localeRoot, "en.json"), "utf8"));
  for (const locale of Object.keys(expected)) {
    const catalog = JSON.parse(readFileSync(join(localeRoot, `${locale}.json`), "utf8"));
    const different = Object.keys(en).filter(key => catalog[key] !== en[key]).length;
    assert.ok(different / Object.keys(en).length > 0.90, `${locale}: English placeholders dominate`);
  }
});
test("non-English catalogs do not copy complete English sentences, including mixed-language prefixes", () => {
  const words = text => (text.replace(/\{\d+\}/g, "").toLowerCase().match(/[a-z]+/g) ?? []);
  const functionWords = new Set(["the", "this", "to", "is", "can", "with", "are", "will", "before", "after", "you", "your"]);
  const english = JSON.parse(readFileSync(join(localeRoot, "en.json"), "utf8"));
  for (const [locale, catalog] of Object.entries(messages.catalogs)) {
    if (locale === "en" || locale === "zh-TW") continue;
    for (const [source, translation] of Object.entries(catalog)) {
      const original = words(english[source]);
      const translated = ` ${words(translation).join(" ")} `;
      for (let index = 0; index + 3 <= original.length; index++) {
        const phrase = original.slice(index, index + 3);
        if (!phrase.some(word => functionWords.has(word))) continue; // Preserve shared technical names.
        assert.ok(!translated.includes(` ${phrase.join(" ")} `), `${locale}: English clause copied into ${source}`);
      }
    }
  }
});
function browser(saved, languages = ["en-US"], storageThrows = false, desktop) {
  const data = new Map(saved ? [["cutvoke.ui.language", saved]] : []), events = {}, html = { dataset: {} };
  const storage = { getItem: key => { if (storageThrows) throw Error("denied"); return data.get(key) ?? null; }, setItem: (key, value) => { if (storageThrows) throw Error("denied"); data.set(key, value); } };
  const api = load("src/i18n/locale.ts", { localStorage: storage, navigator: { languages, language: languages[0] }, document: { documentElement: html }, window: { cutvokeDesktop: desktop, addEventListener: (key, fn) => { events[key] = fn; } } });
  return { api, data, events, html };
}
test("system matching handles regional tags, multiple preferences and fallback", () => {
  const { api } = browser();
  for (const [tag, expected] of Object.entries({ "zh-Hant": "zh-TW", "zh-HK": "zh-TW", "zh-MO": "zh-TW", "zh-SG": "zh-CN", "en-AU": "en", "pt-PT": "pt-BR", "ar-EG": "ar", "ja-JP": "ja", "en-US-u-hc-h23": "en", "ko_KR": "ko" })) assert.equal(api.matchLocale(tag), expected);
  assert.equal(api.detectLocale(["it-IT", "fr-CA", "en-US"]), "fr");
  assert.equal(api.detectLocale(["invalid", "xx-ZZ"]), "en");
  assert.equal(api.matchLocale("../../ar"), undefined);
});
test("preference persistence, storage events, RTL and storage denial", () => {
  const { api, data, events, html } = browser("bad", ["zh-Hant-HK"]);
  assert.equal(api.getLanguagePreference(), "system");
  assert.equal(api.getLocale(), "zh-TW");
  let changes = 0; const unsubscribe = api.subscribeLanguage(() => changes++);
  api.setLanguage("ar");
  assert.equal(data.get("cutvoke.ui.language"), "ar");
  assert.equal(html.dir, "rtl");
  assert.equal(html.lang, "ar");
  assert.equal(browser(data.get("cutvoke.ui.language")).api.getLocale(), "ar");
  data.set("cutvoke.ui.language", "de");events.storage({ key: "cutvoke.ui.language" });
  assert.equal(api.getLocale(), "de");assert.equal(html.dir, "ltr");
  unsubscribe();api.setLanguage("system");assert.equal(changes, 2);
  const denied = browser(null, ["ko-KR"], true);denied.api.setLanguage("fr");assert.equal(denied.api.getLocale(), "fr");
  assert.deepEqual([...data.keys()], ["cutvoke.ui.language"]);
});
test("desktop preference wins over a random-origin browser store and follows native system languages", () => {
  const writes = [];
  const desktop = { initialLanguage: { preference: "ar" }, getLanguageState: () => ({ systemLanguages: ["de-DE"] }),
    setLanguagePreference: async preference => { writes.push(preference); } };
  const { api, html } = browser("ja", ["en-US"], false, desktop);
  assert.equal(api.getLocale(), "ar");assert.equal(html.dir, "rtl");
  api.setLanguage("system");assert.equal(api.getLocale(), "de");assert.equal(writes.at(-1), "system");
  const shell = createRequire(import.meta.url)("../../../apps/desktop/i18n.cjs");
  for (const tag of ["zh-TW", "zh-Hant", "pt-PT", "de-AT", "ar-EG", "bad"]) assert.equal(shell.matchLocale(tag), api.matchLocale(tag));
});
function serviceAdapter(relative, locale) {
  return load(relative, { require: () => ({ tr: (source, ...values) => messages.translate(source, locale, values) }) });
}
test("all authored service progress phases are localized; unknown diagnostics and device IDs are preserved", () => {
  const sources = ["speech_recognition.py", "person_cutout.py"].map(name => readFileSync(join(appRoot, "../../src/cutvoke/rendering", name), "utf8")).join("\n");
  const phases = [...sources.matchAll(/(?:report|progress)\("([^"\n]+)"/g)].map(match => match[1]);
  assert.ok(phases.length >= 10);
  for (const locale of Object.keys(messages.catalogs)) {
    const { backendMessage } = serviceAdapter("src/i18n/backendMessages.ts", locale);
    const unknown = "Library XYZ: 42 / 原始诊断";
    assert.ok(backendMessage(unknown).includes(unknown));
    for (const phase of phases) assert.equal(backendMessage(phase), messages.translate(phase, locale), `${locale}: ${phase}`);
    assert.equal(backendMessage("SAM2 跨帧跟踪（cuda:0）"), messages.translate("SAM2 跨帧跟踪（{0}）", locale, ["cuda:0"]));
  }
});
test("preflight summaries use owned issue codes and preserve unknown raw diagnostics separately", () => {
  for (const locale of Object.keys(messages.catalogs)) {
    const { preflightSummary } = serviceAdapter("src/lib/preflight.ts", locale);
    assert.equal(preflightSummary({ code: "MISSING_FILE", resourceKind: "media" }), messages.translate("素材文件不可用，请在素材库中重新链接。", locale));
    assert.equal(preflightSummary({ code: "NOT_A_FILE", resourceKind: "lut" }), messages.translate("自定义 LUT 文件不可用，请重新导入 LUT。", locale));
    const issue = { code: "THIRD_PARTY_UNKNOWN", message: "External plugin: 原文 42" };
    assert.equal(preflightSummary(issue), messages.translate("工程检查有提醒，请查看原始诊断。", locale));
    assert.equal(issue.message, "External plugin: 原文 42");
  }
});
test("reviewed German actions and help do not retain obvious English fragments", () => {
  for (const [source, text] of Object.entries(messages.catalogs.de)) {
    assert.doesNotMatch(text, /(?<![\p{L}\p{N}_])(?:retry|unlock|matching|idle|loading|failed|preview|track|tracks|frame|click|reloading|timed|uncategorized|set|tools?|playhead|fit|split|hover|cancel|close|show|hide|open|move|build|safe|swaps|loop|templates?|media)(?![\p{L}\p{N}_])/iu, source);
  }
});
