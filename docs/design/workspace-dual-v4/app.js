const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const fmt = (seconds) => `00:${String(Math.floor(seconds)).padStart(2,'0')}`;

const clips = [
  { name:'整机开场', source:'整机_01.mov', start:0, end:6, focus:'whole', subtitle:'这台设备怎么使用？' },
  { name:'准备原料', source:'原料_02.mov', start:6, end:11, focus:'feed', subtitle:'先准备好需要处理的原料。' },
  { name:'进料口特写', source:'进料口_03.mov', start:11, end:17, focus:'feed', subtitle:'从进料口缓慢倒入。' },
  { name:'启动设备', source:'按钮_04.mov', start:17, end:23, focus:'control', subtitle:'确认护盖关闭后启动。' },
  { name:'观察运行', source:'运行_05.mov', start:23, end:29, focus:'body', subtitle:'运行时留意指示灯。' },
  { name:'出料过程', source:'出料_06.mov', start:29, end:35, focus:'outlet', subtitle:'处理完成后从这里取出。' },
  { name:'清洁部件', source:'清洁_07.mov', start:35, end:41, focus:'whole', subtitle:'停机后再清洁接触部件。' },
  { name:'收尾画面', source:'收尾_08.mov', start:41, end:48, focus:'whole', subtitle:'操作完成，检查设备状态。' }
];
const candidates = [
  { name:'进料口_备用A.mov', duration:'00:08', note:'同机位 · 光线较均匀', focus:'feed' },
  { name:'进料口_备用B.mov', duration:'00:07', note:'近景 · 动作更完整', focus:'feed' },
  { name:'设备侧面_备用.mov', duration:'00:09', note:'侧面 · 可见操作手势', focus:'body' }
];
const state = {
  scene:'overview', ratio:'wide', selected:2, playhead:13, panelOpen:false, panelMode:'trim',
  previewAsset:null, proposal:null, textLine:2, textOp:'caption', captionDraft:clips[2].subtitle, recordPhase:'idle',
  arrowMode:'position', arrowPosition:'left-top', arrowName:'进料口', arrowPurpose:'原料从此进入', arrowStart:'11.0', arrowEnd:'17.0',
  trimStart:'11.0', trimEnd:'17.0', playing:false, timer:null, undoStack:[], redoStack:[], assistantCustom:false, taskKind:'device'
};

function alternateSvg() {
  const store=state.taskKind==='store',label=store?'门店日常画面示意':state.taskKind==='season'?'新品素材混剪示意':'本地素材混剪示意';
  const subject=store
    ? '<rect x="160" y="125" width="480" height="262" rx="7" fill="#b9bfc0" stroke="#717b7f" stroke-width="5"/><rect x="200" y="160" width="150" height="150" fill="#d9dcda" stroke="#889295" stroke-width="4"/><path d="M212 185H338M212 220H338M212 255H338" stroke="#a4adaf" stroke-width="9"/><rect x="408" y="167" width="178" height="185" fill="#e2e4e2" stroke="#858f92" stroke-width="4"/><path d="M420 226H575" stroke="#9aa3a5" stroke-width="10"/><rect x="250" y="338" width="300" height="45" rx="7" fill="#788286"/><circle cx="366" cy="248" r="22" fill="#7a8588"/>'
    : '<rect x="255" y="92" width="290" height="300" rx="15" fill="#b6bdbd" stroke="#687478" stroke-width="5"/><rect x="280" y="116" width="240" height="214" rx="7" fill="#d9ddda"/><path d="M330 300C300 223 315 180 370 155C388 209 382 267 330 300Z" fill="#899696"/><path d="M400 291C387 199 430 161 485 146C491 217 464 272 400 291Z" fill="#a0abaa"/><path d="M325 315H490" stroke="#687579" stroke-width="5"/><rect x="292" y="350" width="216" height="18" rx="7" fill="#7d898c"/>';
  return `<svg viewBox="0 0 800 450" role="img" aria-label="${label}"><rect width="800" height="450" fill="#e4e6e4"/><path d="M0 390H800V450H0Z" fill="#adb5b5"/><path d="M50 400H750" stroke="#8b9699" stroke-width="3"/>${subject}<text x="35" y="40" fill="#566266" font-size="17" font-family="PingFang SC, sans-serif">${label} · ${escapeHtml(selectedClip().name)}</text></svg>`;
}
function machineSvg() {
  if(state.taskKind!=='device')return alternateSvg();
  const focus = state.previewAsset?.focus || clips[state.selected].focus;
  const marks = { whole:[355,218,286,270], feed:[314,121,105,104], control:[490,243,98,82], body:[355,257,270,204], outlet:[628,358,92,53] };
  const [x,y,w,h] = marks[focus];
  return `<svg viewBox="0 0 800 450" role="img" aria-label="灰阶设备结构示意，当前镜头聚焦${escapeHtml(clips[state.selected].name)}">
    <defs><linearGradient id="wall" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#e8e9e7"/><stop offset="1" stop-color="#c7cbca"/></linearGradient><linearGradient id="metal" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#c4c9c9"/><stop offset=".55" stop-color="#939b9d"/><stop offset="1" stop-color="#b4babb"/></linearGradient><linearGradient id="darkMetal" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#818a8d"/><stop offset="1" stop-color="#596368"/></linearGradient></defs>
    <rect width="800" height="450" fill="url(#wall)"/><path d="M0 370H800" stroke="#9fa7a9" stroke-width="3"/><path d="M0 374H800V450H0Z" fill="#aeb5b5"/><path d="M60 404H742" stroke="#8b9496" stroke-width="2"/>
    <rect x="210" y="345" width="495" height="27" rx="9" fill="#596469"/><rect x="227" y="373" width="20" height="32" rx="3" fill="#6c777a"/><rect x="663" y="373" width="20" height="32" rx="3" fill="#6c777a"/>
    <path d="M291 146L438 146L417 204L314 204Z" fill="url(#metal)" stroke="#5f696d" stroke-width="4"/><path d="M316 164H412" stroke="#6d777b" stroke-width="3"/><path d="M337 201V223H393V201" fill="#788286" stroke="#5b6569" stroke-width="3"/>
    <rect x="252" y="219" width="348" height="127" rx="18" fill="url(#metal)" stroke="#5c666a" stroke-width="5"/><path d="M275 237V324" stroke="#dfe1df" stroke-width="3" opacity=".7"/><path d="M425 223V344" stroke="#727c7f" stroke-width="3" opacity=".55"/>
    <rect x="471" y="249" width="102" height="68" rx="6" fill="#5f6a6d" stroke="#3b474c" stroke-width="4"/><rect x="483" y="260" width="77" height="33" rx="3" fill="#b8c5c3"/><path d="M490 282L506 271L523 277L543 266" fill="none" stroke="#627577" stroke-width="3"/><circle cx="539" cy="305" r="6" fill="#d8ddda"/>
    <path d="M600 292L654 304V336H600Z" fill="url(#darkMetal)" stroke="#515e62" stroke-width="4"/><rect x="648" y="337" width="72" height="19" rx="5" fill="#8e989a" stroke="#596569" stroke-width="3"/><rect x="603" y="369" width="118" height="13" rx="5" fill="#737e81"/>
    <path d="M222 319C175 312 175 288 135 290" fill="none" stroke="#717c7f" stroke-width="9"/><path d="M132 291V363" stroke="#717c7f" stroke-width="9"/><rect x="120" y="361" width="48" height="13" rx="5" fill="#586469"/>
    <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="14" fill="none" stroke="#f8f8f6" stroke-width="4" stroke-dasharray="10 7" opacity=".88"/><path d="M${x+5} ${y+5}h25" stroke="#3d474b" stroke-width="4"/><circle cx="${x+w-12}" cy="${y+12}" r="6" fill="#343e43" stroke="#fff" stroke-width="2"/>
    <text x="45" y="42" fill="#525d61" font-size="16" font-family="PingFang SC, sans-serif">设备结构示意 · 本地素材剪辑</text>
  </svg>`;
}

function selectedClip() { return clips[state.selected]; }
function setScene(scene) {
  state.scene=scene;
  if(scene==='text'){state.textLine=state.selected;state.captionDraft=selectedClip().subtitle;}
  state.panelOpen=scene!=='overview';
  state.panelMode=scene==='replace'?'replace':scene==='text'?'text':scene==='sound'?'sound':scene==='device'?'device':'trim';
  state.previewAsset=null;state.proposal=null;
  render();
}
function render() {
  $$('.scene-tabs button').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.scene===state.scene)));
  $$('.ratio-buttons button').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.ratio===state.ratio)));
  $('#previewFrame').className=`preview-frame ${state.ratio}`;
  const clip=selectedClip();
  $('#previewTitle').textContent=`镜头 ${String(state.selected+1).padStart(2,'0')} · ${clip.name}`;
  $('#previewSource').textContent=`本地素材 · ${state.previewAsset?.name||clip.source}`;
  $('#machineArt').innerHTML=machineSvg();
  $('#frameCaption').textContent=state.previewAsset?`替换预览 · ${state.previewAsset.name}`:`${clip.name} · 示意画面`;
  $('#assistantTarget').textContent=state.scene==='text'?`字幕句 ${state.textLine+1} · ${clip.name}`:state.scene==='sound'?`讲解段 · ${clip.name}`:state.scene==='device'?`箭头说明 · ${clip.name}`:`镜头 ${String(state.selected+1).padStart(2,'0')} · ${clip.name}`;
  $('#assistantTime').textContent=`${fmt(clip.start)}–${fmt(clip.end)} · 播放点 ${fmt(state.playhead)}`;
  if(!state.assistantCustom)$('#assistantReply').textContent=`已定位镜头 ${String(state.selected+1).padStart(2,'0')} · ${clip.name}。当前引用随镜头、文字和播放点更新；修改先预览，再确认。`;
  $('#scopeChip').textContent=`${state.scene==='text'?'字幕':state.scene==='sound'?'讲解':state.scene==='device'?'箭头':'镜头 '+String(state.selected+1).padStart(2,'0')} · ${fmt(state.playhead)}`;
  $('#playhead').value=String(state.playhead); $('#playTime').textContent=`${fmt(state.playhead)} / 00:48`;
  $('#undoButton').disabled=state.undoStack.length===0; $('#redoButton').disabled=state.redoStack.length===0;
  renderArrow(); renderShots(); renderTracks(); renderRail(); renderPanel(); renderProposal();
  requestAnimationFrame(placePanel);
}
function renderArrow() {
  const note=$('#arrowNote');
  const visible=state.scene==='device'&&state.playhead>=Number(state.arrowStart)&&state.playhead<=Number(state.arrowEnd);
  note.hidden=!visible;
  if(visible)$('#arrowLineSvg').removeAttribute('hidden');else $('#arrowLineSvg').setAttribute('hidden','');
  note.className=`arrow-note pos-${state.arrowPosition}`;
  const targetY=state.ratio==='wide'?35:state.ratio==='square'?44:47;
  const source=state.arrowPosition==='left-top'?(state.ratio==='portrait'?'50 15':'18 20'):state.arrowPosition==='left-bottom'?'18 81':state.arrowPosition==='right-bottom'?'70 81':state.ratio==='portrait'?'52 15':'70 20';
  $('#annotationPath').setAttribute('d',`M${source}L46 ${targetY}`);
  $('#arrowName').textContent=state.arrowName||'未命名部件';
  $('#arrowPurpose').textContent=state.arrowPurpose||'请填写简短作用';
}
function renderShots() {
  $('#shotStrip').innerHTML=clips.map((clip,index)=>`<button class="shot" type="button" data-shot="${index}" aria-pressed="${index===state.selected}"><span class="shot-thumb"><span class="shot-number">${String(index+1).padStart(2,'0')}</span>${escapeHtml(state.taskKind==='device'?(clip.focus==='feed'?'进料':clip.focus==='control'?'控制':clip.focus==='outlet'?'出料':'设备'):(clip.tag||'素材'))}</span><strong>${escapeHtml(clip.cut?'已标记剪除 · '+clip.name:clip.name)}</strong><small>${fmt(clip.start)}–${fmt(clip.end)}</small></button>`).join('');
}
function renderTracks() {
  $('#subtitleTrack').innerHTML=clips.map((clip,index)=>`<div class="track-block${clip.cut?' cut':''}" style="flex:${clip.end-clip.start}" title="${escapeHtml(clip.cut?'已标记剪除该句与对应源时段':clip.subtitle)}">${clip.cut?'剪除标记':index%2===0?escapeHtml(clip.subtitle):''}</div>`).join('');
  $('#voiceTrack').innerHTML=`<div class="track-block voice" style="flex:11">开场讲解</div><div class="track-block voice" style="flex:18">${clips.some(clip=>clip.voice)?'本机录音示意 · 操作讲解':'操作讲解 · 00:11–00:29'}</div><div class="track-block voice" style="flex:19">收尾讲解</div>`;
  $('#musicTrack').innerHTML=`<div class="track-block music" style="flex:48">背景音乐 · 00:00–00:48</div>`;
}
function renderRail() {
  const rail=$('#railContent');rail.className='rail-content';
  if(state.scene==='text'){
    $('#railTitle').textContent='文字定位';$('#railAction').textContent='定位当前句';
    rail.innerHTML=clips.slice(1,7).map((clip,index)=>`<button class="rail-text-row" type="button" data-transcript="${index+1}"><strong>${fmt(clip.start)} · ${escapeHtml(clip.subtitle)}</strong><small>点击定位；字幕与剪画面分开</small></button>`).join('');return;
  }
  if(state.scene==='sound'){
    $('#railTitle').textContent='声音轨';$('#railAction').textContent='看讲解';
    rail.innerHTML=`<div class="rail-text-row"><strong>讲解 · 3 段</strong><small>当前段 00:11–00:29</small></div><div class="rail-text-row"><strong>音乐 · 1 条</strong><small>贯穿 00:00–00:48</small></div><div class="rail-text-row"><strong>电脑录音</strong><small>仅演示操作流程，未连接麦克风</small></div>`;return;
  }
  if(state.scene==='device'){
    $('#railTitle').textContent='画面说明';$('#railAction').textContent='编辑箭头';
    rail.innerHTML=`<div class="rail-text-row"><strong>进料口 · 00:11–00:17</strong><small>静态箭头手动定位，不跟踪运动</small></div><div class="rail-text-row"><strong>画面短句</strong><small>进料口 / 原料从此进入</small></div>`;return;
  }
  $('#railTitle').textContent='本次素材';$('#railAction').textContent='查看全部';
  rail.innerHTML=`<input class="rail-search" id="railSearch" placeholder="搜索本次素材" aria-label="搜索本次素材">${candidates.map((asset,index)=>`<button class="rail-item" type="button" data-rail-asset="${index}"><span class="mini-thumb">本地</span><span><strong>${escapeHtml(asset.name)}</strong><small>${asset.duration} · ${escapeHtml(asset.note)}</small></span></button>`).join('')}`;
}
function panelTabs(active) {return `<div class="panel-tabs">${[['trim','裁剪'],['replace','换素材']].map(([id,label])=>`<button type="button" data-panel-mode="${id}" aria-pressed="${active===id}">${label}</button>`).join('')}</div>`;}
function renderPanel() {
  const panel=$('#floatPanel'),eligible=state.panelOpen;
  panel.hidden=!eligible;$('#panelTab').hidden=eligible||state.scene==='overview';
  $('#panelTitle').textContent=state.scene==='replace'?`镜头 ${String(state.selected+1).padStart(2,'0')} · 换素材`:state.scene==='text'?'文字剪辑':state.scene==='sound'?'声音与录音':state.scene==='device'?'设备箭头说明':`镜头 ${String(state.selected+1).padStart(2,'0')} · 精细修改`;
  $('#panelSubtitle').textContent=state.scene==='sound'?'录音设备未连接 · 流程演示':state.scene==='device'?'静态位置 · 不自动跟踪':'当前对象 · 草稿未应用';
  const body=$('#panelBody');
  if(state.scene==='replace'||(state.scene==='overview'&&state.panelMode==='replace')){
    body.innerHTML=`${state.scene==='overview'?panelTabs('replace'):''}<input class="panel-search" id="candidateSearch" placeholder="搜索本地候选" aria-label="搜索本地候选">${candidates.map((asset,index)=>`<button class="candidate" type="button" data-candidate="${index}" aria-pressed="${state.previewAsset===asset}"><span class="mini-thumb">本地</span><span><strong>${escapeHtml(asset.name)}</strong><small>${asset.duration} · ${escapeHtml(asset.note)}</small></span></button>`).join('')}`;
    $('#panelAction').textContent=state.previewAsset?'确认这次替换':'先选本地候选';$('#panelAction').disabled=!state.previewAsset;$('#panelStatus').textContent=state.previewAsset?'候选预览 · 未应用':'先选一段候选';return;
  }
  if(state.scene==='text'){
    body.innerHTML=`<div class="panel-chip-row"><button type="button" data-text-op="caption" aria-pressed="${state.textOp==='caption'}">只改字幕</button><button type="button" data-text-op="cut" aria-pressed="${state.textOp==='cut'}">标记剪除句子与时段</button></div><p class="panel-note">点一句定位播放点；剪除仅在源时间轴上标记，不改真实视频。</p>${state.textOp==='caption'?`<label>当前字幕<input id="captionDraft" type="text" maxlength="40" value="${escapeHtml(state.captionDraft)}"></label>`:''}${clips.slice(1,6).map((clip,index)=>`<button class="transcript-line" type="button" data-text-line="${index+1}" aria-pressed="${state.textLine===index+1}"><small>${fmt(clip.start)}–${fmt(clip.end)}</small><span>${escapeHtml(clip.subtitle)}</span></button>`).join('')}`;
    $('#panelAction').disabled=false;$('#panelAction').textContent='预览文字修改';$('#panelStatus').textContent='仅修改演示提案';return;
  }
  if(state.scene==='sound'){
    const labels={idle:'开始示意录音',recording:'停止示意录音',captured:'试听示意',auditioned:'重录'};
    body.innerHTML=`<p>当前讲解段：${fmt(selectedClip().start)}–${fmt(selectedClip().end)}</p><p class="panel-note">未读取麦克风；按钮只演示指定段落录音、试听、重录和替换顺序。</p><button class="panel-action" id="recordControl" type="button">${labels[state.recordPhase]}</button><button class="panel-action" id="recordReset" type="button" ${state.recordPhase==='idle'?'disabled':''}>重录这段</button><p id="recordStatus" class="panel-note">${state.recordPhase==='idle'?'尚无录音草稿':state.recordPhase==='recording'?'示意录音中，无真实音频':state.recordPhase==='captured'?'示意片段已记录，未试听':'示意试听已完成，可预览替换'}</p>`;
    $('#panelAction').disabled=false;$('#panelAction').textContent='预览替换讲解';$('#panelStatus').textContent='未连接麦克风';return;
  }
  if(state.scene==='device'){
    const tabs=`<div class="panel-tabs">${[['position','位置'],['time','时段'],['copy','文字']].map(([id,label])=>`<button type="button" data-arrow-mode="${id}" aria-pressed="${state.arrowMode===id}">${label}</button>`).join('')}</div>`;
    const content=state.arrowMode==='position'?`<div class="panel-chip-row">${[['left-top','左上'],['right-top','右上'],['right-bottom','右下'],['left-bottom','左下']].map(([id,label])=>`<button type="button" data-arrow-position="${id}" aria-pressed="${state.arrowPosition===id}">${label}</button>`).join('')}</div><p class="panel-note">箭头固定指向进料口，画面内可见。</p>`:state.arrowMode==='time'?`<div class="panel-chip-row"><label>出现<input id="arrowStart" type="number" min="0" max="48" step="0.1" value="${state.arrowStart}"></label><label>结束<input id="arrowEnd" type="number" min="0" max="48" step="0.1" value="${state.arrowEnd}"></label></div>`:`<label>画面名称<input id="arrowNameInput" type="text" maxlength="12" value="${escapeHtml(state.arrowName)}"></label><label>简短作用<input id="arrowPurposeInput" type="text" maxlength="32" value="${escapeHtml(state.arrowPurpose)}"></label>`;
    body.innerHTML=tabs+content;$('#panelAction').disabled=false;$('#panelAction').textContent='预览箭头说明';$('#panelStatus').textContent='画面短句 · 未应用';return;
  }
  body.innerHTML=`${panelTabs(state.panelMode)}<div class="panel-chip-row"><label>起点（秒）<input id="trimStart" type="number" min="0" max="48" step="0.1" value="${state.trimStart}"></label><label>终点（秒）<input id="trimEnd" type="number" min="0" max="48" step="0.1" value="${state.trimEnd}"></label></div>`;
  $('#panelAction').disabled=false;$('#panelAction').textContent='预览修改';$('#panelStatus').textContent='裁剪草稿 · 未应用';
}
function renderProposal() {
  const p=state.proposal;$('#proposal').hidden=!p||state.panelOpen;
  if(p)$('#proposalText').textContent=`${p.description} · 示意预览，尚未应用`;
}
function placePanel() {}
function showToast(message){const toast=$('#toast');toast.textContent=message;toast.hidden=false;clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>toast.hidden=true,2600);}
function selectShot(index){state.selected=index;state.playhead=clips[index].start;state.trimStart=String(clips[index].start.toFixed(1));state.trimEnd=String(clips[index].end.toFixed(1));if(state.scene==='text'){state.textLine=index;state.captionDraft=clips[index].subtitle;}state.assistantCustom=false;state.previewAsset=null;state.proposal=null;render();}
function makeProposal(type,description,payload={}){state.proposal={type,description,payload};renderProposal();}
function openConfirm(){const p=state.proposal;if(!p){showToast('先选择或填写一项修改');return;}const current=p.type==='replace'?selectedClip().source:p.type==='text'?selectedClip().subtitle:p.type==='sound'?'操作讲解 · 当前演示轨':p.type==='device'?'当前画面无箭头说明':`${fmt(selectedClip().start)}–${fmt(selectedClip().end)}`;const next=p.type==='text'?(p.payload.op==='cut'?'标记剪除该句及对应源时段':p.payload.value):p.payload.asset?.name||p.payload.label||'仅变更所选范围';$('#dialogTitle').textContent='确认这次示意修改';$('#dialogBody').innerHTML=`<p>${escapeHtml(p.description)}</p><div class="diff"><div><small>当前</small><p>${escapeHtml(current)}</p></div><div><small>预览后</small><p>${escapeHtml(next)}</p></div></div><small>这是界面提案流程示意，没有真实渲染或文件写入。</small>`;$('#dialogConfirm').hidden=false;$('#compareDialog').showModal();}
function snapshot(){return {clips:structuredClone(clips),arrow:{name:state.arrowName,purpose:state.arrowPurpose,position:state.arrowPosition,start:state.arrowStart,end:state.arrowEnd}};}
function restore(snap){clips.splice(0,clips.length,...structuredClone(snap.clips));Object.assign(state,{arrowName:snap.arrow.name,arrowPurpose:snap.arrow.purpose,arrowPosition:snap.arrow.position,arrowStart:snap.arrow.start,arrowEnd:snap.arrow.end});}
function applyProposal(){const p=state.proposal;if(!p)return;state.undoStack.push(snapshot());state.redoStack=[];const clip=selectedClip();if(p.type==='replace')clip.source=p.payload.asset.name;if(p.type==='text'){if(p.payload.op==='cut')clip.cut=true;else clip.subtitle=p.payload.value;}if(p.type==='sound')clip.voice='电脑录音示意';if(p.type==='trim')clip.trim=`${state.trimStart}–${state.trimEnd}`;state.proposal=null;state.previewAsset=null;$('#compareDialog').close();render();showToast('已应用到本页演示版本，可撤销');}
function togglePlayback(){state.playing=!state.playing;$('#playButton').textContent=state.playing?'暂停示意':'示意播放';if(state.playing){state.timer=setInterval(()=>{state.playhead=Math.min(48,state.playhead+.5);const index=clips.findIndex(c=>state.playhead>=c.start&&state.playhead<c.end);if(index>=0&&index!==state.selected){state.selected=index;render();}else{$('#playhead').value=String(state.playhead);$('#playTime').textContent=`${fmt(state.playhead)} / 00:48`;$('#assistantTime').textContent=`${fmt(selectedClip().start)}–${fmt(selectedClip().end)} · 播放点 ${fmt(state.playhead)}`;renderArrow();}if(state.playhead>=48)togglePlayback();},500);}else clearInterval(state.timer);}

document.addEventListener('click',event=>{
  const scene=event.target.closest('[data-scene]');if(scene){setScene(scene.dataset.scene);return;}
  const ratio=event.target.closest('[data-ratio]');if(ratio){state.ratio=ratio.dataset.ratio;render();return;}
  const shot=event.target.closest('[data-shot]');if(shot){selectShot(Number(shot.dataset.shot));return;}
  const candidate=event.target.closest('[data-candidate]');if(candidate){state.previewAsset=candidates[Number(candidate.dataset.candidate)];makeProposal('replace',`将镜头 ${state.selected+1} 换为 ${state.previewAsset.name}`,{asset:state.previewAsset});render();return;}
  const railAsset=event.target.closest('[data-rail-asset]');if(railAsset){setScene('replace');state.previewAsset=candidates[Number(railAsset.dataset.railAsset)];makeProposal('replace',`将镜头 ${state.selected+1} 换为 ${state.previewAsset.name}`,{asset:state.previewAsset});render();return;}
  const transcript=event.target.closest('[data-transcript],[data-text-line]');if(transcript){const index=Number(transcript.dataset.transcript??transcript.dataset.textLine);state.textLine=index;selectShot(index);state.scene='text';state.panelOpen=true;render();return;}
  const textOp=event.target.closest('[data-text-op]');if(textOp){state.textOp=textOp.dataset.textOp;renderPanel();return;}
  const panelMode=event.target.closest('[data-panel-mode]');if(panelMode){state.panelMode=panelMode.dataset.panelMode;renderPanel();return;}
  const arrowMode=event.target.closest('[data-arrow-mode]');if(arrowMode){state.arrowMode=arrowMode.dataset.arrowMode;renderPanel();return;}
  const arrowPos=event.target.closest('[data-arrow-position]');if(arrowPos){state.arrowPosition=arrowPos.dataset.arrowPosition;renderArrow();renderPanel();return;}
  if(event.target.id==='togglePanel'||event.target.id==='panelTab'){state.panelOpen=true;renderPanel();renderProposal();requestAnimationFrame(placePanel);return;}
  if(event.target.id==='closePanel'){state.panelOpen=false;renderPanel();renderProposal();$('#togglePanel').focus();return;}
  if(event.target.id==='scrollShots'){$('#shotStrip').scrollBy({left:350,behavior:'smooth'});return;}
  if(event.target.id==='playButton'){togglePlayback();return;}
  if(event.target.id==='sourceCompare'){$('#dialogTitle').textContent='按需查看原片';$('#dialogBody').innerHTML=`<p>原片对照从当前镜头打开，关闭后返回同一播放点。</p><div class="diff"><div><small>原片来源</small><p>${escapeHtml(selectedClip().source)}</p></div><div><small>当前剪辑</small><p>镜头 ${state.selected+1} · ${fmt(selectedClip().start)}–${fmt(selectedClip().end)}</p></div></div><small>本样本无真实视频，未启动第二个播放器。</small>`;$('#dialogConfirm').hidden=true;$('#compareDialog').showModal();return;}
  if(event.target.id==='railAction'){if(state.scene==='overview')setScene('replace');else if(state.scene==='text'){state.textLine=state.selected;renderPanel();}else state.panelOpen=true,renderPanel();return;}
  if(event.target.id==='recordControl'){state.recordPhase={idle:'recording',recording:'captured',captured:'auditioned',auditioned:'idle'}[state.recordPhase];renderPanel();return;}
  if(event.target.id==='recordReset'){state.recordPhase='idle';renderPanel();return;}
  if(event.target.id==='panelAction'){
    if(state.scene==='replace'){if(!state.previewAsset){showToast('先选一段本地候选');return;}openConfirm();return;}
    if(state.scene==='text'){makeProposal('text',state.textOp==='caption'?'只改当前字幕文字':'标记剪除这句话及对应源时段',{op:state.textOp,value:state.captionDraft});openConfirm();return;}
    if(state.scene==='sound'){if(state.recordPhase!=='auditioned'){showToast('先完成示意录音与试听');return;}makeProposal('sound','用本机录音示意替换当前讲解段',{label:'本机录音示意'});openConfirm();return;}
    if(state.scene==='device'){makeProposal('device','预览当前镜头的静态箭头说明',{label:`${state.arrowName} · ${state.arrowPurpose}`});openConfirm();return;}
    makeProposal('trim','仅预览当前镜头的局部参数',{label:`${state.trimStart}–${state.trimEnd} 秒`});openConfirm();return;
  }
  if(event.target.id==='confirmProposal'){openConfirm();return;}
  if(event.target.id==='cancelProposal'){state.proposal=null;state.previewAsset=null;render();return;}
  if(event.target.id==='dialogConfirm'){applyProposal();return;}
  if(event.target.id==='dialogCancel'||event.target.id==='dialogClose'){$('#compareDialog').close();return;}
  if(event.target.id==='undoButton'){const prev=state.undoStack.pop();if(prev){state.redoStack.push(snapshot());restore(prev);render();showToast('已撤销本页演示修改');}return;}
  if(event.target.id==='redoButton'){const next=state.redoStack.pop();if(next){state.undoStack.push(snapshot());restore(next);render();showToast('已重做本页演示修改');}return;}
  if(event.target.id==='clearScope'){$('#scopeChip').textContent='当前任务 · 无对象';return;}
  if(event.target.id==='sendPrompt'){state.assistantCustom=true;$('#assistantReply').textContent='已收到这条演示输入；没有调用模型，也没有改动时间线。请选择具体镜头后可继续预览本页交互。';$('#promptInput').value='';showToast('仅记录在本页演示中');return;}
});
document.addEventListener('input',event=>{
  if(event.target.id==='playhead'){state.playhead=Number(event.target.value);const index=clips.findIndex(c=>state.playhead>=c.start&&state.playhead<c.end);if(index>=0&&index!==state.selected){state.selected=index;render();}else{$('#playTime').textContent=`${fmt(state.playhead)} / 00:48`;$('#assistantTime').textContent=`${fmt(selectedClip().start)}–${fmt(selectedClip().end)} · 播放点 ${fmt(state.playhead)}`;renderArrow();}return;}
  if(event.target.id==='candidateSearch'){$$('.candidate').forEach(row=>row.hidden=!row.textContent.includes(event.target.value.trim()));return;}
  if(event.target.id==='railSearch'){$$('.rail-item').forEach(row=>row.hidden=!row.textContent.includes(event.target.value.trim()));return;}
  if(event.target.id==='captionDraft'){state.captionDraft=event.target.value;return;}
  const fields={arrowStart:'arrowStart',arrowEnd:'arrowEnd',arrowNameInput:'arrowName',arrowPurposeInput:'arrowPurpose',trimStart:'trimStart',trimEnd:'trimEnd'};if(fields[event.target.id]){state[fields[event.target.id]]=event.target.value;renderArrow();return;}
});
window.addEventListener('resize',()=>requestAnimationFrame(placePanel));
window.renderEditor=render;
const initialEditor={clips:structuredClone(clips),candidates:structuredClone(candidates),state:structuredClone({...state,timer:null,playing:false})};
window.editorTaskController={
  template:()=>structuredClone(initialEditor),
  save:()=>{
    clearInterval(state.timer);state.timer=null;state.playing=false;$('#playButton').textContent='示意播放';
    return {clips:structuredClone(clips),candidates:structuredClone(candidates),state:structuredClone({...state,timer:null,playing:false,panelOpen:false,previewAsset:null,proposal:null})};
  },
  load:data=>{
    clearInterval(state.timer);
    clips.splice(0,clips.length,...structuredClone(data.clips));
    candidates.splice(0,candidates.length,...structuredClone(data.candidates));
    Object.assign(state,structuredClone(data.state),{timer:null,playing:false,panelOpen:false,previewAsset:null,proposal:null});
    $('#playButton').textContent='示意播放';
    render();
  }
};
render();
