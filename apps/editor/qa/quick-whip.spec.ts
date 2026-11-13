import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const effectId = 'cutvoke.transition.community.quick-whip';
test('opt-in Quick Whip lists, applies, previews, saves and survives undo/redo/reopen', async ({ page, request }) => {
  const id = `quick-whip-${Date.now()}`;
  const path = `/api/v1/projects/${id}`;
  const read = async () => (await request.get(path)).json();
  const command = async (type: string, payload: object) => {
    const project = await read();
    const result = await request.post(`${path}/commands`, { data: {
      type, payload, commandId: crypto.randomUUID(), expectedRevision: project.revision,
      actor: { kind: 'human', id: 'quick-whip-browser-test' },
    } });
    expect(result.ok(), await result.text()).toBeTruthy();
  };
  const fixture = await (await request.get('/api/v1/projects/e2e-fixture')).json();
  const created = await request.post('/api/v1/projects', { data: {
    projectId: id, name: id, width: 160, height: 90, fps: 15,
  } });
  expect(created.status()).toBe(201);
  await command('track.add', { trackId: 'v1', kind: 'video' });
  for (let index = 0; index < 2; index++) {
    await command('clip.insert', { trackId: 'v1', clipId: `color-${index}`,
      sourcePath: fixture.sequence.tracks[0].clips[index].assetRef.sourcePath,
      timelineStart: { num: index * 5, den: 1 }, timelineEnd: { num: (index + 1) * 5, den: 1 },
    });
  }
  const open = async (hasTransition = false) => {
    await page.goto('/');
    await page.getByRole('button', { name: `打开工程 ${id}` }).click();
    await expect(page.getByRole('heading', { name: '时间线' })).toBeVisible();
    if (hasTransition) await page.locator('.transition-marker__select').click();
    else await page.getByRole('button', { name: '在 5.00 秒的片段接缝添加转场' }).click();
    await expect(page.getByLabel('后一段片段（转场挂此段）')).toHaveValue('color-1');
  };
  const effect = async () => (await read()).sequence.tracks[0].clips[1].effects.find((e: any) => e.effectId === effectId);
  await open();
  await page.getByRole('button', { name: '到左侧选择转场', exact: true }).click();
  const card = page.locator(`.resource-effect-library:visible .resource-card[data-effect-id="${effectId}"]`);
  await expect(card).toBeVisible();
  await expect(card).toContainText('快速甩动');
  await card.press('Enter');
  await expect.poll(async () => (await effect())?.params.duration).toBe(0.25);
  const badge = page.locator('.zone-right .cv-badge--info');
  await expect(badge).toHaveText('快速甩动');
  const duration = page.locator('.zone-right input[type="number"][min="0.1"][max="0.6"]');
  await expect(duration).toHaveValue('0.25');
  await expect(duration).toHaveAttribute('step', '0.05');
  await page.getByRole('button', { name: '预览当前转场' }).click();
  const frame = page.locator('.resource-preview img');
  await expect(frame).toBeVisible({ timeout: 20_000 });
  await expect.poll(() => frame.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  mkdirSync('../../.artifacts/quick-whip-ui', { recursive: true });
  await page.screenshot({ path: '../../.artifacts/quick-whip-ui/applied-preview.png' });
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(effect).toBeUndefined();
  await expect(badge).toHaveCount(0);
  await page.getByRole('button', { name: '重做', exact: true }).click();
  await expect.poll(async () => (await effect())?.params.duration).toBe(0.25);
  await expect(badge).toHaveText('快速甩动');
  await duration.fill('0.6');
  await page.getByRole('button', { name: '保存转场参数' }).click();
  await expect.poll(async () => (await effect())?.params.duration).toBe(0.6);
  await expect(duration).toHaveValue('0.6');
  await open(true);
  await expect(badge).toHaveText('快速甩动');
  await expect(duration).toHaveValue('0.6');
  await page.screenshot({ path: '../../.artifacts/quick-whip-ui/reopened.png' });
});
