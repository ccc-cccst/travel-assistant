// Operational Chrome CDP review; uses the real local HTTP backend and no npm packages.
const fs=require('node:fs/promises');
const path=require('node:path');
async function main(){
 const live=process.argv.includes('--live'),base=live?'http://127.0.0.1:8783/':'http://127.0.0.1:8782/';
 const dir=path.resolve('result/experiments/'+(live?'P001_browser_live':'P001_browser')+'/output/playwright');await fs.mkdir(dir,{recursive:true});
 const tab=await (await fetch('http://127.0.0.1:9231/json/new?'+base,{method:'PUT'})).json();
 const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 let seq=0;const pending=new Map();ws.onmessage=e=>{const v=JSON.parse(e.data);if(pending.has(v.id)){const {r,j}=pending.get(v.id);pending.delete(v.id);v.error?j(Error(JSON.stringify(v.error))):r(v.result)}};
 const send=(method,params={})=>new Promise((r,j)=>{const id=++seq;pending.set(id,{r,j});ws.send(JSON.stringify({id,method,params}))});
 const evaluate=async expression=>(await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true})).result.value;
 const until=async (expr,attempts=100)=>{for(let i=0;i<attempts;i++){if(await evaluate(expr))return;await new Promise(r=>setTimeout(r,100))}throw Error('Timed out: '+expr)};
 try{
 await send('Page.enable');await send('Runtime.enable');await send('Emulation.setDeviceMetricsOverride',{width:1366,height:1000,deviceScaleFactor:1,mobile:false});
 await until("document.getElementById('interests')?.children.length>0");
 await fs.writeFile(path.join(dir,'desktop-accessibility.json'),JSON.stringify(await send('Accessibility.getFullAXTree'),null,2));
 await evaluate("document.querySelector('[name=days]').value='6';document.querySelector('[name=days]').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#interests input[value=museum]').click();document.getElementById('form').requestSubmit()");
 await until("document.getElementById('start').disabled===false");
 const six=await evaluate("document.getElementById('review').innerText");
 if(!six.includes('6天')||!six.includes('博物馆'))throw Error(six);
 if(live){
   await evaluate("document.querySelector('[name=days]').value='2';document.querySelector('[name=mode]').value='train';document.querySelector('[name=days]').dispatchEvent(new Event('input',{bubbles:true}));document.getElementById('form').requestSubmit()");
   await until("document.getElementById('start').disabled===false");
   await evaluate("document.getElementById('start').click()");
   await until("document.getElementById('result').innerText.includes('已通过当前硬约束核验')",2000);
 }else{
   await evaluate("document.getElementById('start').click()");
   await until("document.getElementById('result').innerText.includes('模型服务尚未配置')");
 }
 const screenshot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});await fs.writeFile(path.join(dir,'desktop.png'),Buffer.from(screenshot.data,'base64'));
 await evaluate("document.querySelector('[name=mode]').value='airplane';document.querySelector('[name=nature_language]').value='我只坐火车';document.getElementById('form').requestSubmit()");
 await until("document.getElementById('review').innerText.includes('冲突')");
 if(!(await evaluate("document.getElementById('start').disabled")))throw Error('Conflicting modes were not blocked');
 await send('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});
 const overflow=await evaluate('document.documentElement.scrollWidth>window.innerWidth');if(overflow)throw Error('Mobile horizontal overflow');
 const shot=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});await fs.writeFile(path.join(dir,'mobile.png'),Buffer.from(shot.data,'base64'));
 const report={real_http_backend:true,url:base,six_day_input:true,interest_confirmation:true,
               missing_model_state:!live,text_form_conflict_blocked:true,mobile_overflow:false,
               live_model_planning:live,desktop_review:six};
 await fs.writeFile(path.join(dir,'RESULT.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{ws.close();await fetch('http://127.0.0.1:9231/json/close/'+tab.id)}
}
main().catch(e=>{console.error(e);process.exitCode=1});
