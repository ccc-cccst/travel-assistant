// Reproduce the user's blank-cost-limit case against the live local backend.
const fs=require('node:fs/promises'),path=require('node:path');
async function main(){
 const dir=path.resolve('result/experiments/P002_browser');await fs.mkdir(dir,{recursive:true});
 const tab=await(await fetch('http://127.0.0.1:9231/json/new?http://127.0.0.1:8783/',{method:'PUT'})).json();
 const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 let n=0;const pending=new Map();ws.onmessage=e=>{const v=JSON.parse(e.data),p=pending.get(v.id);if(p){pending.delete(v.id);v.error?p.j(Error(JSON.stringify(v.error))):p.r(v.result)}};
 const rpc=(method,params={})=>new Promise((r,j)=>{const id=++n;pending.set(id,{r,j});ws.send(JSON.stringify({id,method,params}))});
 const evaluate=async expression=>(await rpc('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true})).result.value;
 const until=async expression=>{for(let i=0;i<150;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100))}throw Error('Timeout: '+expression)};
 try{
  await rpc('Page.enable');await rpc('Emulation.setDeviceMetricsOverride',{width:1366,height:1000,deviceScaleFactor:1,mobile:false});
  await until("document.querySelector('#origin option')&&typeof renderPlanningStatus==='function'");
  await fs.writeFile(path.join(dir,'accessibility.json'),JSON.stringify(await rpc('Accessibility.getFullAXTree'),null,2));
  await evaluate("document.getElementById('origin').value='成都';document.getElementById('destination').value='苏州';document.getElementById('destination').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[name=days]').value='4';document.querySelector('[name=budget]').value='';document.querySelector('[name=mode]').value='airplane';document.querySelector('[name=pace]').value='full';document.querySelector('[name=mobility]').value='less_walking'");
  await until("document.querySelector('#cuisines option[value=\"江浙菜\"]')");
  await evaluate("document.getElementById('form').requestSubmit()");
  await until("document.getElementById('start').disabled===false");
  const blank=await evaluate("document.querySelector('[name=budget]').value===''");
  await evaluate("document.getElementById('start').click()");
  await until("document.getElementById('result').innerText.includes('当前数据中未找到符合')");
  const message=await evaluate("document.getElementById('result').innerText");
  if(!blank||!message.includes('只坐飞机')||message.includes('预算'))throw Error(message);
  const retry=await evaluate("document.getElementById('start').disabled===false");if(!retry)throw Error('Retry disabled');
  const screenshot=await rpc('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  await fs.writeFile(path.join(dir,'transport-unavailable.png'),Buffer.from(screenshot.data,'base64'));
  const fallback=await evaluate("renderPlanningStatus({state:'no_verified_result'});document.getElementById('result').innerText");
  if(fallback.includes('预算'))throw Error('Old ambiguous fallback retained');
  const timeout=await evaluate("renderPlanningStatus({state:'search_timeout'});document.getElementById('result').innerText");
  if(!timeout.includes('时间'))throw Error(timeout);
  const result={blank_money_limit:blank,transport_failure_message:message,retry_enabled:retry,legacy_fallback:fallback,timeout_message:timeout,real_live_backend:true};
  await fs.writeFile(path.join(dir,'RESULT.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{ws.close();await fetch('http://127.0.0.1:9231/json/close/'+tab.id)}
}
main().catch(e=>{console.error(e);process.exitCode=1});
