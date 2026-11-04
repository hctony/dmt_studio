const test=require('node:test');const assert=require('node:assert/strict');const {EventEmitter}=require('node:events');
const {installLifecycle}=require('./lifecycle');
function setup(){
 const app=new EventEmitter();app.getVersion=()=> 'test';
 const win=new EventEmitter();win.visible=true;win.minimized=false;win.isDestroyed=()=>false;
 win.isMinimized=()=>win.minimized;win.restore=()=>win.minimized=false;win.show=()=>win.visible=true;win.focus=()=>{};win.hide=()=>win.visible=false;
 const gui={busy:false,pending:new Map(),closed:0,close(){this.closed++;}};
 let tray,menu;const messages=[];
 class Tray extends EventEmitter{constructor(){super();tray=this;this.destroyed=false;}setContextMenu(value){menu=value;}setToolTip(){}displayBalloon(value){messages.push(value);}isDestroyed(){return this.destroyed;}destroy(){this.destroyed=true;}}
 const lifecycle=installLifecycle({app,win,gui,iconPath:'test.ico',electron:{Tray,Menu:{buildFromTemplate:v=>v},nativeImage:{createFromPath:()=>({isEmpty:()=>false})},dialog:{showMessageBoxSync:(_w,m)=>messages.push(m)}}});
 const event=()=>({prevented:false,preventDefault(){this.prevented=true;}});
 app.quit=()=>{const e=event();app.emit('before-quit',e);if(!e.prevented){win.emit('close',event());app.emit('will-quit');}return e;};
 return {app,win,gui,lifecycle,tray,messages,event,menu:()=>menu};
}
test('close hides without stopping running analysis and tray restores the window',()=>{
 const s=setup();s.gui.busy=true;const e=s.event();s.win.emit('close',e);
 assert.equal(e.prevented,true);assert.equal(s.win.visible,false);assert.equal(s.gui.closed,0);
 s.win.minimized=true;s.tray.emit('click');assert.equal(s.win.visible,true);assert.equal(s.win.minimized,false);
 s.win.emit('close',s.event());assert.equal(s.messages.length,1);s.tray.emit('double-click');assert.equal(s.win.visible,true);
});
test('explicit quit refuses active jobs and can be retried when idle',()=>{
 const s=setup();s.gui.pending.set(1,{});s.win.visible=false;
 assert.equal(s.app.quit().prevented,true);assert.equal(s.gui.closed,0);assert.equal(s.tray.destroyed,false);assert.equal(s.win.visible,true);
 s.gui.pending.clear();assert.equal(s.app.quit().prevented,false);assert.equal(s.gui.closed,1);assert.equal(s.tray.destroyed,true);
});
test('update quit passes through close handler and destroys tray',()=>{
 const s=setup();s.gui.updating=true;s.win.visible=false;
 assert.equal(s.app.quit().prevented,false);assert.equal(s.gui.closed,1);assert.equal(s.tray.destroyed,true);
});
test('tray menus and busy notices follow selected language',()=>{
 const s=setup();assert.equal(s.menu()[0].label,'창 열기');s.lifecycle.setLanguage('zh-TW');
 assert.equal(s.menu()[0].label,'開啟視窗');assert.equal(s.menu()[2].label,'完全結束');
 s.gui.busy=true;s.menu()[2].click();assert.match(s.messages.at(-1).message,/請先完成/);
 s.lifecycle.setLanguage('ko');assert.equal(s.menu()[2].label,'완전히 종료');
});
