// Shared by browser regression coverage and all four packaged-app acceptances.
async function verifyNativePreview(page, expect, { timeout = 90_000, cycles = 3, onDiagnostic = () => {} } = {}) {
  const video = page.locator("video.player__video");
  const ruler = page.getByRole("slider", { name: "时间线播放头", exact: true });
  const samples = [];
  for (let cycle = 0; cycle < cycles; cycle++) {
    if (cycle) { await ruler.focus(); await ruler.press("Home"); }
    // First-frame editing can start well before the continuous decoder loads.
    // A short clip can finish on the source/audio clock during that load, so
    // establish the decoder prerequisite before measuring its playback.
    await expect(video).toBeVisible({ timeout });
    await expect.poll(() => video.evaluate(v => v.readyState), { timeout }).toBeGreaterThanOrEqual(2);
    // Install the compositor observer before Play. Slow automation round trips
    // must not consume a short clip before its first playing frame is observed.
    const observerKey = `__cutvokeNativePreview_${Date.now()}_${cycle}`;
    await page.evaluate(({ timeout, key }) => {
      // Strict allowlist: no URLs, DOM text, media paths or exception messages.
      const state = { promise: null, cancel: null, diagnostic: { events: [], counts: {}, dropped: 0 } };
      const started = Date.now(), nodes = new WeakMap();
      let nextNode = 0, previousState = "";
      const number = value => Number.isFinite(value) ? value : null;
      const record = (event, video, metadata) => {
        try {
          const d = state.diagnostic;
          d.counts[event] = Math.min(1_000_000, (d.counts[event] || 0) + 1);
          let identity = null;
          if (video) {
            identity = nodes.get(video);
            if (!identity) { identity = { id: ++nextNode, source: video.currentSrc, generation: 1 }; nodes.set(video, identity); }
            else if (identity.source !== video.currentSrc) { identity.source = video.currentSrc; identity.generation++; }
          }
          const entry = { event, elapsedMs: Date.now() - started, node: identity?.id ?? null,
            sourceGeneration: identity?.generation ?? null, paused: video ? Boolean(video.paused) : null,
            seeking: video ? Boolean(video.seeking) : null, ended: video ? Boolean(video.ended) : null,
            readyState: number(video?.readyState), time: number(video?.currentTime) };
          if (metadata) { entry.mediaTime = number(metadata.mediaTime); entry.presentedFrames = number(metadata.presentedFrames); }
          // Poll transitions, not the continuously advancing clock. Preserve the
          // final state on timeout even when no compositor callback ever arrives.
          if (event === "state") {
            const signature = JSON.stringify([entry.node, entry.sourceGeneration, entry.paused, entry.seeking, entry.ended, entry.readyState]);
            if (signature === previousState) return;
            previousState = signature;
          }
          if (d.events.length === 64) { d.events.shift(); d.dropped = Math.min(1_000_000, d.dropped + 1); }
          d.events.push(entry);
        } catch { /* Diagnostic collection must not interrupt the observer. */ }
      };
      state.snapshot = () => { record("snapshot", document.querySelector("video.player__video")); return state.diagnostic; };
      window[key] = state;
      state.promise = new Promise((resolve, reject) => {
        let target = null, source = "", callback = null, generation = 0, done = false;
        const cleanup = () => {
          done = true; generation++;
          clearInterval(watch); clearTimeout(deadline);
          if (target && callback !== null) target.cancelVideoFrameCallback(callback);
        };
        state.cancel = cleanup;
        const arm = () => {
          if (done) return;
          const current = document.querySelector("video.player__video");
          record("state", current);
          if (!current || current.paused || current.seeking) return;
          if (typeof current.requestVideoFrameCallback !== "function") {
            record("unavailable", current); cleanup(); reject(new Error("Preview compositor frame callbacks unavailable")); return;
          }
          if (current === target && current.currentSrc === source && callback !== null) return;
          if (target && callback !== null) target.cancelVideoFrameCallback(callback);
          target = current; source = current.currentSrc;
          const registered = ++generation;
          record("arm", current);
          callback = current.requestVideoFrameCallback((_, metadata) => {
            if (done) return;
            if (registered !== generation) { record("stale-generation", current, metadata); return; }
            callback = null;
            const reason = current !== document.querySelector("video.player__video") ? "inactive-node"
              : current.currentSrc !== source ? "changed-source" : current.paused ? "paused"
              : current.seeking ? "seeking" : metadata.mediaTime <= .25 ? "early-frame" : null;
            record(reason || "accepted", current, metadata);
            if (reason) { arm(); return; }
            try {
              const canvas = document.createElement("canvas");
              canvas.width = current.videoWidth; canvas.height = current.videoHeight;
              const context = canvas.getContext("2d"); context.drawImage(current, 0, 0);
              const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
              let energy = 0;
              for (let i = 0; i < pixels.length; i += 4) energy += pixels[i] + pixels[i + 1] + pixels[i + 2];
              cleanup();
              resolve({ width: canvas.width, height: canvas.height, energy, time: current.currentTime,
                mediaTime: metadata.mediaTime, presentedFrames: metadata.presentedFrames });
            } catch (error) { cleanup(); reject(error); }
          });
        };
        const watch = setInterval(arm, 16);
        const deadline = setTimeout(() => { record("deadline", document.querySelector("video.player__video")); cleanup(); reject(new Error("No submitted playing preview frame")); }, timeout);
        arm();
      });
      // A failed Play/locator assertion can outlive the observer's deadline.
      // Retain its rejection for the later await without an unhandled event.
      void state.promise.catch(() => {});
    }, { timeout, key: observerKey });
    let frame;
    try {
      await page.getByRole("button", { name: "播放", exact: true }).click();
      await expect.poll(() => video.evaluate(v => v.currentTime), { timeout }).toBeGreaterThan(.25);
      await expect(video).toBeVisible();
      await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow")), { timeout }).toBeGreaterThan(.25);
      // HAVE_ENOUGH_DATA and the audio clock can precede compositor submission,
      // particularly on Intel macOS. Read the first submitted playing frame,
      // never poll until pixels happen to become nonblack. Follow a legitimate
      // pool/source handoff while waiting, rejecting callbacks from old sources.
      frame = await page.evaluate(key => window[key].promise, observerKey);
    } finally {
      const diagnostic = await page.evaluate(key => {
        const state = window[key];
        if (!state) return null;
        try { return state.snapshot(); } catch { return null; }
        finally { state.cancel(); delete window[key]; }
      }, observerKey).catch(() => null);
      // Diagnostics never change acceptance or hide the original failure.
      try { if (diagnostic) onDiagnostic({ cycle, ...diagnostic }); } catch {}
    }
    expect(frame.width).toBeGreaterThan(0); expect(frame.height).toBeGreaterThan(0);
    expect(frame.energy, JSON.stringify(frame)).toBeGreaterThan(0);
    await page.getByRole("button", { name: "暂停", exact: true }).click();
    samples.push(frame);
  }
  return samples;
}
module.exports = { verifyNativePreview };
