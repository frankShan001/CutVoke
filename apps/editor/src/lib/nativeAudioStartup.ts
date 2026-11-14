/** HTML media startup is asynchronous. Keep the mix muted until every play and
 * seek has settled; commit transport only then. This is not sample-accurate mixing. */
export type StartupVoice = { element: HTMLAudioElement; time: number };
type Request = { key: string; voices: StartupVoice[]; context: AudioContext;
  current(): boolean; begin?(): void; commit(): boolean; failed(): void };
type Attempt = { request: Request; abort: AbortController; phase: 'pending' | 'running' | 'failed'; timer?: ReturnType<typeof setTimeout> };
export class NativeAudioStartup {
  private attempt?: Attempt;
  private owners = new WeakMap<HTMLAudioElement, Attempt>();
  pending(element: HTMLAudioElement) { return this.attempt?.phase === 'pending' && this.owners.get(element) === this.attempt; }
  private pause(attempt: Attempt) {
    for (const { element } of attempt.request.voices) if (this.owners.get(element) === attempt) { element.pause(); element.muted = true; }
  }
  cancel() {
    const attempt = this.attempt;
    if (!attempt) return;
    this.attempt = undefined; clearTimeout(attempt.timer); attempt.abort.abort(); this.pause(attempt);
  }
  start(request: Request) {
    const old = this.attempt;
    if (old?.request.current() && old.request.key === request.key && old.request.voices.length === request.voices.length &&
      old.request.voices.every((v, i) => v.element === request.voices[i].element)) {
      if (old.phase === 'running' && request.context.state !== 'running') {
        old.phase = 'failed'; old.abort.abort(); this.pause(old); request.failed();
      }
      return old.phase;
    }
    this.cancel();
    const attempt: Attempt = { request, abort: new AbortController(), phase: 'pending' };
    this.attempt = attempt; request.begin?.();
    const current = () => this.attempt === attempt && !attempt.abort.signal.aborted && request.current();
    const check = () => { if (!current()) throw new Error('stale native startup'); };
    const fail = () => {
      if (this.attempt !== attempt || attempt.phase === 'failed') return;
      const notify = request.current();
      attempt.phase = 'failed'; clearTimeout(attempt.timer); attempt.abort.abort(); this.pause(attempt);
      if (notify) request.failed();
    };
    for (const { element } of request.voices) { this.owners.set(element, attempt); element.muted = true; }
    attempt.timer = setTimeout(fail, 2000);
    const seek = ({ element, time }: StartupVoice) => new Promise<void>((resolve, reject) => {
      check();
      const clean = () => { element.removeEventListener('seeked', done); element.removeEventListener('error', error); attempt.abort.signal.removeEventListener('abort', error); };
      const done = () => { clean(); resolve(); };
      const error = () => { clean(); reject(new Error('native seek interrupted')); };
      element.addEventListener('seeked', done); element.addEventListener('error', error); attempt.abort.signal.addEventListener('abort', error);
      try { element.currentTime = time; if (!element.seeking) done(); } catch (e) { clean(); reject(e); }
    });
    void (async () => {
      try {
        await request.context.resume(); check();
        if (request.context.state !== 'running') throw new Error('audio context did not resume');
        await Promise.all(request.voices.map(seek)); check();
        await Promise.all(request.voices.map(async ({ element }) => {
          await element.play();
          // A stale play can start after cancellation. Do not stop a newer owner.
          if (!current()) { if (this.owners.get(element) === attempt) element.pause(); check(); }
        })); check();
        // A fast voice can advance while another play Promise is pending. It was
        // muted; restore the requested start before making the complete mix audible.
        await Promise.all(request.voices.map(seek)); check();
        if (request.voices.some(({ element, time }) => element.seeking || Math.abs(element.currentTime - time) > .04)) throw new Error('native seek drifted before commit');
        if (request.context.state !== 'running' || !request.commit()) throw new Error('native commit cancelled');
        attempt.phase = 'running'; clearTimeout(attempt.timer);
        for (const { element } of request.voices) element.muted = false;
      } catch { fail(); }
    })();
    return attempt.phase;
  }
}
