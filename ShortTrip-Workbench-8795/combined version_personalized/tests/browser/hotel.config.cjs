const path = require('node:path');
const {defineConfig} = require('playwright/test');
const root = path.resolve(__dirname, '../..');
const output = process.env.SHORTTRIP_BROWSER_OUT;
if (!output) throw Error('Set SHORTTRIP_BROWSER_OUT to a fresh experiment directory');
const port=Number(process.env.SHORTTRIP_BROWSER_PORT || 18974);
module.exports=defineConfig({
  testDir:__dirname, testMatch:process.env.SHORTTRIP_BROWSER_SPEC || 'hotel.spec.cjs', workers:1,
  timeout:100000, retries:0,
  reporter:[['list'],['junit',{outputFile:path.join(output,'junit.xml')}],
            ['html',{outputFolder:path.join(output,'html'),open:'never'}]],
  outputDir:path.join(output,'traces'), use:{channel:'msedge',baseURL:'http://127.0.0.1:'+port,
    viewport:{width:1220,height:920},trace:'retain-on-failure'},
  globalSetup:require.resolve('./setup.cjs'),
});
