/** A shared two-request queue; leaving the viewport removes queued work.
 * Running decodes finish into a bounded cache instead of flooding the server on scroll. */
type Entry = { url: string; refs: number; state: "queued" | "loading" | "done"; blobUrl: string | null;
  bytes: number; resolve: (value: string | null) => void; promise: Promise<string | null> };
const entries = new Map<string, Entry>();
let running = 0;

function prune() {
  let bytes = [...entries.values()].reduce((sum, entry) => sum + entry.bytes, 0);
  for (const [key, entry] of entries) {
    if (entries.size <= 128 && bytes <= 16 * 1024 * 1024) break;
    if (entry.refs || entry.state !== "done") continue;
    if (entry.blobUrl) URL.revokeObjectURL(entry.blobUrl);
    bytes -= entry.bytes; entries.delete(key);
  }
}
function pump() {
  for (const entry of entries.values()) {
    if (running >= 2) break;
    if (entry.state !== "queued" || !entry.refs) continue;
    entry.state = "loading"; running += 1;
    void fetch(entry.url).then(async response => {
      if (response.status === 204) return null;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      entry.bytes = blob.size;
      return URL.createObjectURL(blob);
    }).then(value => { entry.blobUrl = value; entry.resolve(value); })
      .catch(() => { entry.resolve(null); entries.delete(entry.url); })
      .finally(() => { entry.state = "done"; running -= 1; prune(); pump(); });
  }
}
export function acquireTimelineMedia(url: string) {
  let entry = entries.get(url);
  if (!entry) {
    let resolve!: Entry["resolve"];
    const promise = new Promise<string | null>(done => { resolve = done; });
    entry = { url, refs: 0, state: "queued", blobUrl: null, bytes: 0, resolve, promise };
    entries.set(url, entry);
  } else { entries.delete(url); entries.set(url, entry); }
  entry.refs += 1;
  pump();
  const current = entry;
  let released = false;
  return { promise: entry.promise, release() {
    if (released) return;
    released = true; current.refs = Math.max(0, current.refs - 1);
    if (!current.refs && current.state === "queued") {
      entries.delete(url); current.resolve(null);
    }
    prune();
  } };
}
