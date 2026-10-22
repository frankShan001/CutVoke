import { expect, test } from '@playwright/test';

test('desktop first-run setup must be saved before creating a project', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.addInitScript(() => {
    const language = { preference: 'zh-CN', locale: 'zh-CN', systemLanguages: ['zh-CN'] } as const;
    let setupComplete = false;
    window.cutvokeDesktop = {
      initialLanguage: language,
      getLanguageState: () => language,
      setLanguagePreference: async () => language,
      settings: async () => ({ setupComplete, projectDirectory: '/test-projects', projectLocationManaged: true }),
      saveSettings: async () => { setupComplete = true; return { restartRequired: false }; },
    } as NonNullable<Window['cutvokeDesktop']>;
  });
  await page.goto('/');
  const setup = page.getByRole('dialog', { name: '欢迎使用 CutVoke' });
  await expect(setup).toBeVisible();
  await expect(page.getByRole('button', { name: '关闭设置', exact: true })).toHaveCount(0);
  await setup.getByRole('button', { name: '保存并开始使用', exact: true }).click();
  await expect(setup).not.toBeVisible();
  expect(await page.evaluate(async () => (await window.cutvokeDesktop!.settings()).setupComplete)).toBe(true);
  for (const locale of ['en', 'de', 'ar', 'zh-CN']) {
    await page.getByTestId('language-select').selectOption(locale);
    await expect(page.locator('html')).toHaveAttribute('lang', locale);
  }
  await page.getByLabel('新工程名称', { exact: true }).fill(`short-home-${Date.now()}`);
  await page.getByRole('button', { name: '创建工程', exact: true }).click();
  await expect(page.getByRole('heading', { name: '时间线', exact: true })).toBeVisible();
});
