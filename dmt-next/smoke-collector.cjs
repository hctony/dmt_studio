/* Real Electron-owned Go lifecycle + Python receipt/index interoperability. */
const {app}=require('electron');
const path=require('node:path'),fs=require('node:fs'),assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {startCollector}=require('./collector-service');
const root=path.join(__dirname,'smoke-user-data','collector-'+Date.now());
app.setPath('userData',root);app.disableHardwareAcceleration();
app.whenReady().then(async()=>{
 let service;
 try{
  service=await startCollector(app);assert(process.env.DMT_COLLECTOR_URL);assert.equal(process.env.DMT_COLLECTOR_REQUIRED,'1');
  const proc=spawn('python',[path.join(__dirname,'smoke-collector.py')],{env:{...process.env,DMT_CACHE_DIR:path.join(root,'cache')},windowsHide:true,stdio:['ignore','pipe','pipe']});
  proc.stdout.pipe(process.stdout);proc.stderr.pipe(process.stderr);
  await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{proc.kill();reject(new Error('Collector integration timed out'));},45000);proc.once('error',reject);proc.once('exit',code=>{clearTimeout(timer);code===0?resolve():reject(new Error('Python collector smoke failed: '+code));});});
  await service.drain();service.close();console.log(JSON.stringify({electronOwnedGo:true,sharedPythonIndex:true,cleanDrain:true}));app.quit();
 }catch(error){service?.close();console.error(error);app.exit(1);}
});
