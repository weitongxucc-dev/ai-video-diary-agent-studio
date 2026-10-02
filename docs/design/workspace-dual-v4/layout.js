const layoutShell = document.querySelector('#shell');
const layout = { nav:188, assistant:280, navClosed:false, assistantClosed:false, workspaceClosed:false };
const widthFor = key => layout[key+'Closed'] ? 36 : layout[key];
const clamp = (value, min, max) => Math.min(max,Math.max(min,value));
function limits(key) {
  const other = key==='nav' ? widthFor('assistant') : widthFor('nav');
  const remaining = layout.workspaceClosed ? 36 : 360;
  return key==='nav' ? [150,Math.min(320,window.innerWidth-other-remaining)]
    : [220,Math.min(420,window.innerWidth-other-remaining)];
}
function drawLayout() {
  for(const key of ['nav','assistant']) {
    if(!layout[key+'Closed']) {
      const [min,max]=limits(key);
      layout[key]=clamp(layout[key],min,Math.max(min,max));
    }
  }
  layoutShell.style.setProperty('--nav-width',widthFor('nav')+'px');
  layoutShell.style.setProperty('--assistant-width',widthFor('assistant')+'px');
  layoutShell.classList.toggle('nav-collapsed',layout.navClosed);
  layoutShell.classList.toggle('assistant-collapsed',layout.assistantClosed);
  layoutShell.classList.toggle('workspace-collapsed',layout.workspaceClosed);
  for(const [id,key] of [['navDivider','nav'],['assistantDivider','assistant']]) {
    const [min,max]=limits(key), node=document.querySelector('#'+id);
    node.setAttribute('aria-valuemin',String(min));
    node.setAttribute('aria-valuemax',String(max));
    node.setAttribute('aria-valuenow',String(layout[key]));
    node.setAttribute('aria-valuetext',layout[key]+' 像素');
  }
}
function toggleColumn(key, closed) { layout[key+'Closed']=closed; drawLayout(); }
document.querySelector('#collapseNav').addEventListener('click',()=>toggleColumn('nav',true));
document.querySelector('#restoreNav').addEventListener('click',()=>toggleColumn('nav',false));
document.querySelector('#collapseAssistant').addEventListener('click',()=>toggleColumn('assistant',true));
document.querySelector('#restoreAssistant').addEventListener('click',()=>toggleColumn('assistant',false));
document.querySelector('#collapseWorkspace').addEventListener('click',()=>toggleColumn('workspace',true));
document.querySelector('#restoreWorkspace').addEventListener('click',()=>toggleColumn('workspace',false));
for(const [id,key] of [['navDivider','nav'],['assistantDivider','assistant']]) {
  const divider=document.querySelector('#'+id);
  divider.addEventListener('pointerdown',event=>{
    if(layout[key+'Closed']||layout.workspaceClosed)return;
    event.preventDefault();
    divider.setPointerCapture(event.pointerId);
    const starting=layout[key], x=event.clientX;
    document.body.classList.add('resizing');
    divider.classList.add('dragging');
    const move=e=>{
      const [min,max]=limits(key);
      layout[key]=clamp(starting+e.clientX-x,min,Math.max(min,max));
      drawLayout();
    };
    const stop=()=>{
      divider.removeEventListener('pointermove',move);
      divider.removeEventListener('pointerup',stop);
      divider.removeEventListener('pointercancel',stop);
      document.body.classList.remove('resizing');
      divider.classList.remove('dragging');
    };
    divider.addEventListener('pointermove',move);
    divider.addEventListener('pointerup',stop);
    divider.addEventListener('pointercancel',stop);
  });
  divider.addEventListener('keydown',event=>{
    const [min,max]=limits(key);
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();
    layout[key]=event.key==='Home'?min:event.key==='End'?max:
      clamp(layout[key]+(event.key==='ArrowRight'?12:-12),min,Math.max(min,max));
    drawLayout();
  });
}
window.addEventListener('resize',drawLayout);
drawLayout();
