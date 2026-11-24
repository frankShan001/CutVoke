import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const projectPath=(id:string)=>`/api/v1/projects/${id}`;
async function read(request:APIRequestContext,id:string) {
  const response=await request.get(projectPath(id));expect(response.ok()).toBeTruthy();return response.json();
}
async function apply(request:APIRequestContext,id:string,type:string,payload:object) {
  const project=await read(request,id);
  const response=await request.post(`${projectPath(id)}/commands`,{data:{type,payload,expectedRevision:project.revision,
    commandId:`curve-${Date.now()}-${Math.random()}`,actor:{kind:"human",id:"curve-browser"}}});
  expect(response.ok(),await response.text()).toBeTruthy();
}
async function fixture(request:APIRequestContext) {
  const seed=await read(request,"e2e-fixture");const sourcePath=seed.sequence.tracks[0].clips[0].assetRef.sourcePath;
  const id=`curve-${Date.now()}-${Math.random().toString(36).slice(2,7)}`,name=`曲线验收 ${id}`;
  expect((await request.post('/api/v1/projects',{data:{projectId:id,name,width:160,height:90,fps:15}})).ok()).toBeTruthy();
  await apply(request,id,"track.add",{trackId:"v",kind:"video"});
  await apply(request,id,"clip.insert",{trackId:"v",clipId:"c",sourcePath,timelineStart:{num:"0",den:"1"},timelineEnd:{num:"5",den:"1"}});
  return {id,name,sourcePath};
}
async function open(page:Page,name:string) {
  await page.goto('/');await page.getByRole('button',{name:`打开工程 ${name}`}).click();
  await page.locator('.clip-block').first().click();
  await expect(page.getByLabel('关键帧参数')).toBeVisible();
}

test('generic curve editor persists Bezier volume and retimes selected nodes as one undo',async({page,request})=>{
  const {id,name}=await fixture(request);await open(page,name);
  await page.getByLabel('关键帧参数').selectOption('volume');
  await page.getByLabel('关键帧时间（秒）').fill('0');await page.getByLabel('关键帧值',{exact:true}).fill('.2');
  await page.getByLabel('关键帧插值').selectOption('cubic-bezier');
  for(const [index,value] of ['.8','.1','.9','.2'].entries())await page.getByLabel(`Bezier ${['x1','y1','x2','y2'][index]}`).fill(value);
  await page.locator('.inspector').getByRole('button',{name:'添加',exact:true}).first().click();
  const lane=async()=>(await read(request,id)).sequence.tracks[0].clips[0].keyframes.volume;
  await expect.poll(async()=>(await lane())?.length).toBe(1);
  const selections=page.getByRole('checkbox',{name:/^选择关键帧 /});
  await expect(selections).toHaveCount(1);
  let releaseRefresh!:()=>void,blocked=0;
  const refreshGate=new Promise<void>(resolve=>{releaseRefresh=resolve;});
  let holdRefresh=true;
  await page.route(url=>[`${projectPath(id)}`,`${projectPath(id)}/sync`].includes(url.pathname),async route=>{
    if(holdRefresh && route.request().method()==='GET'){blocked++;await refreshGate;}
    await route.continue().catch(()=>{});
  });
  try {
    await page.getByLabel('关键帧时间（秒）').fill('2');await page.getByLabel('关键帧值',{exact:true}).fill('1');
    await page.getByLabel('关键帧插值').selectOption('linear');
    await page.locator('.inspector').getByRole('button',{name:'添加',exact:true}).first().click();
    await expect.poll(async()=>(await lane())?.length).toBe(2);
    await expect.poll(()=>blocked).toBeGreaterThan(0);
    // The server already has two nodes while this view still has one. Do not
    // allow a stale Select All to silently exclude the just-added node.
    await expect(selections).toHaveCount(1);
    await expect(page.getByRole('button',{name:'全选节点'})).toBeDisabled();
    await expect(selections.first()).toBeDisabled();
    await expect(page.locator('.inspector').getByRole('button',{name:'添加',exact:true}).first()).toBeDisabled();
  } finally {holdRefresh=false;releaseRefresh();}
  await expect(selections).toHaveCount(2);
  await expect(page.getByRole('button',{name:'全选节点'})).toBeEnabled();
  await page.getByRole('button',{name:'全选节点'}).click();
  await expect(selections.nth(0)).toBeChecked();await expect(selections.nth(1)).toBeChecked();
  await page.getByLabel('关键帧移动秒数').fill('.5');await page.getByLabel('关键帧时间倍率').fill('.5');
  await page.getByRole('button',{name:'应用时间调整'}).click();
  const times=async()=>(await lane()).map((key:any)=>Number(key.time.num)/Number(key.time.den));
  await expect.poll(times).toEqual([.5,1.5]);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(times).toEqual([0,2]);
  await page.getByRole('button',{name:'重做',exact:true}).click();await expect.poll(times).toEqual([.5,1.5]);
  await page.reload();await page.getByRole('button',{name:`打开工程 ${name}`}).click();await page.locator('.clip-block').first().click();
  await page.getByLabel('关键帧参数').selectOption('volume');
  await expect.poll(async()=>(await lane())[0].bezier).toEqual([.8,.1,.9,.2]);
  await expect(page.getByRole('img',{name:'参数时间曲线'})).toBeVisible();
});

test('history refresh follows a pristine keyframe form without overwriting a newer selection',async({page,request})=>{
  const {id,name}=await fixture(request);
  await apply(request,id,'clip.keyframe',{clipId:'c',action:'batch',keyframes:[
    {param:'opacity',time:{num:'1',den:'1'},value:.4,interpolation:'linear'},
    {param:'opacity',time:{num:'3',den:'1'},value:.8,interpolation:'linear'},
  ]});
  await open(page,name);
  const lane=async()=>(await read(request,id)).sequence.tracks[0].clips[0].keyframes.opacity;
  const keyTime=(frame:{time:{num:string;den:string}})=>Number(frame.time.num)/Number(frame.time.den);
  const keys=await lane();
  const a=keys.find((key:{time:{num:string;den:string}})=>keyTime(key)===1);
  const b=keys.find((key:{time:{num:string;den:string}})=>keyTime(key)===3);
  const checkbox=(key:string)=>page.getByRole('checkbox',{name:`选择关键帧 ${key}`,exact:true});
  const time=page.getByLabel('关键帧时间（秒）',{exact:true});
  const undo=page.getByRole('button',{name:'撤销',exact:true});
  const update=page.getByRole('button',{name:'更新节点',exact:true});
  const aTime=async()=>{
    const frame=(await lane()).find((node:{id:string})=>node.id===a.id);
    return keyTime(frame);
  };
  await checkbox(a.id).check();
  await page.getByLabel('关键帧移动秒数').fill('.5');
  await page.getByRole('button',{name:'应用时间调整',exact:true}).click();
  await expect.poll(aTime).toBe(1.5);
  await expect(time).toHaveValue('1.5');
  await undo.click();
  await expect.poll(aTime).toBe(1);
  await expect(checkbox(a.id)).toBeChecked();
  await expect(time).toHaveValue('1');
  await page.getByRole('button',{name:'重做',exact:true}).click();
  await expect.poll(aTime).toBe(1.5);
  await expect(time).toHaveValue('1.5');

  // A deliberate draft survives, but must not overwrite a changed version of
  // the same selected keyframe until that keyframe is explicitly reselected.
  await time.fill('1.75');
  await undo.click();
  await expect.poll(aTime).toBe(1);
  await expect(time).toHaveValue('1.75');
  await expect(update).toBeDisabled();
  await expect(page.locator('.inspector').getByRole('button',{name:'添加',exact:true}).first()).toBeDisabled();
  await page.locator('.keyframe-editor').getByRole('button',{name:'重置参数',exact:true}).click();
  await expect(checkbox(a.id)).toBeChecked();
  await expect(time).toHaveValue('1');
  await expect(update).toBeEnabled();

  await page.getByRole('button',{name:'重做',exact:true}).click();
  await expect.poll(aTime).toBe(1.5);
  await expect(time).toHaveValue('1.5');
  let release!:()=>void;
  const gate=new Promise<void>(resolve=>{release=resolve;});
  let waiting=false;
  const match=(url:URL)=>url.pathname===`${projectPath(id)}/commands`;
  await page.route(match,async route=>{
    if(route.request().method()==='POST' && route.request().postDataJSON().type==='history.undo'){
      waiting=true;await gate;
    }
    await route.continue().catch(()=>{});
  });
  try {
    await undo.click();
    await expect.poll(()=>waiting).toBe(true);
    const newer=page.getByRole('button',{name:`曲线节点 ${b.id}`,exact:true});
    await newer.press('Enter');
    await expect(checkbox(b.id)).toBeChecked();
    await expect(checkbox(a.id)).not.toBeChecked();
    await time.fill('3.25');
  } finally {
    release();
  }
  await expect.poll(aTime).toBe(1);
  await expect(checkbox(a.id).locator('..')).toContainText('t=1.00s');
  await expect(checkbox(b.id)).toBeChecked();
  await expect(checkbox(a.id)).not.toBeChecked();
  await expect(time).toHaveValue('3.25');
  await expect(update).toBeEnabled();
  await page.unroute(match);
});

test('graph node and Bezier handle gestures commit and undo without losing node identity',async({page,request})=>{
  const {id,name}=await fixture(request);
  await apply(request,id,'clip.keyframe',{clipId:'c',action:'batch',keyframes:[
    {param:'opacity',time:0,value:.2,interpolation:'cubic-bezier',bezier:[.25,.1,.25,1]},
    {param:'opacity',time:2,value:.8}]});
  await open(page,name);
  const lane=async()=>(await read(request,id)).sequence.tracks[0].clips[0].keyframes.opacity;
  const original=await lane();const node=page.getByRole('button',{name:`曲线节点 ${original[0].id}`,exact:true});
  await node.scrollIntoViewIfNeeded();let box=(await node.boundingBox())!;
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
  await page.mouse.move(box.x+35,box.y-12,{steps:6});await page.mouse.up();
  await expect.poll(async()=>Number((await lane())[0].time.num)).not.toBe(0);
  expect((await lane())[0].id).toBe(original[0].id);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(lane).toEqual(original);
  await node.click();const handle=page.locator('[aria-label="贝塞尔控制点 1"]');await handle.scrollIntoViewIfNeeded();box=(await handle.boundingBox())!;
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();
  await page.mouse.move(box.x+25,box.y-10,{steps:5});await page.mouse.up();
  await expect.poll(async()=>(await lane())[0].bezier).not.toEqual(original[0].bezier);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(lane).toEqual(original);
  await page.locator('.inspector').screenshot({path:'../../.artifacts/keyframe-curve-inspector.png'});
});

test('effect-instance lanes survive reorder and audio clips expose the volume curve',async({page,request})=>{
  const {id,name}=await fixture(request);
  await apply(request,id,'effect.add',{clipId:'c',effectId:'cutvoke.fx.blur',params:{sigma:2}});
  await apply(request,id,'effect.add',{clipId:'c',effectId:'cutvoke.fx.blur',params:{sigma:8}});
  const effects=(await read(request,id)).sequence.tracks[0].clips[0].effects;
  const param=`effect:${effects[0].instanceId}:sigma`;
  await open(page,name);await page.getByLabel('关键帧参数').selectOption(param);
  await page.getByLabel('关键帧值',{exact:true}).fill('4');await page.locator('.inspector').getByRole('button',{name:'添加',exact:true}).first().click();
  await expect.poll(async()=>(await read(request,id)).sequence.tracks[0].clips[0].keyframes[param]?.length).toBe(1);
  await apply(request,id,'effect.reorder',{clipId:'c',effectId:'cutvoke.fx.blur',toIndex:1});
  const clip=(await read(request,id)).sequence.tracks[0].clips[0];expect(clip.effects[1].instanceId).toBe(effects[0].instanceId);expect(clip.keyframes[param][0].value).toBe(4);
  await apply(request,id,'track.add',{trackId:'audio',kind:'audio'});
  const audioPath=fileURLToPath(new URL('../../../src/cutvoke/assets/audio/tech_minimal.wav',import.meta.url));
  await apply(request,id,'clip.insert',{trackId:'audio',clipId:'audio-c',sourcePath:audioPath,timelineStart:{num:'0',den:'1'},timelineEnd:{num:'5',den:'1'}});
  await page.reload();await page.getByRole('button',{name:`打开工程 ${name}`}).click();
  await page.locator('.timeline-track__lane[data-track-id="audio"] .clip-block').click();
  await expect(page.getByLabel('关键帧参数')).toHaveValue('volume');
  await expect(page.getByLabel('关键帧参数').locator('option')).toHaveCount(1);
});
