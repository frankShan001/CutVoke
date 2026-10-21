import { useSyncExternalStore } from "react";
import { getLanguageSnapshot, getLocale, subscribeLanguage } from "./locale";
import { translate } from "./messages";
export { translate, catalogs, msg, formatMessage, type Catalog, type UiMessage } from "./messages";
export const tr = (source: string, ...values: unknown[]): string => translate(source, getLocale(), values);
export function useLocale() {
  useSyncExternalStore(subscribeLanguage, getLanguageSnapshot, getLanguageSnapshot);
  return getLocale();
}

/** Rerenders the existing tree on language changes without remounting project/forms. */
export function LocalizedApp({ App }: { App: React.ComponentType }) {
  useLocale();
  if (typeof document !== "undefined") document.title = tr("CutVoke 编辑器");
  return <App />;
}
