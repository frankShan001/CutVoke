// Shared by browser regression coverage and all four packaged-app acceptances.
async function verifyNativePreview(page, expect, { timeout = 90_000, cycles = 3, traceSeekWrites = false,
  onDiagnostic = () => {}, seekControlStep = null } = {}) {
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
    await page.evaluate(({ timeout, key, traceSeekWrites, seekControlStep }) => {
      // Strict allowlist: no URLs, DOM text, media paths or exception messages.
      const state = { promise: null, cancel: null, diagnostic: { events: [], counts: {}, dropped: 0, media: [], mediaDropped: 0, clocks: [], clocksDropped: 0, stops: [], stopsDropped: 0 } };
      const seekCoverage = traceSeekWrites ? {
        status: "unavailable", startOrdinal: null, acceptedOrdinal: null, snapshotOrdinal: null,
        endOrdinal: null, absenceEvidence: false,
      } : null;
      if (seekCoverage) Object.assign(state.diagnostic, {
        seekCoverage, seekWrites: [], seekWritesDropped: 0, phases: [], phasesDropped: 0,
        callerProbeInstalled: false, unknownSeekCallers: 0,
      });
      const seekSites = ["bind", "loadedmetadata", "loadeddata", "seeked", "loaded-other", "video-loop", "video-end",
        "external-sync", "audio-tick", "project-reset", "play-restart", "audition", "stop", "frame-step"];
      const phaseNames = ["selection", "bound", "presented", "fallback-segment", "fallback-open", "fallback-video"];
      const fieldEvidence = raw => {
        const values = {};
        for (const key of ["prepared", "audioActive", "nativeAudioReady", "playing", "offered", "matching", "retained", "warmedOffer", "ready", "holding", "sourceMatches"]) {
          const value = raw?.[key];
          if (typeof value === "boolean") values[key] = value;
        }
        for (const key of ["playhead", "decodeTime", "reportedTime", "audioAnchor", "windowStart", "windowEnd"]) {
          const value = number(raw?.[key]);
          if (value !== null) values[key] = value;
        }
        return values;
      };
      let origin = null;
      const started = Date.now(), nodes = new WeakMap();
      let ordinal = 0;
      let nextNode = 0, previousState = "", previousClock = "";
      const number = value => Number.isFinite(value) ? value : null;
      const record = (event, video, metadata, channel = "events", requestedTime, evidence = null) => {
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
          if (channel === "seekWrites") {
            entry.requestedTime = number(requestedTime);
            entry.caller = evidence?.site ?? "unknown";
            if (entry.caller === "unknown") d.unknownSeekCallers = Math.min(1_000_000, d.unknownSeekCallers + 1);
            else entry.evidence = evidence.fields;
          }
          if (channel === "phases") entry.evidence = evidence;
          if (metadata) { entry.mediaTime = number(metadata.mediaTime); entry.presentedFrames = number(metadata.presentedFrames); }
          // Poll transitions, not the continuously advancing clock. Preserve the
          // final state on timeout even when no compositor callback ever arrives.
          if (event === "state") {
            const signature = JSON.stringify([entry.node, entry.sourceGeneration, entry.paused, entry.seeking, entry.ended, entry.readyState]);
            if (signature === previousState) return;
            previousState = signature;
          }
          append(channel, entry);
          return true;
        } catch { /* Diagnostic collection must not interrupt the observer. */ }
      };
      // Independent bounded channels prevent RAF samples from evicting the
      // media/stop events needed to explain an early stop or a lagging decoder.
      const append = (channel, entry) => {
        const d = state.diagnostic, values = d[channel];
        if (seekCoverage) entry.ordinal = ++ordinal;
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
      let restoreCaller = () => {};
      if (seekCoverage) try {
        const key = "__cutvokeNativeSeekEvidence";
        const prior = Object.getOwnPropertyDescriptor(window, key);
        if (!prior) {
          let listening = true;
          const hook = {
            beginSeek(site, video, raw) {
              if (!listening || !seekSites.includes(site)) return;
              const previous = origin;
              const scope = { site, video, used: false, fields: fieldEvidence(raw) };
              origin = scope;
              return () => { if (origin === scope) origin = previous; };
            },
            phase(phase, video, raw) {
              if (listening && phaseNames.includes(phase)) record(phase, video, null, "phases", undefined, fieldEvidence(raw));
            },
          };
          Object.defineProperty(window, key, { configurable: true, value: hook });
          state.diagnostic.callerProbeInstalled = true;
          restoreCaller = () => {
            listening = false;
            origin = null;
            if (Object.getOwnPropertyDescriptor(window, key)?.value === hook) delete window[key];
          };
        }
      } catch { /* Existing acceptance still applies without caller telemetry. */ }
      let stopSeekWrites = () => {};
      if (seekCoverage) try {
        const proto = window.HTMLMediaElement?.prototype;
        const prior = proto && Object.getOwnPropertyDescriptor(proto, "currentTime");
        if (prior?.configurable && typeof prior.get === "function" && typeof prior.set === "function") {
          let observing = true, missed = false;
          const nativeSet = prior.set;
          const set = function (value) {
            if (observing) try {
              const active = document.querySelector("video.player__video");
              if (active && this === active) {
                const caller = origin?.video === this && !origin.used ? origin : null;
                if (caller) caller.used = true;
                if (!Number.isFinite(value) || !record("seek-write-attempt", this, null, "seekWrites", value, caller)) missed = true;
              }
            } catch { missed = true; }
            // The probe never coerces values or changes native setter errors.
            return Reflect.apply(nativeSet, this, [value]);
          };
          Object.defineProperty(proto, "currentTime", { ...prior, set });
          seekCoverage.startOrdinal = ++ordinal;
          stopSeekWrites = () => {
            if (!observing) return;
            observing = false;
            seekCoverage.endOrdinal = ++ordinal;
            try {
              const current = Object.getOwnPropertyDescriptor(proto, "currentTime");
              if (!current || current.set !== set || current.get !== prior.get
                || current.configurable !== prior.configurable || current.enumerable !== prior.enumerable) missed = true;
              else Object.defineProperty(proto, "currentTime", prior);
            } catch { missed = true; }
            seekCoverage.status = missed ? "incomplete" : "observed";
          };
        }
      } catch { /* An unavailable probe never changes frame acceptance. */ }
      const cleanEvidence = () => {
        stopSeekWrites();
        try { for (const type of mediaTypes) document.removeEventListener(type, media, true); } catch {}
        try { window.removeEventListener("cutvoke:transport-clock", clock); window.removeEventListener("cutvoke:preview-stop", stop); } catch {}
        try { restoreEvidence(); } catch {}
        try { restoreCaller(); } catch {}
        restoreEvidence = () => {};
        restoreCaller = () => {};
      };
      state.snapshot = (completed) => {
        if (!completed) stopSeekWrites();
        try {
          if (record("snapshot", document.querySelector("video.player__video")) && seekCoverage) seekCoverage.snapshotOrdinal = ordinal;
        } finally { stopSeekWrites(); }
        if (seekCoverage) {
          const d = state.diagnostic;
          // Absence applies only between the marker ordinals to attempted JS
          // calls through this prototype on the active video. It is not proof
          // of no seek; a capped/unavailable trace cannot rule out even writes.
          if (seekCoverage.snapshotOrdinal === null || d.seekWritesDropped || d.dropped
            || d.mediaDropped || d.clocksDropped || d.stopsDropped || d.phasesDropped) {
            if (seekCoverage.status === "observed") seekCoverage.status = "incomplete";
          }
          seekCoverage.absenceEvidence = seekCoverage.status === "observed";
        }
        return state.diagnostic;
      };
      window[key] = state;
      if (seekControlStep !== null) {
        // Isolated positive control: no frame oracle, RAF/polling or deadlines
        // from the cold/warm playback acceptance are started here.
        state.cancel = cleanEvidence;
        record("control-zero", document.querySelector("video.player__video"));
        return;
      }
      state.promise = new Promise((resolve, reject) => {
        let target = null, source = "", callback = null, generation = 0, done = false;
        const cleanup = (retainEvidence = false) => {
          const stopFrameObserver = !done;
          if (stopFrameObserver) { done = true; generation++; }
          if (!retainEvidence) cleanEvidence();
          if (!stopFrameObserver) return;
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
            if (record(reason || "accepted", current, metadata) && !reason && seekCoverage) {
              seekCoverage.acceptedOrdinal = ordinal;
            }
            if (reason) { arm(); return; }
            try {
              const canvas = document.createElement("canvas");
              canvas.width = current.videoWidth; canvas.height = current.videoHeight;
              const context = canvas.getContext("2d"); context.drawImage(current, 0, 0);
              const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
              let energy = 0;
              for (let i = 0; i < pixels.length; i += 4) energy += pixels[i] + pixels[i + 1] + pixels[i + 2];
              // Stop the strict frame observer now. On a normal opt-in success,
              // keep only passive evidence through the existing final snapshot.
              cleanup(Boolean(seekCoverage && canvas.width > 0 && canvas.height > 0 && energy > 0));
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
    }, { timeout, key: observerKey, traceSeekWrites, seekControlStep });
    const snapshot = completed => page.evaluate(({ key, completed }) => {
      const state = window[key];
      if (!state) return null;
      try { return state.snapshot(completed); } catch { return null; }
      finally { state.cancel(); delete window[key]; }
    }, { key: observerKey, completed }).catch(() => null);
    if (seekControlStep !== null) {
      let completed = false, diagnostic;
      try {
        await page.getByRole("button", { name: "下一帧", exact: true }).click({ timeout });
        await expect.poll(() => video.evaluate(v => v.paused &&
          Math.abs(v.currentTime - seekControlStep) < .002, undefined, { timeout }), { timeout }).toBe(true);
        completed = true;
      } finally { diagnostic = await snapshot(completed); }
      return diagnostic;
    }
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
      const diagnostic = await snapshot(frame !== undefined);
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
// Run only after existing acceptance has finished. This qualifies a known
// setter label in the real packaged UI, not the timing of a naturally cold seek.
async function verifyNativeSeekControl(page, expect, step, timeout = 5_000) {
  if (!Number.isFinite(step) || step <= 0) return { passed: false, reason: "invalid-frame-duration" };
  const video = page.locator("video.player__video");
  const ruler = page.getByRole("slider", { name: "时间线播放头", exact: true });
  let initial;
  try {
    await ruler.focus({ timeout });
    await ruler.press("Home", { timeout });
    await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow", { timeout })), { timeout }).toBe(0);
    const measure = () => video.evaluate(v => ({
      readyState: v.readyState, paused: v.paused, seeking: v.seeking, time: v.currentTime,
    }), undefined, { timeout });
    await expect.poll(async () => {
      const v = await measure();
      return v.readyState >= 2 && v.paused && !v.seeking && Math.abs(v.time) < .002;
    }, { timeout }).toBe(true);
    initial = await measure();
    if (initial.readyState < 2 || !initial.paused || initial.seeking || !Number.isFinite(initial.time) || Math.abs(initial.time) >= .002)
      return { passed: false, reason: "zero-frame-not-ready" };
  } catch {
    return { passed: false, reason: "zero-frame-not-ready" };
  }
  let diagnostic;
  try {
    diagnostic = await verifyNativePreview(page, expect, { timeout, cycles: 1, traceSeekWrites: true, seekControlStep: step });
  } catch {
    return { passed: false, reason: "step-or-native-cursor-unavailable", initial };
  }
  if (!diagnostic?.callerProbeInstalled || diagnostic.seekCoverage?.status !== "observed")
    return { passed: false, reason: "caller-probe-unavailable-or-incomplete", initial };
  const zero = diagnostic.events.find(event => event.event === "control-zero");
  const end = diagnostic.events.find(event => event.event === "snapshot");
  if (!zero?.paused || zero.seeking || zero.readyState < 2 || !Number.isFinite(zero.time) || Math.abs(zero.time) >= .002)
    return { passed: false, reason: "zero-frame-not-ready-at-observer-start", initial };
  if (zero.node !== end?.node || zero.sourceGeneration !== end?.sourceGeneration)
    return { passed: false, reason: "active-source-changed-during-frame-step", initial };
  const label = diagnostic.seekWrites.find(write => write.caller === "frame-step" &&
    write.active && write.paused && write.node === end?.node && write.sourceGeneration === end?.sourceGeneration &&
    write.ordinal > diagnostic.seekCoverage.startOrdinal && write.ordinal < diagnostic.seekCoverage.snapshotOrdinal &&
    Math.abs(write.requestedTime - step) < 1e-6);
  if (!label) return { passed: false, reason: "expected-frame-step-label-not-observed", initial };
  if (!end.paused || !Number.isFinite(end.time) || Math.abs(end.time - step) >= .002)
    return { passed: false, reason: "stepped-native-cursor-not-held", initial };
  return { passed: true, initial: { readyState: zero.readyState, paused: zero.paused, time: zero.time },
    caller: "frame-step", requestedTime: label.requestedTime,
    observedTime: end.time, node: label.node, sourceGeneration: label.sourceGeneration };
}
module.exports = { verifyNativePreview, verifyNativeSeekControl };
