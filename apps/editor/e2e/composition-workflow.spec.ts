import { expect, test, type APIRequestContext, type Page } from '@playwright/test';
import { mkdirSync,writeFileSync } from 'node:fs';
const read = async (request: APIRequestContext, id: string) => (await request.get(`/api/v1/projects/${id}`)).json();
async function command(request: APIRequestContext, id: string, type: string, payload: object) {
  const project = await read(request,id);
  const result = await request.post(`/api/v1/projects/${id}/commands`, { data: { type, payload,
    expectedRevision: project.revision, commandId: crypto.randomUUID(), actor: { kind: 'test', id: 'composition-browser' } } });
  expect(result.ok(), await result.text()).toBeTruthy();
}
async function fixture(request: APIRequestContext, count=1) {
  const seed = await read(request,'e2e-fixture');
  const id = `composition-${Date.now()}-${Math.random().toString(36).slice(2,6)}`;
  expect((await request.post('/api/v1/projects',{ data:{ projectId:id, name:id, width:160, height:90, fps:15 } })).ok()).toBeTruthy();
  for(let i=0;i<count;i++) await command(request,id,'clip.insert',{ clipId:`c${i}`,trackId:'v',...(i===0?{createTrackKind:'video'}:{}),
    sourcePath:seed.sequence.tracks[0].clips[i%3].assetRef.sourcePath,timelineStart:{num:String(i*3),den:'1'},timelineEnd:{num:String(i*3+3),den:'1'} });
  return id;
}
async function open(page: Page,id: string) {
  await page.goto('/'); await page.getByRole('button',{name:`打开工程 ${id}`,exact:true}).click();
  await page.locator('.timeline-track__lane .clip-block').first().click();
}

test('adjustment and exact effect compositing are editable and undoable in the browser',async({page,request})=>{
  const id=await fixture(request); await command(request,id,'effect.add',{clipId:'c0',effectId:'cutvoke.fx.invert',params:{}});
  await open(page,id);await page.getByRole('tab',{name:'效果条',exact:true}).click();
  const row=page.locator('.effect-row').first();await row.locator('.effect-row__compositing summary').click();
  await row.getByRole('spinbutton',{name:'效果透明度',exact:true}).fill('0.5');await row.getByRole('spinbutton',{name:'效果透明度',exact:true}).blur();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects[0].compositing.opacity).toBe(.5);
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await expect.poll(async()=>Object.hasOwn((await read(request,id)).sequence.tracks[0].clips[0].effects[0],'compositing')).toBe(false);
  await page.getByRole('button',{name:'添加调整层',exact:true}).click();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks.some((track:any)=>track.role==='adjustment')).toBe(true);
  mkdirSync('../../.artifacts/composition-workflow',{recursive:true});await page.screenshot({path:'../../.artifacts/composition-workflow/effects-and-adjustment.png'});
});

test('matte selector uses stable references and the server rejects cycles without pollution',async({page,request})=>{
  const id=await fixture(request,2);await open(page,id);
  const section=page.getByTestId('layer-compositing');await section.locator('summary').click();
  await section.getByLabel('轨道遮罩',{exact:true}).selectOption('c1');
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].compositing.matte.clipId).toBe('c1');
  await section.getByText('反相',{exact:true}).click();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].compositing.matte.invert).toBe(true);
  const before=await read(request,id);
  const rejected=await request.post(`/api/v1/projects/${id}/commands`,{data:{type:'clip.composite',payload:{clipId:'c1',compositing:{matte:{clipId:'c0'}}},expectedRevision:before.revision,commandId:crypto.randomUUID()}});
  expect(rejected.ok()).toBe(false);expect((await read(request,id)).revision).toBe(before.revision);
  const video=await request.get(`/api/v1/projects/${id}/preview-window?index=0&editorTitles=1`);expect(video.ok(),await video.text()).toBeTruthy();
});

test('nested composition navigation edits local content and restores modal focus',async({page,request})=>{
  const id=await fixture(request,2);await command(request,id,'clip.group',{clipIds:['c0','c1'],groupId:'nested'});await open(page,id);
  const section=page.getByTestId('essential-properties');await section.locator('summary').click();await section.getByRole('button',{name:'进入预合成',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'预合成编辑',exact:true});await expect(dialog).toBeVisible();
  await dialog.locator('.composition-lane__clips button').first().click();
  const before=await read(request,id);await page.keyboard.press('Delete');expect((await read(request,id)).revision).toBe(before.revision);
  await dialog.getByRole('button',{name:'添加模糊',exact:true}).click();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].nested.tracks[0].clips[0].effects.some((effect:any)=>effect.effectId==='cutvoke.fx.blur')).toBe(true);
  await expect(dialog.getByRole('img',{name:'预合成预览',exact:true})).toBeVisible();
  await page.screenshot({path:'../../.artifacts/composition-workflow/nested-editor.png'});await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
});

test('workspace width and saved layout survive reset and a narrow viewport',async({page,request})=>{
  const id=await fixture(request);await open(page,id);
  const separator=page.getByRole('separator',{name:'素材面板宽度',exact:true});await separator.focus();await page.keyboard.press('ArrowRight');await page.keyboard.press('ArrowRight');
  const expected=await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--zone-left-w'));
  await page.locator('.workspace-settings > summary').click();await page.getByLabel('工作区名称',{exact:true}).fill('剪辑布局');await page.getByRole('button',{name:'保存当前布局',exact:true}).click();
  await page.getByRole('button',{name:'重置布局',exact:true}).click();await page.getByRole('button',{name:'剪辑布局',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--zone-left-w'))).toBe(expected);
  await page.locator('.workspace-settings > summary').click();await page.setViewportSize({width:375,height:800});
  await page.screenshot({path:'../../.artifacts/composition-workflow/narrow.png'});
  const overflow=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(element=>element.getBoundingClientRect().right>window.innerWidth+1 && getComputedStyle(element).position!=='absolute').slice(0,10).map(element=>({tag:element.tagName,cls:element.className,right:element.getBoundingClientRect().right})));
  writeFileSync('../../.artifacts/composition-workflow/layout-diagnostics.json',JSON.stringify(await page.evaluate(()=>[document.documentElement,document.body,...document.querySelectorAll('body *')].filter(element=>element.scrollWidth>element.clientWidth+1 || element.getBoundingClientRect().right>window.innerWidth+1).map(element=>({tag:element.tagName,cls:element.className,width:element.clientWidth,scroll:element.scrollWidth,right:element.getBoundingClientRect().right,position:getComputedStyle(element).position,overflow:getComputedStyle(element).overflow,contain:getComputedStyle(element).contain}))),null,2));
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),{message:JSON.stringify(overflow)}).toBe(true);
});

test('U and UU overview locates animation and camera pose forms preserve the exact instance',async({page,request})=>{
  const id=await fixture(request);
  await command(request,id,'clip.keyframe',{clipId:'c0',action:'add',param:'x',time:0,value:8,interpolation:'linear'});
  await command(request,id,'effect.add',{clipId:'c0',effectId:'cutvoke.fx.cameraPlane',params:{rotationY:20,poseFrames:[{at:0,rotationY:0},{at:2,rotationY:25}]}});
  await open(page,id);const before=await read(request,id);
  await page.keyboard.press('u');const overview=page.getByTestId('modified-properties');await expect(overview.getByRole('button',{name:'动画属性',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.keyboard.press('u');await expect(overview.getByRole('button',{name:'已修改',exact:true})).toHaveAttribute('aria-pressed','true');
  expect((await read(request,id)).revision).toBe(before.revision);
  await overview.locator('.modified-property').filter({hasText:'x'}).last().click();
  const camera=page.locator('.inspector__effect-params[data-effect-id="cutvoke.fx.cameraPlane"]');
  const poses=camera.locator('[data-parameter="poseFrames"]');await poses.locator(':scope > summary').click();
  await poses.getByRole('spinbutton',{name:'三维旋转 Y',exact:true}).first().fill('10');await poses.getByRole('spinbutton',{name:'三维旋转 Y',exact:true}).first().blur();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects[0].params.poseFrames[0].rotationY).toBe(10);
  await page.screenshot({path:'../../.artifacts/composition-workflow/camera-and-properties.png'});
});

test('assisted roto keeps its stack position and exposes paged tracked vertices for manual correction',async({page,request})=>{
  const id=await fixture(request);
  const points=[{x:.2,y:.2},{x:.8,y:.2},{x:.5,y:.8}];
  await command(request,id,'effect.add',{clipId:'c0',effectId:'cutvoke.fx.mask',params:{shape:'freehand',points,pathMode:'bezier'}});
  await command(request,id,'effect.add',{clipId:'c0',effectId:'cutvoke.fx.blur',params:{sigma:2}});
  const original=(await read(request,id)).sequence.tracks[0].clips[0].effects[0];
  await command(request,id,'effect.composite',{clipId:'c0',instanceId:original.instanceId,compositing:{opacity:.8}});
  await open(page,id);const tracking=page.locator('details').filter({has:page.locator(':scope > summary',{hasText:'蒙版光流跟踪'})});
  await tracking.locator(':scope > summary').click();await tracking.getByRole('button',{name:'分析蒙版跟踪',exact:true}).click();
  await expect(tracking.getByRole('button',{name:'应用跟踪结果',exact:true})).toBeVisible({timeout:20000});
  await tracking.getByRole('button',{name:'应用跟踪结果',exact:true}).click();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects[0].effectId).toBe('cutvoke.fx.trackedMask');
  const stack=(await read(request,id)).sequence.tracks[0].clips[0].effects;
  expect(stack[0].instanceId).toBe(original.instanceId);expect(stack[0].compositing.opacity).toBe(.8);expect(stack[1].effectId).toBe('cutvoke.fx.blur');
  const effect=page.locator('.inspector__effect-params[data-effect-id="cutvoke.fx.trackedMask"]');
  if(!(await effect.evaluate(element=>(element as HTMLDetailsElement).open)))await effect.locator(':scope > summary').click();
  const samples=effect.locator('[data-parameter="pathFrames"]');await samples.locator(':scope > summary').click();
  const contour=samples.locator('[data-parameter="points"]');await contour.locator(':scope > summary').click();
  await contour.getByRole('spinbutton',{name:'x',exact:true}).first().fill('0.25');await contour.getByRole('spinbutton',{name:'x',exact:true}).first().blur();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].effects[0].params.pathFrames[0].points[0].x).toBe(.25);
  await page.screenshot({path:'../../.artifacts/composition-workflow/roto-manual-edit.png'});
});
