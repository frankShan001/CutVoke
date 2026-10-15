import type { LanguagePreference, Locale } from "../i18n/locale";

export interface DesktopLanguageState {
  preference: LanguagePreference;
  locale: Locale;
  systemLanguages: string[];
}
declare global {
  interface Window {
    cutvokeDesktop?: {
      initialLanguage: DesktopLanguageState;
      getLanguageState(): DesktopLanguageState;
      setLanguagePreference(value: LanguagePreference): Promise<DesktopLanguageState>;
      chooseCacheDirectory(): Promise<string | null>;
      revealCacheDirectory(): Promise<void>;
      info(): Promise<{ mcp: object; version: string }>;
    };
  }
}
