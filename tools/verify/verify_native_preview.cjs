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
      const state = { promise: null, cancel: null, diagnostic: { events: [], counts: {}, dropped: 0, media: [], mediaDropped: 0, clocks: [], clocksDropped: 0, stops: [], stopsDropped: 0 } };
      const started = Date.now(), nodes = new WeakMap();
      let nextNode = 0, previousState = "", previousClock = "";
      const number = value => Number.isFinite(value) ? value : null;
      const record = (event, video, metadata, channel = "events") => {
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
            readyState: number(video?.readyState), time: number(video?.currentTime),
            duration: number(video?.duration), active: video ? video === document.querySelector("video.player__video") : null };
          if (metadata) { entry.mediaTime = number(metadata.mediaTime); entry.presentedFrames = number(metadata.presentedFrames); }
          // Poll transitions, not the continuously advancing clock. Preserve the
          // final state on timeout even when no compositor callback ever arrives.
          if (event === "state") {
            const signature = JSON.stringify([entry.node, entry.sourceGeneration, entry.paused, entry.seeking, entry.ended, entry.readyState]);
            if (signature === previousState) return;
            previousState = signature;
          }
          append(channel, entry);
        } catch { /* Diagnostic collection must not interrupt the observer. */ }
      };
      // Independent bounded channels prevent RAF samples from evicting the
      // media/stop events needed to explain an early stop or a lagging decoder.
      const append = (channel, entry) => {
        const d = state.diagnostic, values = d[channel];
        if (values.length === 64) {
          values.splice(32, 1); // retain first and most recent observations
          const counter = channel === "events" ? "dropped" : `${channel}Dropped`;
          d[counter] = Math.min(1_000_000, d[counter] + 1);
        }
        values.push(entry);
      };
      const mediaTypes = ["play", "playing", "pause", "seeking", "seeked", "ended", "waiting", "loadedmetadata", "canplay"];
      const media = event => {
        try {
          const video = event.target;
          if (video?.matches("video.player__video, video.player__cached-video")) record(`media-${event.type}`, video, null, "media");
        } catch { /* Diagnostic-only DOM reads. */ }
      };
      const clock = event => {
        try {
          const d = event.detail;
          const time = number(d?.time), end = number(d?.end);
          const running = d?.playing;
          const playing = typeof running === "boolean" ? running : null;
          // Quarter-second buckets plus every running/end transition. Do not
          // call the audio clock, retain detail objects or infer a stop reason.
          const signature = JSON.stringify([time === null ? null : Math.floor(time * 4), end, playing]);
          if (signature === previousClock) return;
          previousClock = signature;
          append("clocks", { elapsedMs: Date.now() - started, time, end, playing });
        } catch { /* A malformed event cannot affect acceptance. */ }
      };
      const stop = event => {
        try {
          const d = event.detail;
          const reason = d?.reason;
          if (!["audio-clock-end", "video-ended", "video-time-end", "image-end"].includes(reason)) return;
          const entry = { elapsedMs: Date.now() - started, reason };
          for (const field of ["observedTime", "decisionEnd", "decisionWindowStart", "decisionWindowEnd", "end", "playhead", "reportedVideoTime", "windowStart", "windowEnd", "audioAnchorTime"])
            entry[field] = number(d[field]);
          for (const field of ["audioActive", "nativeAudioReady", "playing", "prepared"]) {
            const value = d[field];
            entry[field] = typeof value === "boolean" ? value : null;
          }
          append("stops", entry);
          record("stop-snapshot", document.querySelector("video.player__video"), null, "media");
        } catch { /* A malformed event cannot affect acceptance. */ }
      };
      let restoreEvidence = () => {};
      try {
        const prior = Object.getOwnPropertyDescriptor(window, "__cutvokePreviewStopEvidence");
        // Refuse an occupied accessor/non-writable property, rather than invoke
        // foreign getters or change another diagnostic owner's configuration.
        if (!prior || ("value" in prior && prior.writable && prior.configurable)) {
          Object.defineProperty(window, "__cutvokePreviewStopEvidence", { configurable: true, writable: true, value: true });
          restoreEvidence = () => {
            if (prior) Object.defineProperty(window, "__cutvokePreviewStopEvidence", prior);
            else delete window.__cutvokePreviewStopEvidence;
          };
        }
        for (const type of mediaTypes) document.addEventListener(type, media, true);
        window.addEventListener("cutvoke:transport-clock", clock);
        window.addEventListener("cutvoke:preview-stop", stop);
      } catch { /* Existing frame acceptance still runs if diagnostics cannot. */ }
      const cleanEvidence = () => {
        try { for (const type of mediaTypes) document.removeEventListener(type, media, true); } catch {}
        try { window.removeEventListener("cutvoke:transport-clock", clock); window.removeEventListener("cutvoke:preview-stop", stop); } catch {}
        try { restoreEvidence(); } catch {}
        restoreEvidence = () => {};
      };
      state.snapshot = () => { record("snapshot", document.querySelector("video.player__video")); return state.diagnostic; };
      window[key] = state;
      state.promise = new Promise((resolve, reject) => {
        let target = null, source = "", callback = null, generation = 0, done = false;
        const cleanup = () => {
          done = true; generation++;
          cleanEvidence();
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
