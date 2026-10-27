import {expect,test,type APIRequestContext,type Page} from '@playwright/test';
import {mkdirSync} from 'node:fs';
const read=async(request:APIRequestContext,id:string)=>(await request.get(`/api/v1/projects/${id}`)).json();
async function command(request:APIRequestContext,id:string,type:string,payload:object){
  const project=await read(request,id);const response=await request.post(`/api/v1/projects/${id}/commands`,{data:{type,payload,expectedRevision:project.revision,commandId:crypto.randomUUID()}});
  expect(response.ok(),await response.text()).toBeTruthy();
}
async function tool(request:APIRequestContext,name:string,data:object){
  const response=await request.post(`/api/v1/agent/${name}`,{data});const result=await response.json();expect(result.ok,JSON.stringify(result)).toBeTruthy();return result;
}
async function fixture(page:Page,request:APIRequestContext){
  const seed=await read(request,'e2e-fixture'),id=`local-models-${crypto.randomUUID().slice(0,8)}`;
  expect((await request.post('/api/v1/projects',{data:{projectId:id,name:id,width:160,height:90,fps:15}})).ok()).toBeTruthy();
  await command(request,id,'clip.insert',{clipId:'base',trackId:'v',createTrackKind:'video',sourcePath:seed.sequence.tracks[0].clips[0].assetRef.sourcePath,timelineStart:{num:'0',den:'1'},timelineEnd:{num:'1',den:'1'}});
  await page.goto('/');await page.getByRole('button',{name:`打开工程 ${id}`,exact:true}).click();
  await page.locator('.timeline-track__lane .clip-block').first().click();return id;
}
test('local 3D objects, OBJ import, editable geometry and undo are available in the browser',async({page,request})=>{
  const id=await fixture(page,request);await page.getByRole('button',{name:'添加三维场景',exact:true}).click();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks.length).toBe(2);
  await expect(page.locator('.timeline-track__lane .clip-block')).toHaveCount(2);
  await expect(page.locator('.timeline-track__lane .clip-block').last()).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.timeline-track__lane .clip-block').last()).toContainText('三维场景');
  await page.locator('.timeline-track__lane .clip-block').last().click();
  const effect=page.locator('.inspector__effect-params[data-effect-id="cutvoke.fx.scene3d"]');
  if(!(await effect.evaluate(element=>(element as HTMLDetailsElement).open)))await effect.locator(':scope > summary').click();
  const controls=page.getByTestId('scene3d-controls');await controls.getByRole('button',{name:'添加立方体',exact:true}).click();
  const objects=async()=>(await read(request,id)).sequence.tracks[1].clips[0].effects[0].params.objects;
  await expect.poll(async()=>(await objects()).length).toBe(3);
  await controls.getByLabel('OBJ 模型文件',{exact:true}).setInputFiles({name:'triangle.obj',mimeType:'text/plain',buffer:Buffer.from('v -1 -1 0\nv 1 -1 0\nv 0 1 0\nf 1 2 3')});
  await expect.poll(async()=>(await objects()).length).toBe(4);expect((await objects()).at(-1).shape).toBe('mesh');
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(async()=>(await objects()).length).toBe(3);
  const camera=effect.locator('[data-parameter="camera"]');await camera.locator(':scope > summary').click();
  await camera.getByRole('spinbutton',{name:'视野角度',exact:true}).fill('60');await camera.getByRole('spinbutton',{name:'视野角度',exact:true}).blur();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[1].clips[0].effects[0].params.camera.fov).toBe(60);
  const objectForm=effect.locator('[data-parameter="objects"]');await objectForm.locator(':scope > summary').click();await expect(objectForm.getByRole('spinbutton',{name:'粗糙度',exact:true}).first()).toBeVisible();
  const preview=await request.get(`/api/v1/projects/${id}/preview-window?index=0&editorTitles=1`);expect(preview.ok(),await preview.text()).toBeTruthy();
  const frame=await request.get(`/api/v1/projects/${id}/preview-frame?t=0&size=160x90`);expect(frame.ok(),await frame.text()).toBeTruthy();
  await expect(page.getByText(/预览渲染失败|empty source_path/)).toHaveCount(0);
  mkdirSync('../../.artifacts/local-3d',{recursive:true});await page.screenshot({path:'../../.artifacts/local-3d/editor-scene.png'});
});
test('explicit UI handoff persists without a host and reaches an actively waiting vision agent',async({page,request})=>{
  const id=await fixture(page,request);await page.getByRole('tab',{name:'效果条',exact:true}).click();
  const handoff=page.getByTestId('agent-handoff');await handoff.locator(':scope > summary').click();
  await expect(handoff.getByText('没有等待接单的 AI；请求将保留，可复制交接说明。',{exact:true})).toBeVisible();
  await handoff.getByRole('button',{name:'请求 AI 协助选取',exact:true}).click();await expect(handoff.getByText('待 AI 接手',{exact:true})).toBeVisible();
  const before=await read(request,id);await page.reload();await page.getByRole('button',{name:`打开工程 ${id}`,exact:true}).click();
  await page.locator('.timeline-track__lane .clip-block').first().click();await page.getByRole('tab',{name:'效果条',exact:true}).click();
  await handoff.locator(':scope > summary').click();await expect(handoff.getByText('待 AI 接手',{exact:true})).toBeVisible();
  await handoff.getByRole('button',{name:'取消请求',exact:true}).click();await expect(handoff.getByText('已取消',{exact:true})).toBeVisible();
  const registered=await tool(request,'agent_session',{operation:'register',name:'Browser vision host',capabilities:{selfAssessed:true,imageInput:true,toolImageInput:true}});
  const session={agentId:registered.agentId,sessionToken:registered.sessionToken};
  try{
    const waiting=tool(request,'agent_requests',{projectId:id,operation:'wait',waitSeconds:15,...session});
    await expect(handoff.getByText('有支持看图的 AI 正在等待接单',{exact:true})).toBeVisible();
    await handoff.getByRole('button',{name:'请求 AI 协助选取',exact:true}).click();const delivery=await waiting;
    expect(delivery.automaticWake).toBe(false);expect(delivery.requests).toHaveLength(1);
    const requestId=delivery.requests[0].requestId;
    await tool(request,'agent_requests',{projectId:id,operation:'claim',requestId,...session});
    await expect(handoff.getByText('AI 正在处理',{exact:true})).toBeVisible();
    await tool(request,'agent_requests',{projectId:id,operation:'complete',requestId,...session,result:{reviewedRevision:before.revision,summary:'本次验收只验证请求交接，不声称已检查抠像画面。'}});
    await expect(handoff.getByText('AI 已返回结果',{exact:true})).toBeVisible();expect((await read(request,id)).revision).toBe(before.revision);
    await page.setViewportSize({width:1000,height:720});await expect(handoff.getByRole('button',{name:'请求 AI 协助选取',exact:true})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.screenshot({path:'../../.artifacts/local-3d/agent-handoff.png'});
  }finally{await tool(request,'agent_session',{operation:'close',...session});}
});
