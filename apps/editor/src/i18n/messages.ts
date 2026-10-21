import type { Locale } from "./locale";
import en from "./locales/en.json";
import zhTW from "./locales/zh-TW.json";
import ja from "./locales/ja.json";
import ko from "./locales/ko.json";
import es from "./locales/es.json";
import fr from "./locales/fr.json";
import de from "./locales/de.json";
import pt from "./locales/pt-BR.json";
import ru from "./locales/ru.json";
import ar from "./locales/ar.json";

export type Catalog = Readonly<Record<string, string>>;
export const catalogs: Record<Exclude<Locale, "zh-CN">, Catalog> = { en, "zh-TW": zhTW, ja, ko, es, fr, de, "pt-BR": pt, ru, ar };

/** Source message IDs use authored Chinese text (gettext style), never user data. */
export function translate(source: string, language: Locale, values: readonly unknown[] = [], overrides: Partial<Record<Locale, Catalog>> = {}): string {
  const selected = overrides[language] ?? (language === "zh-CN" ? undefined : catalogs[language]);
  const fallback = overrides.en ?? catalogs.en;
  const message = language === "zh-CN" ? source : selected?.[source] || fallback[source] || source;
  return message.replace(/\{(\d+)\}/g, (token, index: string) => Number(index) < values.length ? String(values[Number(index)] ?? "") : token);
}

/** Store authored UI messages as data so old statuses can follow a new locale.
 * This type is only used for UI state, never project or API serialization. */
export interface UiMessage { readonly kind: "ui-message"; readonly source: string; readonly values: readonly unknown[] }
export const msg = (source: string, ...values: unknown[]): UiMessage => ({ kind: "ui-message", source, values });
export function formatMessage(value: string | UiMessage, language: Locale): string {
  if (typeof value === "string") return value;
  return translate(value.source, language, value.values.map(item =>
    item && typeof item === "object" && "kind" in item && item.kind === "ui-message"
      ? formatMessage(item as UiMessage, language) : item));
}
