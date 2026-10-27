import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
const read=async(request:APIRequestContext,id:string)=>(await request.get(`/api/v1/projects/${id}`)).json();
async function command(request:APIRequestContext,id:string,type:string,payload:object) {
  const project=await read(request,id);
  const response=await request.post(`/api/v1/projects/${id}/commands`,{data:{type,payload,expectedRevision:project.revision,
    commandId:`ae-${Date.now()}-${Math.random()}`,actor:{kind:'human',id:'ae-regression'}}});
  expect(response.ok(),await response.text()).toBeTruthy();
}
async function fixture(request:APIRequestContext,count=1) {
  const seed=await read(request,'e2e-fixture');
  const id=`ae-${Date.now()}-${Math.random().toString(36).slice(2,6)}`;
  expect((await request.post('/api/v1/projects',{data:{projectId:id,name:id,width:160,height:90,fps:15}})).ok()).toBeTruthy();
  for(let i=0;i<count;i++)await command(request,id,'clip.insert',{clipId:`c${i}`,trackId:'v',...(i===0?{createTrackKind:'video'}:{}),
    sourcePath:seed.sequence.tracks[0].clips[i%3].assetRef.sourcePath,
    timelineStart:{num:String(i*3),den:'1'},timelineEnd:{num:String(i*3+3),den:'1'}});
  return id;
}
async function open(page:Page,id:string) {
  await page.goto('/');await page.getByRole('button',{name:`打开工程 ${id}`,exact:true}).click();
  await page.locator('.timeline-track__lane .clip-block').first().click();
}

test('quick apply ranks scopes, respects input and modal focus, and uses undoable effect commands',async({page,request})=>{
  const id=await fixture(request);await open(page,id);
  const original=await read(request,id);
  await page.getByLabel('搜索片段、素材或特效').focus();await page.keyboard.press('Control+Enter');
  await expect(page.getByRole('dialog',{name:'快速应用'})).toHaveCount(0);
  await page.locator('.timeline__title').click();await page.keyboard.press('Control+Enter');
  const dialog=page.getByRole('dialog',{name:'快速应用'});await expect(dialog).toBeVisible();
  await dialog.getByRole('button',{name:'关闭',exact:true}).focus();await page.keyboard.press('Delete');
  expect((await read(request,id)).revision).toBe(original.revision);
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
  await page.getByRole('button',{name:'快速应用',exact:true}).click();
  const search=dialog.getByRole('combobox');await search.fill('e: blur');
  await expect(dialog.getByRole('option').first()).toContainText('高斯模糊');
  mkdirSync('../../.artifacts/ae-workflow',{recursive:true});
  await page.screenshot({path:'../../.artifacts/ae-workflow/quick-apply.png'});
  await search.press('Enter');await expect(dialog).toHaveCount(0);
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects.map((effect:any)=>effect.effectId)).toContain('cutvoke.fx.blur');
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects.length).toBe(0);
  await page.getByRole('button',{name:'快速应用',exact:true}).click();await search.fill('p:');
  await expect(dialog.getByRole('option').first()).toBeVisible();
  await expect(dialog.getByRole('option').first()).toContainText('预设');
  await search.fill('e: definitely-no-effect-xxx');await expect(dialog.getByText('没有匹配结果')).toBeVisible();
  await search.press('Escape');
});

test('timeline filter persists through selection and shortcuts without changing the rendered project',async({page,request})=>{
  const id=await fixture(request,3);
  await command(request,id,'clip.keyframe',{clipId:'c1',param:'opacity',time:0,value:.5,interpolation:'hold'});
  let releaseAssets!:()=>void;
  const assetGate=new Promise<void>(resolve=>{releaseAssets=resolve;});
  await page.route(/\/api\/v1\/assets(?:\?.*)?$/,async route=>{await assetGate;await route.continue().catch(()=>{});});
  await open(page,id);const original=await read(request,id);
  const search=page.getByLabel('搜索片段、素材或特效');await search.fill('red');
  await expect(page.locator('.timeline-track__lane .clip-block')).toHaveCount(0);
  releaseAssets();
  await expect(page.locator('.timeline-track__lane .clip-block')).toHaveCount(1);
  await page.locator('.timeline__title').click();await page.keyboard.press('Control+a');
  await expect(page.getByText('已选 1 个片段',{exact:true})).toBeVisible();await expect(search).toHaveValue('red');
  await page.getByLabel('片段筛选方式').selectOption('animated');
  await expect(page.locator('.timeline-track__lane .clip-block')).toHaveCount(0);
  await search.fill('');await expect(page.locator('.timeline-track__lane .clip-block')).toHaveCount(1);
  await expect(page.locator('.timeline-track__lane .clip-block')).toHaveAttribute('data-clip-id','c1');
  expect((await read(request,id)).sequence).toEqual(original.sequence);
  await page.getByLabel('清除筛选').click();await expect(page.locator('.timeline-track__lane .clip-block')).toHaveCount(3);
});

test('keyframe easing is atomic and geometry can be edited and removed from its exact effect instance',async({page,request})=>{
  test.setTimeout(90000);
  const id=await fixture(request);
  await command(request,id,'clip.keyframe',{clipId:'c0',action:'batch',keyframes:[{param:'opacity',time:0,value:.2},{param:'opacity',time:1,value:1}]});
  await command(request,id,'effect.add',{clipId:'c0',effectId:'cutvoke.fx.opticalField',params:{layer:'flare',duration:.5}});
  await open(page,id);
  const lane=async()=>(await read(request,id)).sequence.tracks[0].clips[0].keyframes.opacity;
  const original=await lane();await page.getByRole('button',{name:'全选节点'}).click();await page.keyboard.press('F9');
  await expect.poll(async()=>(await lane()).map((frame:any)=>frame.interpolation)).toEqual(['cubic-bezier','cubic-bezier']);
  expect((await lane()).map((frame:any)=>frame.id)).toEqual(original.map((frame:any)=>frame.id));
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(lane).toEqual(original);
  await page.getByRole('button',{name:'保持（阶梯）',exact:true}).click();
  await expect.poll(async()=>(await lane())[0].interpolation).toBe('hold');
  await page.getByLabel('曲线类型').selectOption('speed');await expect(page.getByRole('img',{name:'参数时间曲线'})).toBeVisible();
  await page.getByLabel('曲线类型').selectOption('value');await page.getByRole('button',{name:'下一关键帧',exact:true}).click();
  await expect(page.getByLabel('关键帧时间（秒）')).toHaveValue('1');
  const optical=page.locator('.inspector details[data-effect-id="cutvoke.fx.opticalField"]');
  if(!await optical.evaluate(node=>(node as HTMLDetailsElement).open)) await optical.locator('summary').first().click();
  const region=optical.locator('[data-parameter="reflectionRegion"]');await region.locator('summary').click();
  const enabled=region.getByRole('checkbox');await enabled.check();
  const params=async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects.find((effect:any)=>effect.effectId==='cutvoke.fx.opticalField').params;
  await expect.poll(async()=>!!(await params()).reflectionRegion).toBe(true);
  await region.getByRole('spinbutton',{name:'y',exact:true}).fill('.6');await region.getByRole('spinbutton',{name:'y',exact:true}).blur();
  await expect.poll(async()=>(await params()).reflectionRegion.y).toBe(.6);
  await page.screenshot({path:'../../.artifacts/ae-workflow/geometry-controls.png'});
  await enabled.uncheck();await expect.poll(async()=>Object.hasOwn(await params(),'reflectionRegion')).toBe(false);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(async()=>(await params()).reflectionRegion.y).toBe(.6);
});

test('effect search stays usable while preset loading is delayed',async({page,request})=>{
  const id=await fixture(request);await open(page,id);
  let release!:()=>void;const gate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/v1/presets',async route=>{await gate;await route.continue().catch(()=>{});});
  try {
    await page.getByRole('button',{name:'快速应用',exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'快速应用'});await dialog.getByRole('combobox').fill('e: blur');
    await expect(dialog.getByRole('option').first()).toContainText('高斯模糊',{timeout:3000});
    await dialog.getByRole('combobox').press('Enter');
    await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects.length).toBe(1);
  } finally {release();}
});

test('complete effect stack operations address duplicates precisely and expose changed parameters',async({page,request})=>{
  const id=await fixture(request);
  for(const sigma of [2,8])await command(request,id,'effect.add',{clipId:'c0',effectId:'cutvoke.fx.blur',params:{sigma}});
  const effects=async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects;
  const [first,second]=await effects();await open(page,id);
  await page.getByRole('tab',{name:'效果条',exact:true}).click();
  const one=page.locator(`.effect-row[data-effect-instance-id="${first.instanceId}"]`);
  const two=page.locator(`.effect-row[data-effect-instance-id="${second.instanceId}"]`);
  await two.getByRole('button',{name:'旁路 高斯模糊',exact:true}).click();
  await expect.poll(async()=>(await effects())[1].enabled).toBe(false);expect((await effects())[0].enabled).not.toBe(false);
  await two.getByRole('button',{name:'重置参数',exact:true}).click();
  await expect.poll(async()=>(await effects())[1].params.sigma).toBe(5);expect((await effects())[0].params.sigma).toBe(2);
  await two.getByRole('button',{name:'恢复 高斯模糊',exact:true}).click();
  await page.getByRole('checkbox',{name:'仅显示已修改',exact:true}).check();
  await expect(one).toBeVisible();await expect(two).toHaveCount(0);
  await page.getByRole('checkbox',{name:'仅显示已修改',exact:true}).uncheck();
  await two.getByRole('button',{name:'删除 高斯模糊',exact:true}).click();
  await expect.poll(async()=>(await effects()).map((effect:any)=>effect.instanceId)).toEqual([first.instanceId]);
});

test('quick apply fits narrow German layouts and retains native modal keyboard focus',async({page,request})=>{
  const id=await fixture(request);await open(page,id);
  await page.getByRole('combobox',{name:'界面语言',exact:true}).selectOption('de');
  await page.getByRole('button',{name:'Schnell anwenden',exact:true}).click();
  await page.setViewportSize({width:375,height:800});
  const dialog=page.getByRole('dialog',{name:'Schnell anwenden'});
  const box=await dialog.boundingBox();expect(box).toBeTruthy();expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(375);
  expect(await dialog.evaluate(node=>node.scrollWidth<=node.clientWidth+1)).toBeTruthy();
  for(let i=0;i<9;i++){await page.keyboard.press('Tab');expect(await dialog.evaluate(node=>node.contains(document.activeElement))).toBeTruthy();}
  await page.screenshot({path:'../../.artifacts/ae-workflow/quick-apply-narrow-de.png'});
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
});
