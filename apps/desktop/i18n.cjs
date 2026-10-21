const fs = require("node:fs");
const path = require("node:path");

const locales = ["zh-CN", "zh-TW", "en", "ja", "ko", "es", "fr", "de", "pt-BR", "ru", "ar"];
const catalogs = Object.fromEntries(locales.map(locale => [locale, require(`./locales/${locale}.json`)]));
function matchLocale(tag) {
  const normalized = String(tag).trim().replaceAll("_", "-").toLowerCase();
  if (!/^[a-z]{2,3}(?:-[a-z0-9]{1,8})*$/.test(normalized)) return undefined;
  if (normalized === "zh" || normalized.startsWith("zh-")) {
    return /(?:^|-)(?:hant|tw|hk|mo)(?:-|$)/.test(normalized) ? "zh-TW" : "zh-CN";
  }
  const base = normalized.split("-")[0];
  return base === "pt" ? "pt-BR" : locales.find(locale => locale.toLowerCase() === base);
}
const validPreference = value => value === "system" || locales.includes(value);
function createLanguageController(storagePath, getSystemLanguages) {
  let preference = "system";
  try {
    const saved = JSON.parse(fs.readFileSync(storagePath, "utf8"));
    if (validPreference(saved.preference)) preference = saved.preference;
  } catch { /* A missing or damaged preference uses the system language. */ }
  function snapshot() {
    const systemLanguages = getSystemLanguages();
    const locale = preference === "system" ? systemLanguages.map(matchLocale).find(Boolean) || "en" : preference;
    return { preference, locale, systemLanguages, messages: catalogs[locale] };
  }
  function text(key, ...values) {
    const template = catalogs[snapshot().locale][key] || catalogs.en[key] || key;
    return template.replace(/\{(\d+)\}/g, (token, index) => values[index] === undefined ? token : String(values[index]));
  }
  return {
    snapshot, text,
    setPreference(value) {
      if (!validPreference(value)) throw new Error("Invalid interface language preference");
      if (value !== preference || !fs.existsSync(storagePath)) {
        fs.mkdirSync(path.dirname(storagePath), { recursive: true });
        const temporary = `${storagePath}.tmp`;
        fs.writeFileSync(temporary, JSON.stringify({ preference: value }) + "\n", "utf8");
        fs.renameSync(temporary, storagePath);
      }
      preference = value;
      return snapshot();
    },
  };
}
function cacheDialogOptions(language) {
  return { title: language.text("chooseCache"), buttonLabel: language.text("chooseFolder"), properties: ["openDirectory", "createDirectory"] };
}
function errorDialogOptions(language, kind, detail) {
  return { type: "error", title: language.text(kind === "service" ? "serviceTitle" : "startupTitle"),
    message: language.text(kind === "service" ? "serviceSummary" : "startupSummary"),
    detail: `${language.text("technicalDetails")}\n${detail}`, buttons: [language.text("close")], defaultId: 0, noLink: true };
}
module.exports = { locales, catalogs, matchLocale, createLanguageController, cacheDialogOptions, errorDialogOptions };
