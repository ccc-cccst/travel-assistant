const {test, expect}=require('playwright/test');
const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const experiments=path.join(root,'result/experiments');

async function loadSpec(page, input){
  const spec=JSON.parse(fs.readFileSync(path.join(experiments,input,'request.json'),'utf8'));
  spec.request_id=randomUUID().replaceAll('-','');
  const store=path.join(root,'result/tripspec',spec.request_id);
  fs.mkdirSync(store,{recursive:true});
  fs.writeFileSync(path.join(store,'latest.json'),JSON.stringify(spec));
  await page.goto('/tripspec?id='+spec.request_id);
  await page.getByRole('button',{name:'按这些需求规划'}).click();
}

test('用完整分店名交付指定酒店的行程', async({page})=>{
  await loadSpec(page,'P036_delivery_adoption/live_02/easy_174');
  await expect(page.getByRole('button',{name:'推荐方案'})).toBeVisible({timeout:65000});
  await expect(page.locator('.plan-day').first().getByText(/武汉斯威特酒店.*华中科技大学店/)).toBeVisible();
});

test('手机上显示首要体验缺证据与其余行程', async({page})=>{
  await page.setViewportSize({width:390,height:844});
  await loadSpec(page,'P037_open_requirements/contrasts_02/shanghai_books');
  await expect(page.getByRole('button',{name:'推荐方案'})).toBeVisible({timeout:65000});
  await expect(page.locator('#plan-result').getByText(/核心体验待补.*二手寄卖店/)).toBeVisible();
  await expect(page.getByText(/本地快照未找到可试排地点/)).toBeVisible();
  await expect(page.locator('.plan-day')).toHaveCount(3);
});
