const {test,expect}=require('playwright/test');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');
const output=process.env.SHORTTRIP_BROWSER_OUT;
const load=p=>JSON.parse(fs.readFileSync(p,'utf8').replace(/^\uFEFF/,''));
async function openPlan(page,name){
  const dir=path.join(root,'result/experiments/P034_delivery_and_quality/web_live_01',name);
  const job=load(path.join(dir,'MEASUREMENT.json')).job;
  const id=load(path.join(dir,'request.json')).request_id;
  await page.goto('/tripspec?'+new URLSearchParams({id,job}));
  await page.getByRole('button',{name:'换一家交通更方便的酒店'}).click();
  return {dir,id,job};
}

test('比较换酒店、采纳、刷新并重新打开原方案',async({page})=>{
  const {job,dir}=await openPlan(page,'human_141');
  const accept=page.getByRole('button',{name:'采用新酒店'});
  await expect(accept).toBeVisible({timeout:65000});
  await page.locator('#plan-result').screenshot({path:path.join(output,'before-accept.png')});
  await accept.click();
  const back=page.getByRole('link',{name:'重新打开换酒店前的原方案'});
  await expect(back).toBeVisible();
  const editedJob=new URL(page.url()).searchParams.get('job');
  const editdir=path.join(root,'result/manual',editedJob);
  const status=load(path.join(editdir,'STATUS.json'));
  expect(status.hotel_change.after.city_transit_minutes).toBeLessThan(status.hotel_change.before.city_transit_minutes);
  expect(status.hotel_change.after.total_cost_cny).toBeLessThanOrEqual(10000);
  expect(status.evidence.rejections.budget).toBeGreaterThan(0);
  const itinerary=(d,id)=>load(path.join(d,'CANDIDATE_ARCHIVE.json')).archive.find(c=>c.id===id).itinerary.itinerary
    .map(day=>day.activities.filter(a=>a.type!=='accommodation').map(a=>[a.type,a.position,a.TrainID,a.FlightID]));
  expect(itinerary(editdir,load(path.join(editdir,'PLAN_VIEW.json')).recommended_id))
    .toEqual(itinerary(dir,status.hotel_change.candidate_id));
  fs.writeFileSync(path.join(output,'hotel-evidence.json'),JSON.stringify(status,null,2));
  await page.reload();await expect(back).toBeVisible();
  await back.click();await expect(page.getByRole('button',{name:'换一家交通更方便的酒店'})).toBeVisible();
  expect(new URL(page.url()).searchParams.get('job')).toBe(job);
});

test('指定酒店时返回具体原因并保留原案',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await openPlan(page,'easy_158');
  await expect(page.getByText(/当前要求指定了住宿/)).toBeVisible({timeout:65000});
  await expect(page.getByRole('button',{name:'采用新酒店'})).toHaveCount(0);
  await expect(page.locator('.plan-day')).toHaveCount(2);
  await page.locator('#plan-result').screenshot({path:path.join(output,'fixed-hotel-mobile.png')});
});

test('目录只有一家满足设施的酒店，保持原案',async({page})=>{
  const dir=path.join(root,'result/experiments/P036_delivery_adoption/dev_02/easy_147/direct');
  const job=require('node:crypto').randomUUID().replaceAll('-','');
  fs.cpSync(dir,path.join(root,'result/manual',job),{recursive:true});
  const spec=load(path.join(dir,'request.json'));
  await page.goto('/tripspec?'+new URLSearchParams({id:spec.request_id,job}));
  await page.getByRole('button',{name:'换一家交通更方便的酒店'}).click();
  await expect(page.getByText(/本次未找到满足原要求且接驳更短的酒店/)).toBeVisible({timeout:65000});
  await expect(page.getByRole('button',{name:'采用新酒店'})).toHaveCount(0);
});

test('填写出发日期并继续原来的四人广州行程',async({page})=>{
  const source=load(path.join(root,'result/experiments/P034_delivery_and_quality/web_live_01/human_122/request.json'));
  source.request_id=require('node:crypto').randomUUID().replaceAll('-','');
  const folder=path.join(root,'result/tripspec',source.request_id);
  fs.mkdirSync(folder,{recursive:true});
  fs.writeFileSync(path.join(folder,'latest.json'),JSON.stringify(source));
  await page.goto('/tripspec?id='+source.request_id);
  await page.getByLabel('确认出发日期').fill('2027-09-15');
  await page.locator('#apply').click();
  await expect(page.getByRole('button',{name:'推荐方案'})).toBeVisible({timeout:65000});
  await expect(page.locator('.plan-day')).toHaveCount(5);
  const spec=load(path.join(folder,'latest.json'));
  expect(spec.items.find(i=>i.field==='people').value).toBe(4);
  expect(spec.items.find(i=>i.field==='start_date').value).toBe('2027-09-15');
});
