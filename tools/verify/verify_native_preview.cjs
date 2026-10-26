// Shared by browser regression coverage and all four packaged-app acceptances.
async function verifyNativePreview(page, expect, { timeout = 90_000, cycles = 3 } = {}) {
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
    await page.getByRole("button", { name: "播放", exact: true }).click();
    await expect.poll(() => video.evaluate(v => v.currentTime), { timeout }).toBeGreaterThan(.25);
    await expect(video).toBeVisible();
    await expect.poll(async () => Number(await ruler.getAttribute("aria-valuenow")), { timeout }).toBeGreaterThan(.25);
    // HAVE_ENOUGH_DATA and the audio clock can precede compositor submission,
    // particularly on Intel macOS. Read the first submitted playing frame,
    // never poll until pixels happen to become nonblack. Follow a legitimate
    // pool/source handoff while waiting, rejecting callbacks from old sources.
    const frame = await page.evaluate(({ timeout }) => new Promise((resolve, reject) => {
      let target = null, source = "", callback = null, generation = 0, done = false;
      const cleanup = () => {
        done = true; generation++;
        clearInterval(watch); clearTimeout(deadline);
        if (target && callback !== null) target.cancelVideoFrameCallback(callback);
      };
      const arm = () => {
        if (done) return;
        const current = document.querySelector("video.player__video");
        if (!current || current.paused || current.seeking) return;
        if (typeof current.requestVideoFrameCallback !== "function") {
          cleanup(); reject(new Error("Preview compositor frame callbacks unavailable")); return;
        }
        if (current === target && current.currentSrc === source && callback !== null) return;
        if (target && callback !== null) target.cancelVideoFrameCallback(callback);
        target = current; source = current.currentSrc;
        const registered = ++generation;
        callback = current.requestVideoFrameCallback((_, metadata) => {
          if (done || registered !== generation) return;
          callback = null;
          if (current !== document.querySelector("video.player__video") || current.currentSrc !== source ||
              current.paused || current.seeking || metadata.mediaTime <= .25) { arm(); return; }
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
      const deadline = setTimeout(() => { cleanup(); reject(new Error("No submitted playing preview frame")); }, timeout);
      arm();
    }), { timeout });
    expect(frame.width).toBeGreaterThan(0); expect(frame.height).toBeGreaterThan(0);
    expect(frame.energy, JSON.stringify(frame)).toBeGreaterThan(0);
    await page.getByRole("button", { name: "暂停", exact: true }).click();
    samples.push(frame);
  }
  return samples;
}
module.exports = { verifyNativePreview };
