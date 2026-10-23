import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { fileURLToPath } from "node:url";

const base=(id:string)=>`/api/v1/projects/${id}`;
async function read(request:APIRequestContext,id:string) { const r=await request.get(base(id));expect(r.ok()).toBeTruthy();return r.json(); }
async function apply(request:APIRequestContext,id:string,type:string,payload:object) {
  const p=await read(request,id);const r=await request.post(`${base(id)}/commands`,{data:{type,payload,expectedRevision:p.revision,
    commandId:`timeline-${Date.now()}-${Math.random()}`,actor:{kind:"human",id:"timeline-browser"}}});
  expect(r.ok(),await r.text()).toBeTruthy();
}
async function fixture(request:APIRequestContext,times=[[0,5],[5,10],[10,15]]) {
  const seed=await read(request,"e2e-fixture");const sourcePath=seed.sequence.tracks[0].clips[0].assetRef.sourcePath;
  const id=`timeline-${Date.now()}-${Math.random().toString(36).slice(2,6)}`,name=`时间线验收 ${id}`;
  const created=await request.post('/api/v1/projects',{data:{projectId:id,name,width:160,height:90,fps:15}});
  expect(created.ok(),await created.text()).toBeTruthy();
  await apply(request,id,'track.add',{trackId:'v',kind:'video'});
  for(const [i,[start,end]] of times.entries())await apply(request,id,'clip.insert',{trackId:'v',clipId:['a','b','c'][i],sourcePath,
    timelineStart:{num:String(start),den:'1'},timelineEnd:{num:String(end),den:'1'}});
  return {id,name,sourcePath};
}
async function open(page:Page,name:string) { await page.goto('/');await page.getByRole('button',{name:`打开工程 ${name}`}).click();await expect(page.locator('[data-clip-id="a"]')).toBeVisible();await page.locator('.project-loading').waitFor({state:'hidden'}); }
const clip=(page:Page,id:string)=>page.locator(`.clip-block[data-clip-id="${id}"]`);
const clips=async(request:APIRequestContext,id:string)=>(await read(request,id)).sequence.tracks.flatMap((t:any)=>t.clips.map((c:any)=>({...c,trackId:t.id})));
const seconds=(r:any)=>Number(r.num)/Number(r.den);

test('Ctrl and Shift multi-select apply volume and delete atomically with undo',async({page,request})=>{
  const {id,name}=await fixture(request);await open(page,name);
  await clip(page,'a').click();await clip(page,'c').click({modifiers:['Shift']});
  await expect(page.locator('.clip-block[aria-pressed="true"]')).toHaveCount(3);
  await expect(page.getByRole('region',{name:'批量片段编辑'})).toBeVisible();
  await clip(page,'b').click({modifiers:['Control']});await expect(page.locator('.clip-block[aria-pressed="true"]')).toHaveCount(2);
  await page.getByLabel('批量音量 (%)').fill('50');await page.getByRole('button',{name:'应用批量音频属性'}).click();
  await expect.poll(async()=>(await clips(request,id)).map((c:any)=>seconds(c.volume))).toEqual([.5,1,.5]);
  await page.keyboard.press('Delete');await expect.poll(async()=>(await clips(request,id)).map((c:any)=>c.id)).toEqual(['b']);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(async()=>(await clips(request,id)).length).toBe(3);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(async()=>(await clips(request,id)).map((c:any)=>seconds(c.volume))).toEqual([1,1,1]);
});

test('adjacent duplicate inserts safely and Ctrl C V pastes the saved snapshot at the playhead',async({page,request})=>{
  const {id,name}=await fixture(request);await open(page,name);await clip(page,'a').click();await page.keyboard.press('Control+d');
  await expect.poll(async()=>(await clips(request,id)).length).toBe(4);
  await expect.poll(async()=>seconds((await clips(request,id)).find((c:any)=>c.id==='b').timelineStart)).toBe(10);
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(async()=>(await clips(request,id)).length).toBe(3);
  await clip(page,'a').click();await page.keyboard.press('Control+c');
  const ruler=page.getByRole('slider',{name:'时间线播放头'});await ruler.focus();await ruler.press('Home');
  for(let i=0;i<30;i++)await ruler.press('ArrowRight');expect(Number(await ruler.getAttribute('aria-valuenow'))).toBeCloseTo(2,8);
  await page.keyboard.press('Control+v');await expect.poll(async()=>(await clips(request,id)).length).toBe(5);
  const state=await clips(request,id);expect(state.some((c:any)=>seconds(c.timelineStart)===2&&seconds(c.timelineEnd)===7)).toBeTruthy();
  expect(seconds(state.find((c:any)=>c.id==='b').timelineStart)).toBe(10);
  await page.screenshot({path:'../../.artifacts/basic-fixes-20261024/timeline/clipboard-paste.png'});
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(async()=>(await clips(request,id)).length).toBe(3);
  await page.getByRole('button',{name:'重做',exact:true}).click();await expect.poll(async()=>(await clips(request,id)).length).toBe(5);
  await page.reload();await page.getByRole('button',{name:`打开工程 ${name}`}).click();await expect(page.locator('.clip-block')).toHaveCount(5);
});

test('group pointer drag preserves relative offsets across lanes as one undo',async({page,request})=>{
  const {id,name}=await fixture(request,[[0,2],[2,4],[10,12]]);await apply(request,id,'track.add',{trackId:'v2',kind:'video'});await open(page,name);
  await clip(page,'a').click();await clip(page,'b').click({modifiers:['Control']});
  const source=(await clip(page,'a').boundingBox())!;const lane=page.locator('.timeline-track__lane[data-track-id="v2"]');const target=(await lane.boundingBox())!;
  const zoomText=await page.locator('.timeline__zoom-val').innerText();const px=Number(zoomText.replace('px/s',''));
  await page.mouse.move(source.x+source.width/2,source.y+source.height/2);await page.mouse.down();
  await page.mouse.move(target.x+px+source.width/2,target.y+target.height/2,{steps:8});
  await expect(page.locator('.clip-block--ghost')).toHaveCount(2);
  await page.screenshot({path:'../../.artifacts/basic-fixes-20261024/timeline/group-drag.png'});
  await page.mouse.up();
  await expect.poll(async()=>(await clips(request,id)).filter((c:any)=>c.trackId==='v2').map((c:any)=>[c.id,seconds(c.timelineStart)])).toEqual([['a',1],['b',3]]);
  await expect(page.locator('.clip-block[aria-pressed="true"]')).toHaveCount(2);
  await page.getByRole('button',{name:'撤销',exact:true}).click();await expect.poll(async()=>(await clips(request,id)).filter((c:any)=>c.trackId==='v').length).toBe(3);
});

test('rectangle selection operates on timeline clips without editing the project',async({page,request})=>{
  const {id,name}=await fixture(request,[[0,2],[3,5],[10,12]]);await open(page,name);
  const before=(await read(request,id)).revision;const lane=page.locator('.timeline-track__lane[data-track-id="v"]');const b=(await lane.boundingBox())!;
  const px=Number((await page.locator('.timeline__zoom-val').innerText()).replace('px/s',''));
  const empty=(await page.locator('.timeline__empty-area').boundingBox())!;
  await page.mouse.move(b.x+6*px,empty.y+3);await page.mouse.down();await page.mouse.move(b.x+2,b.y+2,{steps:8});
  await expect(page.getByTestId('timeline-selection-box')).toBeVisible();await page.mouse.up();
  await expect(page.locator('.clip-block[aria-pressed="true"]')).toHaveCount(2);expect((await read(request,id)).revision).toBe(before);
  await page.screenshot({path:'../../.artifacts/basic-fixes-20261024/timeline/marquee-selection.png'});
});

test('locked attached layer rejects the whole duplicate with a readable message',async({page,request})=>{
  const {id,name}=await fixture(request);
  const sourcePath=fileURLToPath(new URL('../../../src/cutvoke/assets/stickers/arrow_red.png',import.meta.url));
  await apply(request,id,'clip.insert',{trackId:'sticker',createTrackKind:'video',createTrackRole:'sticker',clipId:'child',role:'sticker',
    sourcePath,attachedToClipId:'a',timelineStart:{num:'0',den:'1'},timelineEnd:{num:'5',den:'1'}});
  await apply(request,id,'track.update',{trackId:'sticker',locked:true});const before=(await read(request,id)).revision;
  await open(page,name);await clip(page,'a').click();await page.keyboard.press('Control+d');
  await expect(page.getByText('选中片段或绑定图层所在轨道已锁定，请先解锁',{exact:false}).first()).toBeVisible();
  expect((await read(request,id)).revision).toBe(before);expect((await clips(request,id)).length).toBe(4);
});

test('group audio drag allows legal overlaps on an occupied audio lane',async({page,request})=>{
  const {id,name}=await fixture(request,[[0,2],[2,4],[4,5]]);
  const sourcePath=fileURLToPath(new URL('../../../src/cutvoke/assets/audio/tech_minimal.wav',import.meta.url));
  await apply(request,id,'track.add',{trackId:'audio',kind:'audio'});
  for(const [clipId,start,end] of [['sound-a',0,3],['sound-b',1,4],['bed',5,8]] as const)
    await apply(request,id,'clip.insert',{trackId:'audio',clipId,sourcePath,
      timelineStart:{num:String(start),den:'1'},timelineEnd:{num:String(end),den:'1'}});
  await open(page,name);
  await clip(page,'sound-a').click({position:{x:20,y:15}});
  const b=(await clip(page,'sound-b').boundingBox())!;await clip(page,'sound-b').click({position:{x:b.width-12,y:15},modifiers:['Control']});
  const a=(await clip(page,'sound-a').boundingBox())!;const px=Number((await page.locator('.timeline__zoom-val').innerText()).replace('px/s',''));
  await page.mouse.move(a.x+20,a.y+15);await page.mouse.down();await page.mouse.move(a.x+20+2*px,a.y+15,{steps:8});await page.mouse.up();
  await expect.poll(async()=>seconds((await clips(request,id)).find((c:any)=>c.id==='sound-a').timelineStart)).toBe(2);
  expect(seconds((await clips(request,id)).find((c:any)=>c.id==='sound-b').timelineStart)).toBe(3);
  expect(seconds((await clips(request,id)).find((c:any)=>c.id==='bed').timelineStart)).toBe(5);
});

test('Ctrl A selects and batch-edits all 500 clips without truncation',async({page,request})=>{
  const {id,name}=await fixture(request,[[0,1],[1,2],[2,3]]);
  const template=(await clips(request,id))[0];await apply(request,id,'clip.batch',{clipIds:['a','b','c'],action:'remove'});
  const items=Array.from({length:500},(_,i)=>({trackId:'v',trackIndex:0,trackKind:'video',clip:{...template,id:`large-${i}`,
    timelineStart:{num:String(i),den:'1'},timelineEnd:{num:String(i+1),den:'1'}}}));
  await apply(request,id,'clip.paste',{items,timelineStart:{num:'0',den:'1'}});
  await page.goto('/');await page.getByRole('button',{name:`打开工程 ${name}`}).click();
  await page.getByRole('slider',{name:'时间线播放头'}).focus();await page.keyboard.press('Control+a');
  await expect(page.getByRole('heading',{name:'已选择 500 个片段'})).toBeVisible();
  await page.getByLabel('批量音量 (%)').fill('50');await page.getByRole('button',{name:'应用批量音频属性'}).click();
  await expect.poll(async()=>(await clips(request,id)).filter((c:any)=>seconds(c.volume)===.5).length).toBe(500);
  await page.getByRole('button',{name:'撤销',exact:true}).click();
  await expect.poll(async()=>(await clips(request,id)).filter((c:any)=>seconds(c.volume)===1).length).toBe(500);
  await page.screenshot({path:'../../.artifacts/basic-fixes-20261024/timeline/500-selected.png'});
});
