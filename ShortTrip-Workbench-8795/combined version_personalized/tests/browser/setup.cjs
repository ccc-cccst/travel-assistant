const path=require('node:path');
const {spawn}=require('node:child_process');
const {setTimeout:delay}=require('node:timers/promises');
module.exports=async()=>{
  const root=path.resolve(__dirname,'../..');
  const port=process.env.SHORTTRIP_BROWSER_PORT || '18974';
  const venv=path.resolve(root,'../ShortTrip/.venv');
  const native=require('node:child_process').execFileSync(path.join(venv,'Scripts/python.exe'),
      ['-c','import sys;print(sys._base_executable)'],{encoding:'utf8',windowsHide:true}).trim();
  const child=spawn(native,
                    ['-B','-X','utf8','-c',"import sys,site,runpy;site.addsitedir(sys.argv.pop(1));runpy.run_module('chinatravel.personalization.web',run_name='__main__')",path.join(venv,'Lib/site-packages'),'--port',port],
                    {cwd:root,stdio:'ignore',windowsHide:true});
  const done=new Promise(resolve=>child.once('exit',resolve));
  try{
    for(let n=0;n<150;n++){
      if(child.exitCode!==null)throw Error('本地测试服务器启动失败');
      try{if((await fetch('http://127.0.0.1:'+port+'/options')).ok)return async()=>{child.kill();await done;};}
      catch{}
      await delay(300);
    }
    throw Error('本地测试服务器启动超时');
  }catch(e){child.kill();throw e;}
};
