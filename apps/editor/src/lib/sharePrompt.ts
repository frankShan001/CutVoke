/** Optional, dismissible "share your edit" line shown once an export succeeds. No network, no gating. */
const KEY = "cutvoke.sharePrompt.dismissed";

export function sharePromptDismissed(): boolean {
  try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function dismissSharePrompt(): void {
  try { localStorage.setItem(KEY, "1"); } catch { /* The line simply reappears next session. */ }
}
