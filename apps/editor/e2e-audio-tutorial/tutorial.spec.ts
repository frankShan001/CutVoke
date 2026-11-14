import { expect, test } from '@playwright/test';
import type { Clip, Project } from '../src/types/api';
const seconds = (value: { num: string; den: string }) => Number(value.num) / Number(value.den);

test('real tutorial remains editable in the browser', async ({ page, request }, info) => {
  const projectPath = '/api/v1/projects/audio-first';
  const before: Project = await (await request.get(projectPath)).json();
  // This project runs against a separately unpacked database, never the source library.
  if (info.project.name === 'restored-package') {
    const source = before.sequence.tracks.flatMap(track => track.clips).find(clip => clip.id === 'music')!.assetRef.sourcePath;
    expect(source?.replaceAll('\\', '/')).toContain('/roundtrip/');
  }
  expect(before.sequence.captions).toHaveLength(3);
  expect(before.sequence.tracks.filter((t: {kind: string}) => t.kind === 'audio')).toHaveLength(2);
  await page.goto('/');
  await page.getByRole('button', { name: '打开工程 Audio first · editable tutorial', exact: true }).click();
  await page.getByRole('tablist', { name: '创作域' }).getByRole('tab', { name: '字幕', exact: true }).click();
  await page.getByRole('button', { name: '选择字幕 caption-1', exact: true }).click();
  await page.getByRole('textbox', { name: '编辑字幕文字' }).fill('An editable cue');
  await expect.poll(async () => (await (await request.get(projectPath)).json()).sequence.captions.find((c: {id:string}) => c.id === 'caption-1').text).toBe('An editable cue');
  await expect(page.locator('.cv-caption-row__text').filter({ hasText: 'An editable cue' })).toBeVisible();
  const edited = await (await request.get(projectPath)).json();
  const original = before.sequence.captions!.find((c: {id:string}) => c.id === 'caption-1')!;
  const changed = edited.sequence.captions.find((c: {id:string}) => c.id === 'caption-1');
  expect(changed.start).toEqual(original.start); expect(changed.end).toEqual(original.end);
  await page.screenshot({ path: info.outputPath('editable-caption.png'), fullPage: true });
  await page.getByRole('button', { name: /^撤销/ }).first().click();
  await expect.poll(async () => (await (await request.get(projectPath)).json()).sequence.captions.find((c: {id:string}) => c.id === 'caption-1').text).toBe('A small cue');
  // Retain the actual server-rendered PNG, separately from the editor screenshot.
  const frame = await request.get(`${projectPath}/preview-frame?t=2.5&size=640x360`);
  expect(frame.ok()).toBeTruthy();
  const png = await frame.body();
  expect(png.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
  await info.attach('real-preview.png', { body: png, contentType: 'image/png' });
});

for (const field of ['volume', 'fadeIn', 'fadeOut'] as const) {
  test(`audio ${field} edits persist and undo in the actual tutorial`, async ({ page, request }, info) => {
    const projectPath = '/api/v1/projects/audio-first';
    const project = async (): Promise<Project> => {
      const response = await request.get(projectPath);
      expect(response.ok()).toBeTruthy();
      return response.json();
    };
    const clip = (value: Project): Clip => value.sequence.tracks.flatMap(track => track.clips).find(item => item.id === 'music')!;
    const before = await project();
    const original = clip(before);
    const initial = seconds(original[field]!);
    const open = async () => {
      await page.goto('/');
      await page.getByRole('button', { name: '打开工程 Audio first · editable tutorial', exact: true }).click();
      await page.locator('[data-clip-id="music"]').click();
    };
    const control = () => field === 'volume'
      ? page.getByRole('slider', { name: '片段音量', exact: true })
      : page.getByRole('spinbutton', { name: field === 'fadeIn' ? '淡入时长' : '淡出时长', exact: true });
    await open();
    await expect(control()).toHaveValue(String(field === 'volume' ? initial * 100 : initial));
    const changed = field === 'volume' ? initial + .01 : 1.2;
    if (field === 'volume') await control().press('ArrowRight');
    else { await control().fill(String(changed)); await control().press('Tab'); }
    await expect.poll(async () => seconds(clip(await project())[field]!)).toBeCloseTo(changed, 6);
    await expect(control()).toHaveValue(String(field === 'volume' ? Math.round(changed * 100) : changed));
    // Reload waits for the library and opens the same project, proving saved state rather than local input state.
    await open();
    await expect(control()).toHaveValue(String(field === 'volume' ? Math.round(changed * 100) : changed));
    const edited = await project();
    const expected = { ...original, [field]: clip(edited)[field] };
    expect(clip(edited)).toEqual(expected);
    await page.screenshot({ path: info.outputPath(`persisted-${field}.png`), fullPage: true });
    await page.getByRole('button', { name: /^撤销/ }).first().click();
    await expect.poll(async () => clip(await project())).toEqual(original);
    await expect(control()).toHaveValue(String(field === 'volume' ? initial * 100 : initial));
  });
}
