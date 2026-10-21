import { Languages } from "lucide-react";
import { tr, useLocale } from "../i18n";
import { getLanguagePreference, LANGUAGE_NAMES, LOCALES, setLanguage, type LanguagePreference } from "../i18n/locale";

export function LanguageSelect() {
  useLocale();
  return (
    <label className="language-select" title={tr("界面语言")}>
      <Languages size={15} aria-hidden="true" />
      <span className="sr-only">{tr("界面语言")}</span>
      <select data-testid="language-select" aria-label={tr("界面语言")} value={getLanguagePreference()}
        onChange={event => setLanguage(event.target.value as LanguagePreference)}>
        <option value="system">{tr("跟随系统")}</option>
        {LOCALES.map(locale => <option key={locale} value={locale} lang={locale} dir={locale === "ar" ? "rtl" : "ltr"}>{LANGUAGE_NAMES[locale]}</option>)}
      </select>
    </label>
  );
}
