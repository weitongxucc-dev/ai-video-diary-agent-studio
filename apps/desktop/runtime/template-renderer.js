// Trusted offline drawing adapter. Template JavaScript runs only in an opaque,
// sandboxed iframe, without Node, preload, IPC, files or network capabilities.
const { app, BrowserWindow, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');

const input = process.argv[process.argv.indexOf('--yaohe-template-renderer')+1];
const spec = JSON.parse(fs.readFileSync(input, 'utf8'));
const output = path.dirname(input);
app.setPath('userData', path.join(output, 'browser-profile'));
app.setPath('sessionData', path.join(output, 'browser-session'));
app.enableSandbox();
// Offscreen output is a bitmap sequence; use Chromium's software compositor
// so sandboxed child frames are present in captures without a display GPU.
app.disableHardwareAcceleration();
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-component-update');
app.commandLine.appendSwitch('disable-crash-reporter');

function placement(overlay, seconds) {
  const keys=overlay.keyframes;
  if(!keys.length)return {x:overlay.x,y:overlay.y,scale:1,opacity:overlay.opacity};
  if(seconds<=keys[0].seconds)return keys[0];
  for(let i=1;i<keys.length;i++){
    const right=keys[i],left=keys[i-1];
    if(seconds<=right.seconds){
      const p=(seconds-left.seconds)/(right.seconds-left.seconds);
      return Object.fromEntries(['x','y','scale','opacity'].map(k=>[k,left[k]+p*(right[k]-left[k])]));
    }
  }
  return keys.at(-1);
}

async function render() {
  const isolated=session.fromPartition(`template-${process.pid}`);
  isolated.setPermissionRequestHandler((_wc,_permission,done)=>done(false));
  isolated.setPermissionCheckHandler(()=>false);
  isolated.on('will-download',event=>event.preventDefault());
  let networkFailure=null;
  isolated.webRequest.onBeforeRequest((details,done)=>{
    if(['data:','about:'].includes(new URL(details.url).protocol))return done({cancel:false});
    networkFailure=`Template dependency blocked: ${new URL(details.url).protocol}`;
    done({cancel:true});
  });
  const window=new BrowserWindow({show:false,width:spec.width,height:spec.height,
    useContentSize:true,frame:false,transparent:true,backgroundColor:'#00000000',
    webPreferences:{session:isolated,sandbox:true,contextIsolation:true,nodeIntegration:false,
      webSecurity:true,webviewTag:false,backgroundThrottling:false}});
  window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
  window.webContents.on('will-attach-webview',event=>event.preventDefault());
  window.webContents.on('will-navigate',event=>event.preventDefault());
  const child=spec.document,definition=spec.overlay.definition;
  // Set the source viewport before its scripts run: upstream autofit programs
  // measure their initial layout and must not see the iframe's 300x150 default.
  const host=`<!doctype html><style>html,body{margin:0;overflow:hidden;background:transparent}iframe{position:absolute;border:0;transform-origin:center;width:${definition.width}px;height:${definition.height}px}</style><iframe sandbox="allow-scripts" allow="camera 'none';microphone 'none';geolocation 'none'"></iframe><script>
    const f=document.querySelector('iframe');let pending=new Map();let ready;
    const loaded=new Promise(r=>ready=r);
    addEventListener('message',e=>{if(e.source!==f.contentWindow)return;if(e.data?.kind==='loaded')ready();if(e.data?.kind==='drawn'){const p=pending.get(e.data.ticket);if(p){pending.delete(e.data.ticket);e.data.error?p.reject(Error(e.data.error)):p.resolve()}}});
    f.srcdoc=${JSON.stringify(child).replaceAll('<', '\\u003c')};
    window.draw=async function(data){await loaded;Object.assign(f.style,data.style);await new Promise((resolve,reject)=>{pending.set(data.ticket,{resolve,reject});f.contentWindow.postMessage(data,'*')});await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))};
    </script>`;
  await window.loadURL('data:text/html;charset=utf-8,'+encodeURIComponent(host));
  window.setBackgroundColor('#00000000');
  const overlay=spec.overlay;
  const frames=[];
  for(let i=0;i<spec.frame_count;i++){
    const global=(spec.first_frame+i)/spec.fps;
    const t=global-overlay.start_seconds;
    const at=placement(overlay,t);
    const scale=spec.width*overlay.width/definition.width*at.scale;
    const style={width:`${definition.width}px`,height:`${definition.height}px`,
      left:`${spec.width*at.x-definition.width/2}px`,top:`${spec.height*at.y-definition.height/2}px`,
      transform:`scale(${scale})`,opacity:t<0||global>=overlay.end_seconds?'0':String(at.opacity)};
    const data={kind:'draw',ticket:i,t:Math.max(0,t)+(overlay.animation_offset_seconds??0),style,bindings:definition.bindings,values:definition.values};
    await window.webContents.executeJavaScript(`window.draw(${JSON.stringify(data)})`);
    if(networkFailure)throw Error(networkFailure);
    const file=`frame-${String(i).padStart(7,'0')}.png`;
    const picture=await window.webContents.capturePage(undefined,{stayHidden:true,stayAwake:true});
    // capturePage follows the display's native scale (2x on Retina). Media
    // frames must use the exact requested pixel size on every display.
    const png=picture.resize({width:spec.width,height:spec.height,quality:'best'}).toPNG();
    if(png.readUInt32BE(16)!==spec.width||png.readUInt32BE(20)!==spec.height)
      throw Error('Template pixel dimensions differ from the requested media size');
    fs.writeFileSync(path.join(output,file),png,{flag:'wx'});
    frames.push(file);
  }
  fs.writeFileSync(path.join(output,'frames.json'),JSON.stringify({frames,width:spec.width,height:spec.height}),{flag:'wx'});
  window.destroy();
}
app.whenReady().then(render).then(()=>app.exit(0)).catch(error=>{
  process.stderr.write(String(error.message).slice(0,2000)+'\n');app.exit(1);
});
