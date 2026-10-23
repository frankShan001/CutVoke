import { expect, test } from '@playwright/test';

test('desktop GPU acceleration defaults on, saves off and is restored when settings reopen', async ({page}) => {
  await page.addInitScript(() => {
    const language = {preference:'zh-CN',locale:'zh-CN',systemLanguages:['zh-CN']} as const;
    let gpuAcceleration: boolean | undefined;
    window.cutvokeDesktop = {
      initialLanguage:language,getLanguageState:()=>language,setLanguagePreference:async()=>language,
      settings:async()=>({setupComplete:true,projectDirectory:'/test-projects',projectLocationManaged:true,gpuAcceleration}),
      saveSettings:async value=>{gpuAcceleration=value.gpuAcceleration;return {restartRequired:false}},
    } as NonNullable<Window['cutvokeDesktop']>;
  });
  await page.goto('/');
  await page.getByRole('button',{name:'设置',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'设置',exact:true});
  const toggle=dialog.getByRole('checkbox',{name:'启用 GPU 加速',exact:true});
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await dialog.getByRole('button',{name:'保存设置',exact:true}).click();
  await expect(dialog.getByText('设置已保存',{exact:true})).toBeVisible();
  expect((await page.evaluate(()=>window.cutvokeDesktop!.settings())).gpuAcceleration).toBe(false);
  await dialog.getByRole('button',{name:'关闭设置',exact:true}).click();
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await expect(toggle).not.toBeChecked();
  await toggle.check();
  await dialog.getByRole('button',{name:'关闭设置',exact:true}).click();
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await expect(toggle).not.toBeChecked();
});

test('desktop first-run setup must be saved before creating a project', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  await page.addInitScript(() => {
    const language = { preference: 'en', locale: 'en', systemLanguages: ['en-US'] } as const;
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
  await expect(page.getByRole('dialog', { name: 'Welcome to CutVoke' })).toBeVisible();
  await page.locator('dialog.cache-settings').getByRole('combobox').selectOption('zh-CN');
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
