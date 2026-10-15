/** UI preferences are browser-local; never part of a project or an API command. */
export const LOCALES = ["zh-CN", "zh-TW", "en", "ja", "ko", "es", "fr", "de", "pt-BR", "ru", "ar"] as const;
export type Locale = typeof LOCALES[number];
export type LanguagePreference = Locale | "system";
export const LANGUAGE_STORAGE_KEY = "cutvoke.ui.language";

export const LANGUAGE_NAMES: Record<Locale, string> = {
  "zh-CN": "简体中文", "zh-TW": "繁體中文", en: "English", ja: "日本語", ko: "한국어",
  es: "Español", fr: "Français", de: "Deutsch", "pt-BR": "Português", ru: "Русский", ar: "العربية",
};

export function matchLocale(tag: string): Locale | undefined {
  const normalized = tag.trim().replaceAll("_", "-").toLowerCase();
  if (!normalized || !/^[a-z]{2,3}(?:-[a-z0-9]{1,8})*$/.test(normalized)) return undefined;
  if (normalized === "zh" || normalized.startsWith("zh-")) {
    return /(?:^|-)(?:hant|tw|hk|mo)(?:-|$)/.test(normalized) ? "zh-TW" : "zh-CN";
  }
  const base = normalized.split("-")[0];
  if (base === "pt") return "pt-BR";
  return LOCALES.find(locale => locale.toLowerCase() === base);
}

export function detectLocale(languages: readonly string[]): Locale {
  for (const language of languages) {
    const matched = matchLocale(language);
    if (matched) return matched;
  }
  return "en";
}

function readPreference(): LanguagePreference {
  try {
    const saved = globalThis.localStorage?.getItem(LANGUAGE_STORAGE_KEY);
    if (saved === "system" || LOCALES.includes(saved as Locale)) return saved as LanguagePreference;
  } catch { /* Restricted storage still permits an in-memory choice. */ }
  return "system";
}

const desktop = typeof window === "undefined" ? undefined : window.cutvokeDesktop;
const initialDesktopPreference = desktop?.initialLanguage?.preference;
let preference = initialDesktopPreference && (initialDesktopPreference === "system" || LOCALES.includes(initialDesktopPreference))
  ? initialDesktopPreference : readPreference();
const browserLanguages = () => {
  try { if (desktop) return desktop.getLanguageState().systemLanguages; } catch { /* Use browser matching if the shell is unavailable. */ }
  return typeof navigator === "undefined" ? [] : navigator.languages?.length ? navigator.languages : [navigator.language];
};
let locale = preference === "system" ? detectLocale(browserLanguages()) : preference;
let snapshot = `${preference}:${locale}`;
const listeners = new Set<() => void>();

export const getLocale = (): Locale => locale;
export const getLanguagePreference = (): LanguagePreference => preference;
export const getLanguageSnapshot = () => snapshot;
export const isRtl = (value: Locale = locale) => value === "ar";
export function subscribeLanguage(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function applyPreference(value: LanguagePreference) {
  preference = value;
  locale = value === "system" ? detectLocale(browserLanguages()) : value;
  snapshot = `${preference}:${locale}`;
  if (typeof document !== "undefined") {
    document.documentElement.lang = locale;
    document.documentElement.dir = isRtl(locale) ? "rtl" : "ltr";
    document.documentElement.dataset.uiLocale = locale;
  }
  void desktop?.setLanguagePreference(value).catch(() => { /* The current session and browser preference still work. */ });
  for (const listener of listeners) listener();
}

export function setLanguage(value: LanguagePreference) {
  if (value !== "system" && !LOCALES.includes(value)) return;
  try { globalThis.localStorage?.setItem(LANGUAGE_STORAGE_KEY, value); } catch { /* Session-only preference. */ }
  applyPreference(value);
}

if (typeof window !== "undefined") {
  window.addEventListener("languagechange", () => { if (preference === "system") applyPreference("system"); });
  window.addEventListener("storage", event => { if (event.key === LANGUAGE_STORAGE_KEY || event.key === null) applyPreference(readPreference()); });
}
applyPreference(preference);
