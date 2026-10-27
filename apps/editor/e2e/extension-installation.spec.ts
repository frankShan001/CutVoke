import {expect,test,type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';

async function openComponents(page:Page) {
  await page.goto('/');
  await page.getByRole('button',{name:'设置',exact:true}).click();
  const settings=page.getByRole('dialog',{name:'设置',exact:true});
  await settings.getByRole('button',{name:'可选抠像组件',exact:true}).click();
  return settings.locator('.extension-setup');
}

test('fresh UI asks for a concrete plan, decline is harmless, and shared jobs resume after closing settings',async({page})=>{
  const calls:{action:string;body:Record<string,unknown>}[]=[];
  let job:any=null;let installed=false;let confirmed=false;
  await page.route('**/api/v1/extensions/*',async route=>{
    const action=route.request().url().split('/').at(-1)!;const body=route.request().postDataJSON()||{};
    calls.push({action,body});let result:object={ok:true};
    if(action==='status') result={extensions:[{id:'person',installed,job},{id:'object',installed:false}]};
    if(action==='plan') result={plan:{planId:'reviewed',extensionId:'person',installable:true,alreadyInstalled:false,installDirectory:'E:\\CutVoke Components',downloadBytes:120000000,installedBytes:300000000,state:'needs_user_confirmation',confirmation:confirmed?{confirmationId:'actual-ui-click',decision:'accept'}:null,disk:{freeBytes:20000000000,requiredBytes:1000000000,sufficient:true},device:{suitability:'recommended',ramBytes:32000000000,gpus:[]}}};
    if(action==='confirm') {confirmed=body.decision==='accept';result={confirmation:{confirmationId:'actual-ui-click',decision:body.decision}};}
    if(action==='install') {expect(body).toEqual({planId:'reviewed',confirmationId:'actual-ui-click'});job={jobId:'shared-install',extensionId:'person',status:'running',phase:'downloading',progress:35};result={job};}
    if(action==='job') {job={...job,status:'cancelled',phase:'cancelled',retryable:true};result={job};}
    await route.fulfill({json:result});
  });
  await page.goto('/');expect(calls).toHaveLength(0);
  let panel=await openComponents(page);
  await expect(panel.getByText('未安装',{exact:true})).toHaveCount(2);
  expect(calls.every(call=>call.action==='status')).toBe(true);
  await panel.locator('article').first().getByRole('button',{name:'启用',exact:true}).click();
  await expect(panel.getByRole('button',{name:'同意并安装',exact:true})).toHaveCount(0);
  await panel.getByRole('button',{name:'检查设备与空间',exact:true}).click();
  await expect(panel.getByText('114.4 MiB',{exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'暂不安装',exact:true}).click();
  expect(calls.filter(call=>call.action==='confirm').at(-1)?.body.decision).toBe('decline');
  expect(calls.some(call=>call.action==='install')).toBe(false);
  await panel.locator('article').first().getByRole('button',{name:'启用',exact:true}).click();
  await panel.getByRole('button',{name:'检查设备与空间',exact:true}).click();
  await panel.getByRole('button',{name:'同意并安装',exact:true}).click();
  await expect(panel.getByText('正在下载组件…',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'关闭设置',exact:true}).click();
  panel=await openComponents(page);
  await expect(panel.getByText('正在下载组件…',{exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'取消安装',exact:true}).click();
  await expect(panel.getByText('已取消，下载文件已保留',{exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'继续安装',exact:true}).click();
  await panel.getByRole('button',{name:'检查设备与空间',exact:true}).click();
  await expect(panel.getByText('114.4 MiB',{exact:true})).toBeVisible();
  const resume=panel.getByRole('button',{name:'继续安装',exact:true}).last();await expect(resume).toBeEnabled();
  mkdirSync('../../.artifacts/extension-installation',{recursive:true});
  await resume.scrollIntoViewIfNeeded();
  await page.screenshot({path:'../../.artifacts/extension-installation/confirm-ui.png'});
  expect(await panel.locator('code').count()).toBe(0);
  await resume.click();await expect(panel.getByText('正在下载组件…',{exact:true})).toBeVisible();
  expect(calls.filter(call=>call.action==='confirm')).toHaveLength(2);
});

test('device and storage blockers prevent approval and a changed installation location invalidates the displayed plan',async({page})=>{
  let calls=0;
  await page.route('**/api/v1/extensions/*',async route=>{
    const action=route.request().url().split('/').at(-1)!;
    if(action==='status') return route.fulfill({json:{extensions:[{id:'object',installed:false}]}});
    expect(action).toBe('plan');calls++;
    return route.fulfill({json:{plan:{planId:'blocked',extensionId:'object',installable:false,alreadyInstalled:false,installDirectory:'C:\\full',downloadBytes:3000000000,installedBytes:5000000000,disk:{freeBytes:100000000,requiredBytes:12000000000,sufficient:false},device:{suitability:'slow',ramBytes:16000000000,gpus:[]}}}});
  });
  const panel=await openComponents(page);
  await panel.getByRole('button',{name:'启用',exact:true}).click();
  await panel.getByRole('button',{name:'检查设备与空间',exact:true}).click();
  await expect(panel.getByText('空间不足，请选择其他磁盘后重新检查。',{exact:true})).toBeVisible();
  await expect(panel.getByRole('button',{name:'同意并安装',exact:true})).toHaveCount(0);
  await panel.getByText('下载和安装位置',{exact:true}).click();
  await panel.getByLabel('组件保存目录',{exact:true}).fill('E:\\components');
  await expect(panel.getByText('空间不足，请选择其他磁盘后重新检查。',{exact:true})).toHaveCount(0);
  expect(calls).toBe(1);
  await page.setViewportSize({width:1000,height:720});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});
