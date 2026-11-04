function installLifecycle({app,win,gui,iconPath,electron=require('electron')}) {
  const {Tray,Menu,nativeImage,dialog}=electron;
  let quitting=false,language='ko',notified=false;
  const text=(ko,zh)=>language==='zh-TW'?zh:ko;
  const show=()=>{if(win.isDestroyed())return;if(win.isMinimized())win.restore();win.show();win.focus();};
  const image=nativeImage.createFromPath(iconPath);
  // Never hide the only window if Windows could not create a usable tray icon.
  if(image.isEmpty())throw new Error('DMT tray icon is missing');
  const tray=new Tray(image);
  function refresh(){
    tray.setToolTip(`DMT Analysis Studio v${app.getVersion()}`);
    tray.setContextMenu(Menu.buildFromTemplate([
      {label:text('창 열기','開啟視窗'),click:show},
      {type:'separator'},
      {label:text('완전히 종료','完全結束'),click:()=>app.quit()}
    ]));
  }
  tray.on('click',show);tray.on('double-click',show);tray.on('balloon-click',show);
  app.on('activate',show);
  win.on('close',event=>{
    if(quitting)return;
    event.preventDefault();win.hide();
    if(!notified){
      notified=true;
      try{tray.displayBalloon({title:'DMT Analysis Studio',content:text('트레이에서 계속 실행됩니다. 완전히 종료하려면 트레이 메뉴를 사용하세요.','程式會繼續在系統匣執行。如需完全結束，請使用系統匣選單。'),noSound:true});}catch{}
    }
  });
  app.on('before-quit',event=>{
    if(gui.busy||gui.pending.size){
      event.preventDefault();quitting=false;show();
      dialog.showMessageBoxSync(win,{type:'info',title:'DMT Analysis Studio',message:text('진행 중인 작업을 완료하거나 중지한 뒤 종료하세요.','請先完成或停止目前工作，再結束程式。')});
      return;
    }
    // app.quit also handles the updater's explicit install/restart request.
    quitting=true;gui.close();
  });
  win.on('session-end',()=>{quitting=true;gui.close();});
  app.on('will-quit',()=>{if(!tray.isDestroyed())tray.destroy();});
  refresh();
  return {show,setLanguage:value=>{language=value==='zh-TW'?'zh-TW':'ko';refresh();}};
}
module.exports={installLifecycle};
