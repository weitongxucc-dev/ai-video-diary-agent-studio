const phaseView=document.querySelector('#stageView');
const phaseData={
  active:'edit',
  assets:{selected:0,tagDraft:'',files:[
    {name:'整机_01.mov',duration:'00:06',status:'解析完成',tags:['整机','开场']},
    {name:'进料口_03.mov',duration:'00:06',status:'解析完成',tags:['进料口','近景']},
    {name:'按钮_04.mov',duration:'00:06',status:'待确认标签',tags:['操作']}
  ]},
  ideas:{selected:0,draft:'',saved:false,items:[
    {title:'设备使用的三个关键步骤',reason:'先看结构，再跟操作，最后检查状态'},
    {title:'一分钟看懂进料与启动',reason:'适合新手快速判断入口与按钮'},
    {title:'从进料到清洁的完整演示',reason:'覆盖本次八个本地镜头'}
  ]},
  script:{selected:0,draft:'',saved:false,parts:[
    '这台设备怎么使用？先看清进料口与控制区。',
    '从进料口缓慢倒入原料，确认护盖关闭后启动。',
    '运行时留意指示灯，停机后再清洁接触部件。'
  ]},
  voice:{selected:'温和讲解',auditioned:false,applied:''},
  caption:{style:'白底深字',cover:'设备使用演示',draft:'设备使用演示',applied:false},
  review:{issues:[
    {title:'镜头 03 的字幕需复核',done:false},
    {title:'讲解与音乐的音量需确认',done:false},
    {title:'封面文字需最终确认',done:false}
  ],output:'未开始'},
  publish:{account:'视频号 · 演示账号',copy:'设备使用步骤：进料、启动、观察与清洁。',checked:false,status:'未发布'}
};
phaseData.ideas.draft=phaseData.ideas.items[0].title;
phaseData.script.draft=phaseData.script.parts[0];
const phaseNames={assets:'资料',ideas:'选题',script:'文案',voice:'配音',edit:'视频剪辑',caption:'字幕与封面',review:'检查与输出',publish:'发布'};
const phaseEsc=value=>String(value).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function phaseButton(label,action,value,selected=false,primary=false){
  return '<button type="button" data-phase-action="'+action+'" data-value="'+phaseEsc(value)+'"'+
    (selected?' aria-pressed="true"':'')+(primary?' class="phase-primary"':'')+'>'+phaseEsc(label)+'</button>';
}
function phasePage(title,description,status,left,right){
  return '<div class="phase-head"><div><h1>'+title+'</h1><p>'+description+'</p></div><span class="phase-status">'+status+'</span></div>'+
    '<div class="phase-grid"><section class="phase-card">'+left+'</section><section class="phase-card">'+right+'</section></div>';
}
function renderPhase(){
  document.querySelectorAll('[data-stage]').forEach(button=>{
    const current=button.dataset.stage===phaseData.active;
    button.classList.toggle('active',current);
    if(current)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');
  });
  const editing=phaseData.active==='edit';
  document.querySelector('#editor').hidden=!editing;
  phaseView.hidden=editing;
  document.querySelector('#workspace').classList.toggle('phase-not-edit',!editing);
  if(editing){window.renderEditor();return;}
  const name=phaseNames[phaseData.active];
  document.querySelector('#assistantTarget').textContent=(window.activeTaskTitle||'设备使用演示')+' · '+name;
  document.querySelector('#assistantTime').textContent='当前阶段 · 页面内演示数据';
  document.querySelector('#scopeChip').textContent=name+' · 演示';
  document.querySelector('#assistantReply').textContent='已切换到'+name+'。本阶段的选择、编辑与确认会保留到本页刷新前，不连接真实任务。';
  let markup='';
  if(phaseData.active==='assets'){
    const d=phaseData.assets,file=d.files[d.selected];
    const rows=d.files.map((item,index)=>'<div class="phase-row"><div><strong>'+phaseEsc(item.name)+'</strong><small>'+item.duration+' · '+item.status+'</small></div>'+
      phaseButton(index===d.selected?'已选中':'查看','select-asset',index,index===d.selected)+'</div>').join('');
    const tags=file.tags.map((tag,index)=>phaseButton(tag+' ×','remove-tag',index)).join('');
    markup=phasePage('资料','当前任务的本地视频与解析标签。',d.files.length+' 段本地素材',
      '<h2>本次本地视频</h2>'+rows,
      '<h2>'+phaseEsc(file.name)+'</h2><p>解析状态：'+file.status+'</p><div class="phase-choice-row">'+tags+'</div>'+
      '<label class="phase-label" for="assetTagDraft">补充标签</label><input id="assetTagDraft" placeholder="例如：侧面操作" value="'+phaseEsc(d.tagDraft)+'">'+
      '<div class="phase-actions">'+phaseButton('加入标签','add-tag','')+phaseButton('确认解析状态','parse-asset','')+'</div>'+
      '<div class="phase-note">标签和解析结果只保存在本页，不读取真实文件。</div>');
  }
  if(phaseData.active==='ideas'){
    const d=phaseData.ideas,item=d.items[d.selected];
    markup=phasePage('选题','从本次本地镜头出发选择一个视频主线。',d.saved?'选题已确认':'待确认',
      '<h2>三个选题建议</h2>'+d.items.map((idea,index)=>'<div class="phase-row"><div><strong>'+phaseEsc(idea.title)+'</strong><small>'+phaseEsc(idea.reason)+'</small></div>'+
        phaseButton(index===d.selected?'当前':'选择','select-idea',index,index===d.selected)+'</div>').join(''),
      '<h2>当前选题</h2><p>'+phaseEsc(item.reason)+'</p><label class="phase-label" for="ideaDraft">编辑标题</label>'+
      '<input id="ideaDraft" value="'+phaseEsc(d.draft)+'">'+
      '<div class="phase-actions">'+phaseButton('确认这个选题','save-idea','',false,true)+'</div>'+
      '<div class="phase-preview"><small>预览标题</small><p>'+phaseEsc(item.title)+'</p></div>');
  }
  if(phaseData.active==='script'){
    const d=phaseData.script;
    markup=phasePage('文案','按开场、主体、收尾三段编辑本次讲解。',d.saved?'文案已确认':'草稿',
      '<h2>讲解段落</h2>'+d.parts.map((part,index)=>'<div class="phase-row"><div><strong>'+(index+1)+'. '+phaseEsc(['开场','操作','收尾'][index])+'</strong><small>'+phaseEsc(part)+'</small></div>'+
        phaseButton(index===d.selected?'编辑中':'编辑','select-paragraph',index,index===d.selected)+'</div>').join(''),
      '<h2>编辑第 '+(d.selected+1)+' 段</h2><textarea id="scriptDraft">'+phaseEsc(d.draft)+'</textarea>'+
      '<div class="phase-actions">'+phaseButton('确认这段文案','save-paragraph','',false,true)+'</div>'+
      '<div class="phase-note">确认后左侧段落立即更新；这里只保存文字示意。</div>');
  }
  if(phaseData.active==='voice'){
    const d=phaseData.voice;
    markup=phasePage('配音','为当前任务的讲解选择声音并试听流程。',d.applied?'已选 '+phaseEsc(d.applied):'未确认声音',
      '<h2>声音选择</h2><p>讲解范围：00:00–00:48 · 三段文案</p><div class="phase-choice-row">'+
      ['温和讲解','清晰说明','沉稳演示'].map(v=>phaseButton(v,'choose-voice',v,d.selected===v)).join('')+'</div>'+
      '<div class="phase-actions">'+phaseButton(d.auditioned?'再次试听示意':'试听示意','audition-voice','')+
      phaseButton('确认这款声音','apply-voice','',false,true)+'</div>',
      '<h2>当前声音</h2><div class="phase-preview"><strong>'+phaseEsc(d.selected)+'</strong><p>“'+phaseEsc(phaseData.script.parts[1])+'”</p></div>'+
      '<div class="phase-note">'+(d.auditioned?'已完成试听步骤；无实际音频。':'试听按钮只更新本页状态，不播放真实音频。')+'</div>');
  }
  if(phaseData.active==='caption'){
    const d=phaseData.caption;
    markup=phasePage('字幕与封面','选择字幕样式，编辑当前视频的封面文字。',d.applied?'样式已应用':'待应用',
      '<h2>字幕样式</h2><div class="phase-choice-row">'+['白底深字','深底白字','纯文字'].map(v=>phaseButton(v,'choose-style',v,d.style===v)).join('')+'</div>'+
      '<label class="phase-label" for="coverDraft">封面文字</label><input id="coverDraft" maxlength="24" value="'+phaseEsc(d.draft)+'">'+
      '<div class="phase-actions">'+phaseButton('应用字幕与封面','apply-caption','',false,true)+'</div>',
      '<h2>封面预览</h2><div class="phase-cover"><strong>'+phaseEsc(d.cover)+'</strong></div>'+
      '<div class="phase-note">字幕样式：'+phaseEsc(d.style)+' · 预览为灰阶示意。</div>');
  }
  if(phaseData.active==='review'){
    const d=phaseData.review,done=d.issues.filter(item=>item.done).length;
    markup=phasePage('检查与输出','逐项确认后演示输出状态，不生成文件。',done+'/3 项已确认',
      '<h2>检查清单</h2>'+d.issues.map((item,index)=>'<div class="phase-row"><div><strong>'+phaseEsc(item.title)+'</strong><small>'+(item.done?'已确认':'待确认')+'</small></div>'+
        phaseButton(item.done?'撤回确认':'确认','toggle-issue',index,item.done)+'</div>').join(''),
      '<h2>输出状态</h2><div class="phase-output">'+phaseEsc(d.output)+'</div>'+
      '<div class="phase-actions">'+phaseButton('确认检查并模拟输出','simulate-output','',false,true)+'</div>'+
      '<div class="phase-note">没有视频编码、渲染、下载或文件写入。</div>');
  }
  if(phaseData.active==='publish'){
    const d=phaseData.publish;
    markup=phasePage('发布','确认目标账号和发布文案；最后一步仅模拟提交。',phaseEsc(d.status),
      '<h2>目标平台与账号</h2><div class="phase-choice-row">'+
      ['视频号 · 演示账号','小红书 · 演示账号','抖音 · 演示账号'].map(v=>phaseButton(v,'select-account',v,d.account===v)).join('')+'</div>'+
      '<label class="phase-label" for="publishCopy">发布文案</label><textarea id="publishCopy">'+phaseEsc(d.copy)+'</textarea>',
      '<h2>发布前确认</h2><p>账号：'+phaseEsc(d.account)+'</p><div class="phase-preview">'+phaseEsc(d.copy)+'</div>'+
      '<div class="phase-actions">'+phaseButton(d.checked?'已确认资料':'确认账号与文案','precheck-publish','',d.checked)+
      phaseButton('模拟发布','simulate-publish','',false,true)+'</div>'+
      '<div class="phase-note">不会登录平台，也不会向任何账号提交内容。</div>');
  }
  phaseView.innerHTML=markup;
  if(window.currentTaskSource&&['ideas','script','caption'].includes(phaseData.active)){
    phaseView.insertAdjacentHTML('beforeend','<div class="phase-note">本次引用来源：'+
      phaseEsc(window.currentTaskSource)+'。创作仍须核对原文与授权。</div>');
  }
}
document.addEventListener('click',event=>{
  const stage=event.target.closest('[data-stage]');
  if(stage){phaseData.active=stage.dataset.stage;renderPhase();return;}
  const action=event.target.closest('[data-phase-action]');
  if(!action)return;
  const kind=action.dataset.phaseAction,value=action.dataset.value;
  const d=phaseData;
  if(kind==='select-asset')d.assets.selected=Number(value);
  if(kind==='add-tag'){const tag=d.assets.tagDraft.trim();if(tag&&!d.assets.files[d.assets.selected].tags.includes(tag))d.assets.files[d.assets.selected].tags.push(tag);d.assets.tagDraft='';}
  if(kind==='remove-tag')d.assets.files[d.assets.selected].tags.splice(Number(value),1);
  if(kind==='parse-asset')d.assets.files[d.assets.selected].status='解析状态已确认';
  if(kind==='select-idea'){d.ideas.selected=Number(value);d.ideas.draft=d.ideas.items[d.ideas.selected].title;d.ideas.saved=false;}
  if(kind==='save-idea'){d.ideas.items[d.ideas.selected].title=d.ideas.draft.trim()||d.ideas.items[d.ideas.selected].title;d.ideas.saved=true;}
  if(kind==='select-paragraph'){d.script.selected=Number(value);d.script.draft=d.script.parts[d.script.selected];d.script.saved=false;}
  if(kind==='save-paragraph'){d.script.parts[d.script.selected]=d.script.draft.trim()||d.script.parts[d.script.selected];d.script.saved=true;}
  if(kind==='choose-voice'){d.voice.selected=value;d.voice.auditioned=false;}
  if(kind==='audition-voice')d.voice.auditioned=true;
  if(kind==='apply-voice'){if(d.voice.auditioned)d.voice.applied=d.voice.selected;}
  if(kind==='choose-style')d.caption.style=value;
  if(kind==='apply-caption'){d.caption.cover=d.caption.draft.trim()||d.caption.cover;d.caption.applied=true;}
  if(kind==='toggle-issue'){const issue=d.review.issues[Number(value)];issue.done=!issue.done;d.review.output='未开始';}
  if(kind==='simulate-output'){const pending=d.review.issues.filter(item=>!item.done).length;d.review.output=pending?'还有 '+pending+' 项待确认；未模拟输出':'模拟输出完成 · 无真实文件';}
  if(kind==='select-account'){d.publish.account=value;d.publish.checked=false;d.publish.status='未发布';}
  if(kind==='precheck-publish'){d.publish.checked=true;d.publish.status='发布前资料已确认';}
  if(kind==='simulate-publish')d.publish.status=d.publish.checked?'模拟发布完成 · 未提交平台':'先确认账号与文案';
  renderPhase();
});
document.addEventListener('input',event=>{
  const id=event.target.id,value=event.target.value,d=phaseData;
  if(id==='assetTagDraft')d.assets.tagDraft=value;
  if(id==='ideaDraft')d.ideas.draft=value;
  if(id==='scriptDraft')d.script.draft=value;
  if(id==='coverDraft'){d.caption.draft=value;d.caption.applied=false;}
  if(id==='publishCopy'){d.publish.copy=value;d.publish.checked=false;d.publish.status='未发布';}
});
const phaseTemplate=structuredClone(phaseData);
window.stageController={
  save:()=>structuredClone(phaseData),
  load:data=>{
    for(const key of Object.keys(phaseData))delete phaseData[key];
    Object.assign(phaseData,structuredClone(data));
    renderPhase();
  },
  template:()=>structuredClone(phaseTemplate),
  show:renderPhase
};
renderPhase();
