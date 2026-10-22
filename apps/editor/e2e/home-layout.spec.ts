import { expect, test } from '@playwright/test';

test('short desktop home keeps creation reachable after language changes', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.goto('/');
  for (const locale of ['en', 'de', 'ar', 'zh-CN']) {
    await page.getByTestId('language-select').selectOption(locale);
    await expect(page.locator('html')).toHaveAttribute('lang', locale);
    const hero = await page.locator('.home__hero').boundingBox();
    const create = await page.locator('.home__create').first().boundingBox();
    expect(hero!.y + hero!.height).toBeLessThanOrEqual(create!.y);
  }
  await page.getByLabel('新工程名称', { exact: true }).fill(`short-home-${Date.now()}`);
  await page.getByRole('button', { name: '创建工程', exact: true }).click();
  await expect(page.getByRole('heading', { name: '时间线', exact: true })).toBeVisible();
});
