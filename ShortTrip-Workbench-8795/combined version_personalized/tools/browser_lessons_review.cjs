// Real local backend: error code, occurrence record and applied lesson must agree.
const fs=require('node:fs/promises'),path=require('node:path');
async function main(){
 const dir=path.resolve('result/experiments/P003_browser');await fs.mkdir(dir,{recursive:true});
 const tab=await(await fetch('http://127.0.0.1:9231/json/new?http://127.0.0.1:8783/',{method:'PUT'})).json();
 const ws=new WebSocket(tab.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
 let next=0;const pending=new Map();ws.onmessage=e=>{const v=JSON.parse(e.data),p=pending.get(v.id);if(p){pending.delete(v.id);v.error?p.j(Error(JSON.stringify(v.error))):p.r(v.result)}};
 const rpc=(method,params={})=>new Promise((r,j)=>{const id=++next;pending.set(id,{r,j});ws.send(JSON.stringify({id,method,params}))});
 const evaluate=async expression=>(await rpc('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true})).result.value;
 const until=async expression=>{for(let i=0;i<150;i++){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100))}throw Error('Timeout '+expression)};
 try{
  await rpc('Page.enable');await rpc('Emulation.setDeviceMetricsOverride',{width:1366,height:1000,deviceScaleFactor:1,mobile:false});
  await until("document.querySelector('#origin option')&&typeof diagnosticHtml==='function'");
  await fs.writeFile(path.join(dir,'accessibility.json'),JSON.stringify(await rpc('Accessibility.getFullAXTree'),null,2));
  await evaluate("window.reviewFetch=window.fetch;window.fetch=async(...args)=>{const r=await window.reviewFetch(...args);if(args[0]==='/plan')window.reviewJob=await r.clone().json();return r};document.getElementById('origin').value='成都';document.getElementById('destination').value='苏州';document.getElementById('destination').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[name=days]').value='4';document.querySelector('[name=budget]').value='';document.querySelector('[name=mode]').value='airplane'");
  await until("document.querySelector('#cuisines option[value=\"江浙菜\"]')");
  await evaluate("document.getElementById('form').requestSubmit()");await until("!document.getElementById('start').disabled");
  await evaluate("document.getElementById('start').click()");
  await until("document.querySelector('#result .diagnostic summary')?.innerText.includes('TRIP-TRANSPORT-001')");
  const job=await evaluate('window.reviewJob.id');
  const status=await(await fetch('http://127.0.0.1:8783/job?id='+job)).json();
  if(status.error_code!=='TRIP-TRANSPORT-001'||!status.lesson_ids.includes('L002'))throw Error(JSON.stringify(status));
  const local=JSON.parse(await fs.readFile(path.resolve(status.diagnostic.incident_file),'utf8'));
  if(local.incident_id!==status.incident_id||local.request.money_budget!==null)throw Error('Occurrence differs from displayed failure');
  const applied=JSON.parse(await fs.readFile(path.resolve('result/manual',job,'lessons_applied.json'),'utf8'));
  if(!applied.checks.some(r=>r.lesson_id==='L002'&&r.status==='blocked'))throw Error('Lesson not applied');
  await evaluate("document.querySelector('#result .diagnostic').open=true");
  const screenshot=await rpc('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  await fs.writeFile(path.join(dir,'error-details.png'),Buffer.from(screenshot.data,'base64'));
  const malformed=await fetch('http://127.0.0.1:8783/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start_city:'南京',target_city:'上海',days:2,people_number:2,profile:[]})});
  const bad=await malformed.json();if(malformed.status!==400||bad.error_code!=='TRIP-INPUT-003')throw Error(JSON.stringify(bad));
  await fs.access(path.resolve(bad.diagnostic.incident_file));
  const missing=await fetch('http://127.0.0.1:8783/job?id=not-a-real-job');const notFound=await missing.json();
  if(missing.status!==404||notFound.error_code!=='TRIP-JOB-001')throw Error(JSON.stringify(notFound));
  const result={transport_error_code:status.error_code,incident_id:status.incident_id,blank_money_limit_confirmed:true,
    lesson_applied:'L002',model_calls:status.model_calls,incident_file_matches_response:true,
    malformed_input_error:bad.error_code,missing_job_error:notFound.error_code,real_backend:true};
  await fs.writeFile(path.join(dir,'RESULT.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{ws.close();await fetch('http://127.0.0.1:9231/json/close/'+tab.id)}
}
main().catch(e=>{console.error(e);process.exitCode=1});
