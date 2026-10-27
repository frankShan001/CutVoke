import { expect, test } from '@playwright/test';

test('duplicate effect controls change and remove only their own instance', async ({ page, request }) => {
  const seed = await (await request.get('/api/v1/projects/e2e-fixture')).json();
  const sourcePath = seed.sequence.tracks[0].clips[0].assetRef.sourcePath;
  const id = `instances-${Date.now()}`, name = `特效实例 ${id}`;
  const path = `/api/v1/projects/${id}`;
  expect((await request.post('/api/v1/projects', { data: { projectId: id, name, width: 160, height: 90, fps: 15 } })).ok()).toBeTruthy();
  const read = async () => (await request.get(path)).json();
  const apply = async (type: string, payload: object) => {
    const project = await read();
    const response = await request.post(`${path}/commands`, { data: { type, payload, expectedRevision: project.revision,
      commandId: `${id}-${Math.random()}`, actor: { kind: 'human', id: 'instances-test' } } });
    expect(response.ok(), await response.text()).toBeTruthy();
  };
  await apply('clip.insert', { trackId: 'v', createTrackKind: 'video', clipId: 'c', sourcePath,
    timelineStart: { num: '0', den: '1' }, timelineEnd: { num: '3', den: '1' } });
  for (const sigma of [2, 8]) await apply('effect.add', { clipId: 'c', effectId: 'cutvoke.fx.blur', params: { sigma } });
  const effects = async () => (await read()).sequence.tracks[0].clips[0].effects;
  const [first, second] = await effects();
  await page.goto('/');
  await page.getByRole('button', { name: `打开工程 ${name}` }).click();
  await page.locator('.clip-block').first().click();
  const one = page.locator(`[data-effect-instance-id="${first.instanceId}"]`);
  const two = page.locator(`[data-effect-instance-id="${second.instanceId}"]`);
  await expect(one).toBeVisible(); await expect(two).toBeVisible();
  await two.locator('summary').click();
  await two.getByRole('button', { name: '上移', exact: true }).click();
  await expect.poll(async () => (await effects())[0].instanceId).toBe(second.instanceId);
  await two.getByRole('button', { name: '下移', exact: true }).click();
  await expect.poll(async () => (await effects())[1].instanceId).toBe(second.instanceId);
  await two.getByRole('spinbutton', { name: '强度', exact: true }).fill('5');
  await two.getByRole('spinbutton', { name: '强度', exact: true }).blur();
  await expect.poll(async () => (await effects())[1].params.sigma).toBe(5);
  expect((await effects())[0].params.sigma).toBe(2);
  await two.getByRole('checkbox').uncheck();
  await expect.poll(async () => (await effects())[1].enabled).toBe(false);
  expect((await effects())[0].enabled).not.toBe(false);
  await two.getByRole('button', { name: /删除效果/ }).click();
  await expect.poll(async () => (await effects()).map((effect: any) => effect.instanceId)).toEqual([first.instanceId]);
  expect((await effects())[0].params.sigma).toBe(2);
  await page.getByRole('button', { name: '撤销', exact: true }).click();
  await expect.poll(async () => (await effects()).map((effect: any) => effect.instanceId)).toEqual([first.instanceId, second.instanceId]);
});
