/** Pending local edits must settle before export, history or project switching. */
const pending = new Map<string, () => Promise<boolean>>();

export function registerPendingEdit(key: string, flush: () => Promise<boolean>) {
  pending.set(key, flush);
  return () => { if (pending.get(key) === flush) pending.delete(key); };
}

export async function flushPendingEdits(): Promise<boolean> {
  for (const flush of [...pending.values()]) if (!await flush()) return false;
  return true;
}
