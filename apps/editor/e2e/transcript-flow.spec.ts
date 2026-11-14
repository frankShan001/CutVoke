import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const r = (n: number, den = 1) => ({ num: String(n), den: String(den) });
const seconds = (value: { num: string; den: string }) => Number(value.num) / Number(value.den);
const path = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`;
async function read(request: APIRequestContext, id: string) { const response = await request.get(path(id)); expect(response.ok()).toBeTruthy(); return response.json(); }
async function apply(request: APIRequestContext, id: string, type: string, payload: object) {
  const project = await read(request, id);
  const response = await request.post(`${path(id)}/commands`, { data: { type, payload, expectedRevision: project.revision,
    commandId: `transcript-${Date.now()}-${Math.random()}`, actor: { kind: "human", id: "synthetic-test" } } });
  expect(response.ok(), await response.text()).toBeTruthy(); return read(request, id);
}
async function fixture(request: APIRequestContext, audioEnd = 5) {
  const source = (await read(request, "e2e-fixture")).sequence.tracks[0].clips[0].assetRef.sourcePath;
  const id = `transcript-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const response = await request.post('/api/v1/projects', { data: { projectId: id, name: id, width: 160, height: 90, fps: 15 } });
  expect(response.status()).toBe(201);
  for (const kind of ['video', 'audio']) {
    await apply(request, id, 'track.add', { trackId: kind, kind });
    await apply(request, id, 'clip.insert', { clipId: `${kind}-clip`, trackId: kind, sourcePath: kind === 'audio' ? fileURLToPath(new URL('../../../src/cutvoke/assets/audio/tech_minimal.wav', import.meta.url)) : source, timelineStart: r(0), timelineEnd: r(kind === 'audio' ? audioEnd : 5) });
  }
  await apply(request, id, 'caption.add', { captionId: 'a', text: '你好，开场句子', start: r(1), end: r(2) });
  await apply(request, id, 'caption.add', { captionId: 'b', text: '你好，重叠句子', start: r(3, 2), end: r(3) });
  await apply(request, id, 'caption.add', { captionId: 'words', text: '你好世界', start: r(3), end: r(4), words: [
    { text: '你好', start: r(3), end: r(7, 2) }, { text: '世界', start: r(7, 2), end: r(4) } ] });
  await apply(request, id, 'marker.add', { markerId: 'end', name: '结尾', time: r(9, 2) });
  return id;
}
async function open(page: Page, id: string) {
  await page.goto('/');
  await page.getByRole('button', { name: `打开工程 ${id}`, exact: true }).click();
  await expect(page.getByRole('heading', { name: '时间线', exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('tablist', { name: '创作域' }).getByRole('tab', { name: '字幕', exact: true }).click();
  const panel = page.getByTestId('transcript-editor');
  await panel.locator('summary').click();
  await expect(panel.getByRole('searchbox')).toBeVisible();
  return panel;
}
const reviewButton = (page: Page) => page.getByTestId('transcript-editor').getByRole('button', { name: '预览删除影响', exact: true });
const confirmButton = (page: Page) => page.getByTestId('transcript-editor').getByRole('button', { name: '确认删除所选内容及画面', exact: true });

// All media and projects are synthetic and isolated by e2e_server.py; no ASR/model calls.
test('transcript search, merged sentence preview and explicit single-command deletion undo all lanes', async ({ page, request }) => {
  const id = await fixture(request); const before = await read(request, id);
  const submitted: Array<{ type: string; dryRun?: boolean }> = [];
  page.on('request', req => { if (req.method() === 'POST' && req.url().endsWith(`${path(id)}/commands`)) submitted.push(req.postDataJSON()); });
  const panel = await open(page, id);
  await panel.getByRole('searchbox').fill('句子');
  await expect(panel.locator('.transcript-editor__sentence')).toHaveCount(2);
  await panel.getByRole('checkbox').nth(0).check(); await panel.getByRole('checkbox').nth(1).check();
  await expect(panel).toContainText('所选范围：1 段，共 2.00 秒（重叠已合并）');
  await panel.getByRole('button', { name: '定位', exact: true }).first().click();
  await expect(page.locator('.player-controls__time')).toContainText('0:01');
  await reviewButton(page).click();
  await expect(confirmButton(page)).toBeVisible();
  await expect(panel.getByRole('region', { name: '删除影响预览' })).toContainText('此预览未修改工程');
  expect((await read(request, id)).sequence).toEqual(before.sequence);
  expect((await read(request, id)).revision).toBe(before.revision);
  expect(submitted.filter(item => item.type === 'transcript.delete')).toEqual([expect.objectContaining({ dryRun: true })]);
  await panel.getByRole('button', { name: '取消', exact: true }).click();
  await expect(confirmButton(page)).toHaveCount(0);
  expect((await read(request, id)).revision).toBe(before.revision);
  await reviewButton(page).click(); await confirmButton(page).click();
  await expect.poll(async () => (await read(request, id)).revision).not.toBe(before.revision);
  await expect(confirmButton(page)).toHaveCount(0);
  const after = await read(request, id);
  expect(after.sequence.tracks.map((track: any) => track.clips.map((clip: any) => [seconds(clip.timelineStart), seconds(clip.timelineEnd), seconds(clip.sourceStart)])))
    .toEqual([[[0, 1, 0], [1, 3, 3]], [[0, 1, 0], [1, 3, 3]]]);
  expect(after.sequence.captions.map((caption: any) => caption.text)).toEqual(['你好世界']);
  expect(seconds(after.sequence.markers[0].time)).toBe(2.5);
  expect(submitted.filter(item => item.type === 'transcript.delete' && !item.dryRun)).toHaveLength(1);
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(async () => (await read(request, id)).sequence).toEqual(before.sequence);
});

test('Chinese word selection locates exact timing and ordinary caption editing never deletes media', async ({ page, request }) => {
  const id = await fixture(request); const panel = await open(page, id); const before = await read(request, id);
  await panel.locator('select').selectOption('words');
  await panel.getByRole('button', { name: '你好', exact: true }).click();
  await expect(page.locator('.player-controls__time')).toContainText('0:03');
  await expect(panel).toContainText('共 0.50 秒');
  await reviewButton(page).click(); await expect(confirmButton(page)).toBeVisible();
  // Changing the word selection invalidates the reviewed command immediately.
  await panel.getByRole('button', { name: '世界', exact: true }).click();
  await expect(confirmButton(page)).toHaveCount(0);
  await panel.getByRole('button', { name: '世界', exact: true }).click();
  await reviewButton(page).click(); await confirmButton(page).click();
  await expect.poll(async () => (await read(request, id)).sequence.captions.find((caption: any) => caption.id === 'words')?.text).toBe('世界');
  await expect(panel.locator('select')).toHaveValue('');
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(async () => (await read(request, id)).sequence).toEqual(before.sequence);
  await page.getByRole('button', { name: '选择字幕 words', exact: true }).click();
  await page.getByRole('textbox', { name: '编辑字幕文字' }).fill('只改文本');
  await page.getByRole('textbox', { name: '编辑字幕文字' }).press('Tab');
  await expect.poll(async () => (await read(request, id)).sequence.captions.find((caption: any) => caption.id === 'words')?.text).toBe('只改文本');
  expect((await read(request, id)).sequence.tracks).toEqual(before.sequence.tracks);
});

for (const delayedFrames of [false, true]) {
test(`transcript audition stops actual video and native audio at the selected word boundary${delayedFrames ? ' with delayed animation frames' : ''}`, async ({ page, request }) => {
  await page.addInitScript(() => {
    const captured: HTMLAudioElement[] = []; (window as any).__transcriptAudio = captured;
    const Original = window.Audio;
    (window as any).__nativePauseTimes = [];
    (window as any).__nativePlayTimes = [];
    window.Audio = new Proxy(Original, { construct(target, args) {
      const audio = Reflect.construct(target, args) as HTMLAudioElement;
      const pause = audio.pause.bind(audio), play = audio.play.bind(audio);
      audio.play = () => { (window as any).__nativePlayTimes.push(audio.currentTime); return play(); };
      audio.pause = () => { if (!audio.paused) (window as any).__nativePauseTimes.push(audio.currentTime); pause(); };
      captured.push(audio); return audio;
    } });
    (window as any).__transport = [];
    window.addEventListener('cutvoke:transport-clock', event => (window as any).__transport.push((event as CustomEvent).detail));
  });
  const id = await fixture(request); const panel = await open(page, id);
  await expect(page.getByTestId('live-audio-meter')).toHaveAttribute('data-ready', 'true', { timeout: 20_000 });
  if (delayedFrames) await page.evaluate(() => {
    // Delay render callbacks, not timers or the audio device. A bounded
    // audition must not rely on the screen's refresh cadence to stop sound.
    const raf = window.requestAnimationFrame.bind(window);
    const cancel = window.cancelAnimationFrame.bind(window);
    const delayed = new Map<number, number>();
    window.requestAnimationFrame = callback => {
      const id = raf(() => { delayed.set(id, window.setTimeout(() => { delayed.delete(id); callback(performance.now()); }, 250)); });
      return id;
    };
    window.cancelAnimationFrame = id => { cancel(id); const timer = delayed.get(id); if (timer !== undefined) clearTimeout(timer); delayed.delete(id); };
  });
  await panel.locator('select').selectOption('words'); await panel.getByRole('button', { name: '你好', exact: true }).click();
  await panel.getByRole('button', { name: '试听 3.00–3.50 秒', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__transcriptAudio.some((audio: HTMLAudioElement) => !audio.paused && audio.currentTime >= 3)), { intervals: [10, 20, 30] }).toBe(true);
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '播放');
  await expect.poll(() => page.evaluate(() => (window as any).__transcriptAudio.every((audio: HTMLAudioElement) => audio.paused))).toBe(true);
  await expect.poll(() => page.evaluate(() => (window as any).__transport.at(-1)?.time)).toBe(3.5);
  await expect(page.locator('.statusbar__msg')).toHaveText('已暂停');
  const stopped = await page.evaluate(() => ({ audio: (window as any).__transcriptAudio.map((audio: HTMLAudioElement) => audio.currentTime),
    submitted: (window as any).__transport.filter((value: any) => value.playing && value.time >= 3).map((value: any) => value.time),
    nativePauses: (window as any).__nativePauseTimes.filter((time: number) => time >= 3),
    nativeStarts: (window as any).__nativePlayTimes.filter((time: number) => time >= 3) }));
  expect(stopped.submitted.some((time: number) => time > 3.1)).toBe(true);
  expect(Math.max(...stopped.submitted)).toBeLessThan(3.58);
  expect(stopped.audio.every((time: number) => Math.abs(time - 3.5) < .1)).toBe(true);
  expect(stopped.nativeStarts.length).toBeGreaterThan(0);
  expect(Math.min(...stopped.nativeStarts)).toBeLessThan(3.08);
  expect(stopped.nativePauses.length).toBeGreaterThan(0);
  expect(Math.max(...stopped.nativePauses)).toBeLessThan(3.58);
  await page.screenshot({ path: '../../.artifacts/transcript-word-review.png' });
  // Starting a longer audition, then an explicit seek, stops every audio voice too.
  await panel.locator('.transcript-editor__sentence').filter({ hasText: '你好，开场句子' }).getByRole('button', { name: '试听', exact: true }).click();
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '暂停');
  await panel.locator('.transcript-editor__sentence').filter({ hasText: '你好世界' }).getByRole('button', { name: '定位', exact: true }).click();
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '播放');
  await expect.poll(() => page.evaluate(() => (window as any).__transcriptAudio.every((audio: HTMLAudioElement) => audio.paused))).toBe(true);
  await panel.locator('.transcript-editor__sentence').filter({ hasText: '你好，开场句子' }).getByRole('button', { name: '试听', exact: true }).click();
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '暂停');
  await page.locator('.player__play').click();
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '播放');
  await expect(page.locator('.statusbar__msg')).toHaveText('已暂停');
  await expect.poll(() => page.evaluate(() => (window as any).__transcriptAudio.every((audio: HTMLAudioElement) => audio.paused))).toBe(true);
});

}

test('locked downstream track blocks transcript preview and deletion', async ({ page, request }) => {
  const id = await fixture(request); await apply(request, id, 'track.update', { trackId: 'audio', locked: true });
  const before = await read(request, id); const panel = await open(page, id);
  await panel.getByRole('checkbox').first().check();
  await expect(reviewButton(page)).toBeDisabled(); await expect(panel).toContainText('所选范围会影响锁定轨道');
  expect((await read(request, id)).sequence).toEqual(before.sequence);
});

test('late read-only trial is discarded after revision changes', async ({ page, request }) => {
  const id = await fixture(request); const panel = await open(page, id); let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; }); let received!: () => void; const ready = new Promise<void>(resolve => { received = resolve; });
  await page.route(`**${path(id)}/commands`, async route => {
    if (route.request().postDataJSON().dryRun) { const response = await route.fetch(); received(); await gate; await route.fulfill({ response }); }
    else await route.continue();
  });
  await panel.getByRole('checkbox').first().check(); await reviewButton(page).click(); await ready;
  await apply(request, id, 'caption.add', { captionId: 'new', text: '并发更新', start: r(4), end: r(5) });
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.locator('.cv-caption-row__text').filter({ hasText: '并发更新' })).toBeVisible();
  release(); await expect(reviewButton(page)).toBeEnabled();
  await expect(confirmButton(page)).toHaveCount(0); await expect(panel).toContainText('工程或选择已变化');
});

test('revision conflict during confirmed transcript deletion is never retried', async ({ page, request }) => {
  const id = await fixture(request); const panel = await open(page, id); const before = await read(request, id); let attempts = 0;
  await page.route(`**${path(id)}/commands`, async route => {
    const body = route.request().postDataJSON();
    if (body.type === 'transcript.delete' && !body.dryRun) { attempts++;
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: { code: 'REVISION_CONFLICT', message: 'Concurrent edit', retryable: true } }) });
    } else await route.continue();
  });
  await panel.getByRole('checkbox').first().check(); await reviewButton(page).click(); await confirmButton(page).click();
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('alertdialog').getByRole('button', { name: '关闭', exact: true }).click();
  await expect(confirmButton(page)).toHaveCount(0); expect(attempts).toBe(1);
  expect((await read(request, id)).sequence).toEqual(before.sequence);
});

test('transcript preview waits for pending caption saves and rejects changed selected timing', async ({ page, request }) => {
  const id = await fixture(request); const panel = await open(page, id);
  await panel.getByRole('checkbox').first().check();
  await page.getByRole('button', { name: '选择字幕 a', exact: true }).click();
  let release!: () => void, received!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { received = resolve; });
  let trials = 0;
  await page.route(`**${path(id)}/commands`, async route => {
    const body = route.request().postDataJSON();
    if (body.type === 'caption.update') { received(); await gate; await route.continue(); }
    else { if (body.dryRun) trials++; await route.continue(); }
  });
  await page.getByRole('textbox', { name: '编辑字幕文字' }).fill('正在保存的新句子');
  await ready;
  await reviewButton(page).click();
  expect(trials).toBe(0);
  release();
  await expect.poll(async () => (await read(request, id)).sequence.captions.find((caption: any) => caption.id === 'a')?.text).toBe('正在保存的新句子');
  await expect(reviewButton(page)).toBeDisabled();
  await expect(confirmButton(page)).toHaveCount(0);
  expect(trials).toBe(0);
});

test('a lease acquired after trial blocks confirmed transcript deletion', async ({ page, request }) => {
  const id = await fixture(request); const panel = await open(page, id); const before = await read(request, id);
  await panel.getByRole('checkbox').first().check(); await reviewButton(page).click(); await expect(confirmButton(page)).toBeVisible();
  const response = await request.post(`${path(id)}/edit-lock`, { data: { owner: 'synthetic-agent', ttlSeconds: 30 } });
  expect(response.ok()).toBeTruthy(); const lease = (await response.json()).lease;
  try {
    await expect(confirmButton(page)).toBeDisabled({ timeout: 10_000 });
    expect((await read(request, id)).sequence).toEqual(before.sequence);
  } finally {
    expect((await request.post(`${path(id)}/edit-lock/release`, { data: { leaseId: lease.leaseId } })).ok()).toBeTruthy();
  }
});

test('audition ignores prior loop bounds and stops detached audio synchronously when hidden', async ({ page, request }) => {
  await page.addInitScript(() => {
    const elements: HTMLAudioElement[] = []; (window as any).__transcriptAudio = elements;
    window.Audio = new Proxy(window.Audio, { construct(target, args) { const audio = Reflect.construct(target, args); elements.push(audio); return audio; } });
    const history: Array<{ time: number; playing: boolean; audioRunning: boolean }> = [];
    (window as any).__auditionHistory = history;
    window.addEventListener('cutvoke:transport-clock', event => {
      history.push({ ...(event as CustomEvent).detail, audioRunning: elements.some(audio => !audio.paused) });
    });
  });
  const id = await fixture(request); const panel = await open(page, id);
  await expect(page.getByTestId('live-audio-meter')).toHaveAttribute('data-ready', 'true', { timeout: 20_000 });
  await panel.locator('select').selectOption('words'); await panel.getByRole('button', { name: '你好', exact: true }).click();
  await page.getByRole('button', { name: '设循环入点', exact: true }).click();
  await page.getByRole('button', { name: '下一帧', exact: true }).click();
  await page.getByRole('button', { name: '设循环出点', exact: true }).click();
  await page.getByRole('button', { name: '循环播放区间', exact: true }).click();
  await expect(page.getByRole('button', { name: '循环播放区间', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await panel.getByRole('button', { name: '试听 3.00–3.50 秒', exact: true }).click();
  await expect(page.getByRole('button', { name: '循环播放区间', exact: true })).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => page.evaluate(() => (window as any).__auditionHistory.some((sample: any) =>
    sample.playing && sample.audioRunning && sample.time >= 3 && sample.time < 3.5))).toBe(true);
  await expect.poll(() => page.evaluate(() => (window as any).__auditionHistory.at(-1)?.time)).toBe(3.5);
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '播放');
  await expect(page.locator('.player-controls__time')).toContainText('0:03.500');
  await expect.poll(() => page.evaluate(() => (window as any).__transcriptAudio.every((audio: HTMLAudioElement) => audio.paused))).toBe(true);
  const lastRunningTime = await page.evaluate(() => Math.max(...(window as any).__auditionHistory
    .filter((sample: any) => sample.playing && sample.time >= 3).map((sample: any) => sample.time)));
  expect(lastRunningTime).toBeLessThan(3.58);
  // Dispatch while visible, with the standard hidden predicate, so this proves
  // synchronous voice pausing during active audio, without an automation round
  // trip racing a short audition's completion.
  await page.evaluate(() => {
    (window as any).__hiddenStopVerified = new Promise<boolean>(resolve => {
      const hideWhileRunning = (event: Event) => {
        const sample = (event as CustomEvent).detail;
        const audio = (window as any).__transcriptAudio as HTMLAudioElement[];
        if (!sample.playing || sample.time < 1.1 || sample.time >= 1.8 || !audio.some(element => !element.paused)) return;
        window.removeEventListener('cutvoke:transport-clock', hideWhileRunning);
        Object.defineProperty(document, 'hidden', { configurable: true, value: true });
        document.dispatchEvent(new Event('visibilitychange'));
        resolve(audio.every(element => element.paused));
      };
      window.addEventListener('cutvoke:transport-clock', hideWhileRunning);
    });
  });
  await panel.locator('.transcript-editor__sentence').first().getByRole('button', { name: '试听', exact: true }).click();
  const pausedImmediately = await page.evaluate(() => (window as any).__hiddenStopVerified);
  expect(pausedImmediately).toBe(true);
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '播放');
});

test('obsolete transcript trial cannot block or clear a newer trial after sequence switch', async ({ page, request }) => {
  const id = await fixture(request); const initial = await read(request, id);
  await apply(request, id, 'sequence.add', { sequenceId: 'second' });
  await apply(request, id, 'caption.add', { captionId: 'second-caption', text: '第二序列', start: r(1), end: r(2) });
  await apply(request, id, 'sequence.switch', { sequenceId: initial.sequence.id });
  const panel = await open(page, id);
  let count = 0;
  const releases: Array<() => void> = [], arrivals: Array<Promise<void>> = [];
  const ready: Array<() => void> = [];
  for (let i = 0; i < 2; i++) arrivals.push(new Promise<void>(resolve => { ready[i] = resolve; }));
  await page.route(`**${path(id)}/commands`, async route => {
    if (!route.request().postDataJSON().dryRun) return route.continue();
    const index = count++; const response = await route.fetch();
    const gate = new Promise<void>(resolve => { releases[index] = resolve; }); ready[index]();
    await gate; await route.fulfill({ response });
  });
  await panel.getByRole('checkbox').first().check(); await reviewButton(page).click(); await arrivals[0];
  await apply(request, id, 'sequence.switch', { sequenceId: 'second' });
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(panel.locator('.transcript-editor__sentence').filter({ hasText: '第二序列' })).toBeVisible();
  await expect(panel.locator('.transcript-editor__sentence')).toHaveCount(1);
  await expect(panel.getByRole('checkbox')).toBeEnabled();
  await panel.getByRole('checkbox').check(); await reviewButton(page).click(); await arrivals[1];
  const obsoleteDelivered = page.waitForResponse(response => response.url().endsWith(`${path(id)}/commands`) &&
    response.request().postDataJSON()?.payload?.captionIds?.includes('a'));
  releases[0]();
  // Wait for the obsolete response to reach the browser before checking ownership.
  await obsoleteDelivered;
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  await expect(reviewButton(page)).toBeDisabled(); await expect(confirmButton(page)).toHaveCount(0);
  await expect(panel).not.toContainText('工程或选择已变化');
  releases[1]();
  await expect(confirmButton(page)).toBeVisible();
});

test('a user keyboard seek within an audition cancels native audio before its boundary', async ({ page, request }) => {
  await page.addInitScript(() => {
    const elements: HTMLAudioElement[] = []; (window as any).__transcriptAudio = elements;
    window.Audio = new Proxy(window.Audio, { construct(target, args) { const audio = Reflect.construct(target, args); elements.push(audio); return audio; } });
    (window as any).__transport = [];
    window.addEventListener('cutvoke:transport-clock', event => (window as any).__transport.push((event as CustomEvent).detail));
  });
  const id = await fixture(request); const panel = await open(page, id);
  await expect(page.getByTestId('live-audio-meter')).toHaveAttribute('data-ready', 'true', { timeout: 20_000 });
  await page.evaluate(() => {
    (window as any).__keyboardSeek = new Promise<number>(resolve => {
      const seekWhileRunning = (event: Event) => {
        const sample = (event as CustomEvent).detail;
        const audio = (window as any).__transcriptAudio as HTMLAudioElement[];
        if (!sample.playing || sample.time < 1.1 || sample.time >= 1.8 || !audio.some(element => !element.paused)) return;
        const ruler = document.querySelector<HTMLElement>('.timeline-ruler')!;
        const before = Number(ruler.getAttribute('aria-valuenow'));
        // The transport event precedes the React commit. Keyboard navigation
        // uses the rendered playhead, so wait until that value is in the same
        // audition window rather than assuming it already matches the clock.
        if (!Number.isFinite(before) || before < 1.1 || before >= 1.8) return;
        window.removeEventListener('cutvoke:transport-clock', seekWhileRunning);
        ruler.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
        resolve(before + 1 / 15);
      };
      window.addEventListener('cutvoke:transport-clock', seekWhileRunning);
    });
  });
  await panel.locator('.transcript-editor__sentence').first().getByRole('button', { name: '试听', exact: true }).click();
  const expected = await page.evaluate(() => (window as any).__keyboardSeek);
  expect(expected).toBeGreaterThan(1.1); expect(expected).toBeLessThan(1.9);
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '播放');
  await expect.poll(() => page.evaluate(() => (window as any).__transport.at(-1)?.time)).toBeCloseTo(expected, 4);
  await expect.poll(() => page.evaluate(() => (window as any).__transcriptAudio.every((audio: HTMLAudioElement) => audio.paused))).toBe(true);
});

for (const cancellation of ['pause', 'seek'] as const) {
  test(`native startup held after real play settles cannot revive audio after ${cancellation}`, async ({ page, request }) => {
    await page.addInitScript(() => {
      const audio: HTMLAudioElement[] = [];
      (window as any).__heldStartupAudio = audio;
      (window as any).__requestedStartupCount = 0;
      (window as any).__heldStartupCount = 0;
      (window as any).__releasedStartupCount = 0;
      const gate = new Promise<void>(resolve => { (window as any).__releaseStartup = resolve; });
      window.Audio = new Proxy(window.Audio, { construct(target, args) {
        const element = Reflect.construct(target, args) as HTMLAudioElement;
        const play = element.play.bind(element);
        element.play = async () => {
          (window as any).__requestedStartupCount++;
          await play(); // Actual media playback; only its acknowledgement is delayed.
          (window as any).__heldStartupCount++;
          await gate;
          (window as any).__releasedStartupCount++;
        };
        audio.push(element); return element;
      } });
    });
    const id = await fixture(request); const panel = await open(page, id);
    await expect(page.getByTestId('live-audio-meter')).toHaveAttribute('data-ready', 'true', { timeout: 20_000 });
    await panel.locator('.transcript-editor__sentence').first().getByRole('button', { name: '试听', exact: true }).click();
    await expect.poll(() => page.evaluate(() => {
      const w = window as any;
      return w.__requestedStartupCount > 0 && w.__heldStartupCount === w.__requestedStartupCount;
    }), { intervals: [10, 20] }).toBe(true);
    const cancelButton = cancellation === 'pause' ? page.locator('.player__play')
      : panel.locator('.transcript-editor__sentence').filter({ hasText: '你好世界' }).getByRole('button', { name: '定位', exact: true });
    // Check pending and dispatch cancellation in ONE browser task. The 2s
    // timeout cannot interleave between this evidence and the actual action.
    const pending = await cancelButton.evaluate(button => {
      const w = window as any, audio = w.__heldStartupAudio as HTMLAudioElement[];
      const snapshot = { requested: w.__requestedStartupCount, held: w.__heldStartupCount,
        released: w.__releasedStartupCount, pending: audio.some(e => !e.paused && e.muted),
        safe: audio.every(e => e.paused || e.muted), playing: document.querySelector('.player__play')?.getAttribute('aria-label') };
      if (!snapshot.pending || !snapshot.safe || snapshot.playing !== '暂停' || snapshot.requested <= 0 ||
        snapshot.held !== snapshot.requested || snapshot.released !== 0) throw new Error(`startup no longer pending: ${JSON.stringify(snapshot)}`);
      (button as HTMLElement).click();
      return snapshot;
    });
    expect(pending.held).toBeGreaterThan(0);
    await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '播放');
    if (cancellation === 'seek') await expect(page.locator('.timeline-ruler')).toHaveAttribute('aria-valuenow', '3');
    await page.evaluate(() => (window as any).__releaseStartup());
    await expect.poll(() => page.evaluate(() => ({ requested: (window as any).__requestedStartupCount,
      held: (window as any).__heldStartupCount, released: (window as any).__releasedStartupCount })))
      .toEqual({ requested: pending.requested, held: pending.held, released: pending.held });
    await expect.poll(() => page.evaluate(() => (window as any).__heldStartupAudio.every((element: HTMLAudioElement) => element.paused))).toBe(true);
    // Check later render turns after ALL held acknowledgements settle.
    const stable = await page.evaluate(async () => {
      const samples = [];
      for (let i = 0; i < 8; i++) {
        await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
        const w = window as any;
        samples.push({ requested: w.__requestedStartupCount, held: w.__heldStartupCount, released: w.__releasedStartupCount,
          paused: (w.__heldStartupAudio as HTMLAudioElement[]).every(e => e.paused),
          label: document.querySelector('.player__play')?.getAttribute('aria-label'),
          time: Number(document.querySelector('.timeline-ruler')?.getAttribute('aria-valuenow')) });
      }
      return samples;
    });
    for (const sample of stable) {
      expect(sample).toMatchObject({ requested: pending.requested, held: pending.held, released: pending.held, paused: true, label: '播放' });
      if (cancellation === 'seek') expect(sample.time).toBe(3);
    }
  });
}


test('native audio survives video to image to video recovery without a new seek or source', async ({ page, request }) => {
  await page.addInitScript(() => {
    const audios: HTMLAudioElement[] = [];
    (window as any).__roundtripAudios = audios;
    const Original = window.Audio;
    window.Audio = new Proxy(Original, { construct(target, args) {
      const audio = Reflect.construct(target, args) as HTMLAudioElement;
      audios.push(audio); return audio;
    } });
    (window as any).__roundtripClock = null;
    window.addEventListener('cutvoke:transport-clock', event => {
      (window as any).__roundtripClock = (event as CustomEvent).detail;
    });
  });
  const id = await fixture(request, 12);
  const persisted = await read(request, id);
  const audioSource = persisted.sequence.tracks.find((t: any) => t.id === 'audio').clips[0].assetRef.sourcePath as string;
  await open(page, id);
  // Directory-backed projects import assets under content-hash filenames.
  // Identify this exact persisted source, not the original library basename.
  await page.evaluate(source => { (window as any).__roundtripSource = source; }, audioSource);
  const video = page.locator('video.player__video');
  const meter = page.getByTestId('live-audio-meter');
  await expect(meter).toHaveAttribute('data-ready', 'true', { timeout: 20_000 });
  await expect.poll(() => video.evaluate(v => (v as HTMLVideoElement).readyState)).toBeGreaterThan(1);
  await page.getByRole('button', { name: '播放', exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__roundtripAudios.some((a: HTMLAudioElement) =>
    a.src && new URL(a.src).searchParams.get('path') === (window as any).__roundtripSource && !a.paused && !a.muted && a.currentTime > .1))).toBe(true);
  // Inject only the decoder failure signal. Audio elements, AudioContext,
  // React mode transitions and the subsequent visual edit remain real.
  await video.evaluate(v => {
    (window as any).__roundtripVoice = (window as any).__roundtripAudios.find((a: HTMLAudioElement) => a.src && new URL(a.src).searchParams.get('path') === (window as any).__roundtripSource);
    Object.defineProperty(v, 'error', { configurable: true, value: { code: 3, message: 'controlled decoder failure' } });
    v.dispatchEvent(new Event('error'));
  });
  await expect(video).toHaveCount(0);
  await expect(page.locator('.player__play')).toHaveAttribute('aria-label', '暂停');
  await expect.poll(() => page.evaluate(() => {
    const a = (window as any).__roundtripVoice as HTMLAudioElement;
    return !a.paused && !a.muted;
  })).toBe(true);
  const imageCursor = await page.evaluate(() => (window as any).__roundtripVoice.currentTime as number);
  const before = await read(request, id);
  // Only a visual effect changes. No transport command, source, audio mapping,
  // project identity or explicit seek changes across this recovery.
  await apply(request, id, 'effect.add', { clipId: 'video-clip', effectId: 'cutvoke.fx.vibrance', params: { intensity: .25 } });
  await expect(video).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => {
    const a = (window as any).__roundtripVoice as HTMLAudioElement;
    const clock = (window as any).__roundtripClock;
    return !a.paused && !a.muted && clock?.playing && clock.time > 0;
  })).toBe(true);
  await expect.poll(() => page.evaluate(() => Number(document.querySelector('[data-testid="live-audio-meter"]')?.getAttribute('data-rms')))).toBeGreaterThan(.001);
  const resumed = await page.evaluate(() => ({ time: (window as any).__roundtripVoice.currentTime as number,
    transport: (window as any).__roundtripClock.time as number }));
  expect(resumed.time).toBeGreaterThanOrEqual(imageCursor - .15);
  await expect.poll(() => page.evaluate(() => (window as any).__roundtripVoice.currentTime as number)).toBeGreaterThan(resumed.time + .1);
  await expect.poll(() => page.evaluate(() => (window as any).__roundtripClock.time as number)).toBeGreaterThan(resumed.transport + .1);
  const samples = await page.evaluate(async () => {
    const result: boolean[] = [];
    for (let i = 0; i < 8; i++) {
      await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
      const a = (window as any).__roundtripVoice as HTMLAudioElement;
      result.push(!a.paused && !a.muted && (window as any).__roundtripClock.playing);
    }
    return result;
  });
  expect(samples).toEqual(Array(8).fill(true));
  expect((await read(request, id)).sequence.tracks.find((t: any) => t.id === 'audio')).toEqual(before.sequence.tracks.find((t: any) => t.id === 'audio'));
  await page.getByRole('button', { name: '暂停', exact: true }).click();
});
