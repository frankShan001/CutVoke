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
    const frame = await video.evaluate(v => {
      const canvas = document.createElement("canvas"); canvas.width = v.videoWidth; canvas.height = v.videoHeight;
      const context = canvas.getContext("2d"); context.drawImage(v, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let energy = 0; for (let i = 0; i < pixels.length; i += 4) energy += pixels[i] + pixels[i + 1] + pixels[i + 2];
      return { width: canvas.width, height: canvas.height, energy, time: v.currentTime };
    });
    expect(frame.width).toBeGreaterThan(0); expect(frame.height).toBeGreaterThan(0); expect(frame.energy).toBeGreaterThan(0);
    await page.getByRole("button", { name: "暂停", exact: true }).click();
    samples.push(frame);
  }
  return samples;
}
module.exports = { verifyNativePreview };
