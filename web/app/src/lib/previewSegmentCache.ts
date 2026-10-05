export interface CachedPreviewSegment { url: string; bytes: number }

interface Entry {
  source: string;
  refs: number;
  used: number;
  status: "queued" | "loading" | "ready";
  controller: AbortController;
  media?: CachedPreviewSegment;
  promise: Promise<CachedPreviewSegment>;
  resolve: (media: CachedPreviewSegment) => void;
  reject: (error: unknown) => void;
}

/** Compressed media survives seeks; a bounded queue prevents obsolete scrubs
 * from starting unlimited server renders. Scope belongs to one visual timeline. */
export class PreviewSegmentCache {
  private entries = new Map<string, Entry>();
  private running = 0;
  private clock = 0;
  private closed = false;

  constructor(private maxBytes = 128 * 1024 * 1024, private maxEntries = 64,
    private concurrency = 2) {}

  peek(source: string): CachedPreviewSegment | null {
    return this.entries.get(source)?.media || null;
  }

  acquire(source: string) {
    if (this.closed) throw new Error("Preview cache is closed");
    let entry = this.entries.get(source);
    if (!entry) {
      let resolve!: Entry["resolve"], reject!: Entry["reject"];
      const promise = new Promise<CachedPreviewSegment>((yes, no) => { resolve = yes; reject = no; });
      entry = { source, refs: 0, used: 0, status: "queued", controller: new AbortController(),
        promise, resolve, reject };
      this.entries.set(source, entry);
    }
    entry.refs++;
    entry.used = ++this.clock;
    this.pump();
    let released = false;
    return { promise: entry.promise, release: () => {
      if (released) return;
      released = true;
      entry.refs--;
      if (!entry.refs && entry.status === "queued") {
        this.entries.delete(source);
        entry.reject(new DOMException("Preview position superseded", "AbortError"));
      }
      // An already running request finishes into the cache, even after a seek.
      // Cancelling the client would not stop the corresponding server render.
      this.trim();
    } };
  }

  dispose() {
    this.closed = true;
    for (const entry of this.entries.values()) {
      entry.controller.abort();
      if (entry.status === "queued") entry.reject(new DOMException("Preview scope closed", "AbortError"));
      if (entry.media) URL.revokeObjectURL(entry.media.url);
    }
    this.entries.clear();
  }

  private pump() {
    if (this.closed) return;
    while (this.running < this.concurrency) {
      const next = [...this.entries.values()].filter(e => e.status === "queued" && e.refs)
        .sort((a, b) => b.used - a.used)[0];
      if (!next) break;
      this.running++;
      next.status = "loading";
      void this.load(next).finally(() => { this.running--; this.pump(); });
    }
  }

  private async load(entry: Entry) {
    try {
      let blob: Blob | null = null;
      for (let attempt = 0; !blob; attempt++) {
        let retryable = true;
        try {
          const response = await fetch(entry.source, { signal: entry.controller.signal });
          if (!response.ok) {
            retryable = response.status === 408 || response.status === 429 || response.status >= 500;
            let message = `预览视频段失败（HTTP ${response.status}）`;
            try { const body = await response.json(); message = body.error?.message || message; } catch { /* keep status */ }
            throw new Error(message);
          }
          blob = await response.blob();
          if (!blob.size) { retryable = false; throw new Error("预览视频段为空"); }
        } catch (error) {
          if (entry.controller.signal.aborted || !retryable || attempt >= 2) throw error;
          await new Promise<void>((resolve, reject) => {
            const cancel = () => { clearTimeout(timer); reject(new DOMException("Preview scope closed", "AbortError")); };
            const timer = setTimeout(() => { entry.controller.signal.removeEventListener("abort", cancel); resolve(); }, 200 * (attempt + 1));
            entry.controller.signal.addEventListener("abort", cancel, { once: true });
          });
        }
      }
      if (this.closed || entry.controller.signal.aborted) throw new DOMException("Preview scope closed", "AbortError");
      entry.media = { url: URL.createObjectURL(blob), bytes: blob.size };
      entry.status = "ready";
      entry.resolve(entry.media);
      this.trim();
    } catch (error) {
      this.entries.delete(entry.source);
      entry.reject(error);
    }
  }

  private trim() {
    const ready = [...this.entries.values()].filter(e => e.media);
    let bytes = ready.reduce((sum, e) => sum + e.media!.bytes, 0), count = ready.length;
    for (const entry of ready.filter(e => !e.refs).sort((a, b) => a.used - b.used)) {
      if (bytes <= this.maxBytes && count <= this.maxEntries) break;
      URL.revokeObjectURL(entry.media!.url);
      this.entries.delete(entry.source);
      bytes -= entry.media!.bytes;
      count--;
    }
  }
}
