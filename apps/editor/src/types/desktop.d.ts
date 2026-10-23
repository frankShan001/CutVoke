import type { LanguagePreference, Locale } from "../i18n/locale";

export interface DesktopLanguageState {
  preference: LanguagePreference;
  locale: Locale;
  systemLanguages: string[];
}
export interface DesktopSettings {
  setupComplete: boolean;
  projectDirectory: string;
  projectLocationManaged: boolean;
  gpuAcceleration?: boolean;
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
      settings(): Promise<DesktopSettings>;
      saveSettings(value: { projectDirectory: string; cacheDirectory: string; maxBytes: number; gpuAcceleration?: boolean }): Promise<{ restartRequired: boolean }>;
      chooseProjectDirectory(): Promise<string | null>;
      chooseExportFile(suggested: string): Promise<string | null>;
      revealExport(projectId: string, jobId: string): Promise<void>;
    };
  }
}
