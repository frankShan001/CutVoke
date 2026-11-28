/** Optional, dismissible "share your edit" line shown once an export succeeds. No network, no gating. */
const KEY = "cutvoke.sharePrompt.dismissed";
let dismissedInPage = false;

export function sharePromptDismissed(): boolean {
  if (dismissedInPage) return true;
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function dismissSharePrompt(): void {
  dismissedInPage = true;
  try { localStorage.setItem(KEY, "1"); } catch { /* Fall back to the current page lifetime. */ }
}
