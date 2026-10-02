const role=document.body.dataset.role;
const isOwner=role==='owner';
const currentUser=isOwner?'林总':'小陈';
const W=selector=>document.querySelector(selector);
const we=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const wb=(label,action,value='',selected=false,primary=false)=>
  '<button type="button" data-work-action="'+action+'" data-value="'+we(value)+'"'+
  (selected?' aria-pressed="true"':'')+(primary?' class="phase-primary"':'')+'>'+we(label)+'</button>';
const card=(title,content)=>'<section class="phase-card"><h2>'+title+'</h2>'+content+'</section>';
const panel=(title,description,status,left,right)=>
  '<div class="phase-head"><div><h1>'+title+'</h1><p>'+description+'</p></div><span class="phase-status">'+status+'</span></div>'+
  '<div class="phase-grid">'+left+right+'</div>';
function makeEditor(kind,names,sources,subtitles) {
  const data=window.editorTaskController.template();
  data.clips.forEach((clip,index)=>{
    clip.name=names[index];clip.source=sources[index];clip.subtitle=subtitles[index];clip.focus='whole';
    clip.tag=kind==='store'?['外景','准备','接待','陈列','工作','互动','收尾','门店'][index]:'素材';
  });
  data.candidates=[
    {name:sources[2].replace('.mov','_备用A.mov'),duration:'00:08',note:'同主题补充角度',focus:'whole'},
    {name:sources[3].replace('.mov','_备用B.mov'),duration:'00:07',note:'动作更完整',focus:'whole'},
    {name:sources[4].replace('.mov','_备用C.mov'),duration:'00:09',note:'本地候选片段',focus:'whole'}
  ];
  Object.assign(data.state,{taskKind:kind,selected:2,playhead:13,scene:'overview',panelOpen:false,
    previewAsset:null,proposal:null,undoStack:[],redoStack:[],assistantCustom:false,arrowName:'画面重点',arrowPurpose:'静态说明示意'});
  return data;
}
function makeStage(title,files,ideas,script,active='edit') {
  const data=window.stageController.template();
  data.active=active;
  data.assets.files=files.map((name,index)=>({name,duration:'00:0'+(6+index),status:index===2?'待确认标签':'解析完成',tags:['本地',index===0?'开场':'镜头']}));
  data.ideas.items=ideas.map((name,index)=>({title:name,reason:['围绕已有素材','突出真实场景','按时间顺序讲清楚'][index]}));
  data.ideas.draft=ideas[0];data.script.parts=script.slice();data.script.draft=script[0];
  data.caption.cover=title;data.caption.draft=title;data.publish.copy=title+'：本地素材混剪示意。';
  return data;
}
const taskData={
  device:{id:'device',title:'设备使用演示',owner:'林总',space:'主空间',status:'视频剪辑中',kind:'device',goal:'进料、启动、观察与清洁步骤',
    editor:window.editorTaskController.save(),stage:window.stageController.save(),source:''},
  store:{id:'store',title:'门店日常记录',owner:'小陈',space:'门店内容',status:'等待文案确认',kind:'store',goal:'记录真实的门店服务与陈列',
    editor:makeEditor('store',
      ['门店外景','开门准备','接待顾客','商品陈列','工作片段','顾客互动','打包收尾','门店结尾'],
      ['门头_01.mov','开门_02.mov','接待_03.mov','陈列_04.mov','工作_05.mov','互动_06.mov','打包_07.mov','夜景_08.mov'],
      ['从门店开始新的一天。','先整理好店内环境。','顾客进店后先了解需求。','展示今天的商品陈列。','记录实际服务过程。','交流细节比口号更重要。','把商品仔细打包。','一天的工作到这里结束。']),
    stage:makeStage('门店日常记录',['门头_01.mov','接待_03.mov','陈列_04.mov'],
      ['门店一天从哪里开始','一次真实接待的过程','陈列与打包记录'],
      ['开门前先整理店内环境。','顾客进店后，先听需求再介绍合适的商品。','完成打包后记录一天的收尾。'],'script'),source:''},
  food:{id:'food',title:'菜品出餐记录',owner:'小李',space:'餐饮内容',status:'素材分析排队',kind:'custom',goal:'从备菜到出餐的现场记录',
    editor:makeEditor('custom',
      ['食材开场','备菜过程','下锅操作','火候特写','摆盘准备','出餐近景','顾客桌面','收尾画面'],
      ['食材_01.mov','备菜_02.mov','下锅_03.mov','火候_04.mov','摆盘_05.mov','出餐_06.mov','桌面_07.mov','收尾_08.mov'],
      ['今天记录一道菜的出餐过程。','先把食材准备好。','接着开始烹饪。','火候决定口感。','出餐前认真摆盘。','热菜现在可以上桌。','看看完整的用餐场景。','这道菜就完成了。']),
    stage:makeStage('菜品出餐记录',['食材_01.mov','下锅_03.mov','出餐_06.mov'],
      ['一道菜从备菜到出餐','厨房里的关键步骤','这道菜怎样呈现'],
      ['先看食材准备。','烹饪时留意火候与过程。','最后记录摆盘和上桌。'],'assets'),source:''}
};
const model={
  page:'home',currentTask:isOwner?'device':'store',host:'online',queue:[
    {id:'q1',kind:'分析',title:'门店素材批次 03',owner:'小陈',stage:'等待资源',reason:'重制作占用资源，分析暂缓',eta:'资源空闲后约 15–25 分钟',private:true},
    {id:'q2',kind:'制作',title:'设备使用演示',owner:'林总',stage:'等待人工确认',reason:'需要确认替换镜头',eta:'需要人工确认',private:false},
    {id:'q3',kind:'分析',title:'菜品出餐素材',owner:'小李',stage:'排队',reason:'尚未轮到该批次',eta:'约 30–50 分钟',private:true},
    {id:'q4',kind:'制作',title:'新品预览草稿',owner:'小王',stage:'制作中',reason:'正在合成预览草稿',eta:'约 20–35 分钟',private:true}
  ],
  spaces:[
    {id:'main',name:'主空间',member:'林总',status:'使用中'},
    {id:'chen',name:'门店内容',member:'小陈',status:'使用中'},
    {id:'li',name:'餐饮内容',member:'小李',status:'使用中'},
    {id:'wang',name:'新品项目',member:'小王',status:'使用中'}
  ],
  spaceDraft:{name:'',member:''},invite:null,
  assets:{
    scope:'可见全部',type:'全部类型',analysis:'全部状态',rating:'全部评价',tag:'',selected:'a1',detail:false,bulk:[],uploadMode:'',
    upload:{sample:'设备进料侧面_新.mov',link:'',owner:currentUser,visibility:'私有',background:'无',product:'未关联',authorization:'待确认'},
    watermark:{phase:'none',authorized:false,sourceId:''},
    items:[
      {id:'a1',name:'整机开场_01.mov',type:'设备',owner:'林总',visibility:'团队共享',analysis:'可用',rating:'高',avoid:false,time:'00:00–00:06',description:'设备外观开场，适合介绍整体结构。',auto:['设备','开场'],manual:['整机'],source:'本地上传',authorization:'本人确认可用'},
      {id:'a2',name:'进料口_03.mov',type:'设备',owner:'林总',visibility:'团队共享',analysis:'可用',rating:'高',avoid:false,time:'00:11–00:17',description:'进料口近景，手部动作清楚。',auto:['设备','进料'],manual:['关键镜头'],source:'本地上传',authorization:'本人确认可用'},
      {id:'a3',name:'按钮_04.mov',type:'设备',owner:'林总',visibility:'私有',analysis:'可用',rating:'中',avoid:false,time:'00:17–00:23',description:'启动按钮与护盖同框。',auto:['设备','操作'],manual:[],source:'本地上传',authorization:'本人确认可用'},
      {id:'a4',name:'出料_06.mov',type:'设备',owner:'林总',visibility:'团队共享',analysis:'分析中',rating:'未评价',avoid:false,time:'00:29–00:35',description:'出料过程，正在提取片段信息。',auto:['设备'],manual:[],source:'本地上传',authorization:'本人确认可用'},
      {id:'a5',name:'餐厅门头_01.mov',type:'餐饮',owner:'小李',visibility:'团队共享',analysis:'可用',rating:'中',avoid:false,time:'00:00–00:06',description:'餐厅门头与招牌。',auto:['餐饮','外景'],manual:[],source:'本地上传',authorization:'成员确认可用'},
      {id:'a6',name:'下锅操作_03.mov',type:'餐饮',owner:'小李',visibility:'私有',analysis:'可用',rating:'高',avoid:false,time:'00:12–00:18',description:'锅内操作细节。',auto:['餐饮','厨房'],manual:[],source:'本地上传',authorization:'成员确认可用'},
      {id:'a7',name:'摆盘_05.mov',type:'餐饮',owner:'小李',visibility:'团队共享',analysis:'待处理',rating:'未评价',avoid:false,time:'00:24–00:31',description:'摆盘待解析。',auto:['餐饮'],manual:[],source:'本地上传',authorization:'成员确认可用'},
      {id:'a8',name:'餐桌近景_07.mov',type:'餐饮',owner:'小李',visibility:'私有',analysis:'可用',rating:'低',avoid:true,time:'00:34–00:41',description:'光线不足，暂不用于自动创作。',auto:['餐饮','近景'],manual:[],source:'本地上传',authorization:'成员确认可用'},
      {id:'a9',name:'门头晨景_01.mov',type:'门店',owner:'小陈',visibility:'私有',analysis:'可用',rating:'高',avoid:false,time:'00:00–00:06',description:'早晨开门前的门头。',auto:['门店','外景'],manual:['开场'],source:'本地上传',authorization:'本人确认可用'},
      {id:'a10',name:'顾客接待_03.mov',type:'门店',owner:'小陈',visibility:'私有',analysis:'可用',rating:'高',avoid:false,time:'00:11–00:17',description:'顾客到店的服务场景。',auto:['门店','人物'],manual:[],source:'本地上传',authorization:'本人确认可用'},
      {id:'a11',name:'陈列侧面_04.mov',type:'门店',owner:'小陈',visibility:'团队共享',analysis:'可用',rating:'中',avoid:false,time:'00:17–00:23',description:'商品陈列的侧面角度。',auto:['门店','陈列'],manual:[],source:'本地上传',authorization:'本人确认可用'},
      {id:'a12',name:'打包收尾_07.mov',type:'门店',owner:'小陈',visibility:'私有',analysis:'待处理',rating:'未评价',avoid:false,time:'00:35–00:41',description:'已接收，尚未完成分析。',auto:['门店'],manual:[],source:'本地上传',authorization:'本人确认可用'},
      {id:'a13',name:'护盖关闭_05.mov',type:'设备',owner:'林总',visibility:'私有',analysis:'可用',rating:'中',avoid:false,time:'00:23–00:29',description:'护盖闭合后准备启动。',auto:['设备','安全'],manual:[],source:'本地上传',authorization:'本人确认可用'},
      {id:'a14',name:'出料近景_08.mov',type:'设备',owner:'林总',visibility:'团队共享',analysis:'可用',rating:'高',avoid:false,time:'00:36–00:42',description:'出料过程补充角度。',auto:['设备','出料'],manual:[],source:'本地上传',authorization:'本人确认可用'},
      {id:'a15',name:'门店陈列_05.mov',type:'门店',owner:'小陈',visibility:'团队共享',analysis:'可用',rating:'中',avoid:false,time:'00:23–00:29',description:'陈列区补充镜头。',auto:['门店','陈列'],manual:[],source:'本地上传',authorization:'成员授权共享'},
      {id:'a16',name:'出餐窗口_08.mov',type:'餐饮',owner:'小李',visibility:'团队共享',analysis:'分析中',rating:'未评价',avoid:false,time:'00:40–00:46',description:'窗口出餐过程。',auto:['餐饮','出餐'],manual:[],source:'本地上传',authorization:'成员授权共享'},
      {id:'a17',name:'控制区侧面_09.mov',type:'设备',owner:'林总',visibility:'团队共享',analysis:'可用',rating:'中',avoid:false,time:'00:42–00:48',description:'控制区侧面补充。',auto:['设备','控制'],manual:[],source:'本地上传',authorization:'本人确认可用'}
    ]
  },
  knowledge:{selected:'k1',category:'全部',query:'',topicOpen:true,qa:false,detailOpen:false,editor:'',problem:'',uploadMode:'',draftTitle:'',draftLink:'',draftType:'文档',draftProduct:'设备A',
    items:[
      {id:'k1',title:'设备A使用说明.pdf',kind:'PDF',owner:'林总',visibility:'团队共享',source:'本地文档',time:'2026-09-20',version:'v2',product:'设备A',original:'进料口位于设备上方。开机前请确认护盖闭合。参数页写额定功率 800W。',summary:'操作前先确认护盖，之后从上方缓慢进料。',params:'额定功率：800W（说明书）',issue:'另一份资料写 1000W，参数冲突待人工确认。'},
      {id:'k2',title:'设备A参数卡.png',kind:'图片文字',owner:'林总',visibility:'团队共享',source:'本地图片',time:'2026-09-21',version:'v1',product:'设备A',original:'型号 A-2；额定功率 1000W；操作面板位于右侧。',summary:'图片文字已提取，功率与说明书冲突。',params:'额定功率：1000W（参数卡）',issue:'与使用说明书冲突，待确认。'},
      {id:'k3',title:'门店服务介绍.docx',kind:'文档',owner:'小陈',visibility:'私有',source:'本地文档',time:'2026-09-22',version:'v1',product:'门店服务',original:'先了解顾客需求，再介绍合适商品；记录真实服务过程。',summary:'门店服务流程概要。',params:'服务步骤：倾听 → 介绍 → 确认',issue:'无'},
      {id:'k4',title:'菜品介绍来源',kind:'链接',owner:'小李',visibility:'团队共享',source:'https://example.test/menu',time:'2026-09-22',version:'采集版1',product:'菜品B',original:'菜品原料和出餐说明，来源为演示链接。',summary:'待核对原料名称与过敏提示。',params:'主要原料：待核对',issue:'来源未授权转载，不能直接用于发布文案。'}
    ]},
  people:{tab:'人物',selected:'p1',auditioned:false,message:'',items:[
    {id:'p1',name:'林总',kind:'人物',owner:'林总',visibility:'团队共享',status:'参考素材可用',auth:'本人确认展示',licensed:true,linked:'温和讲解',material:'照片 2 张 · 视频参考 1 段'},
    {id:'p2',name:'小陈',kind:'人物',owner:'小陈',visibility:'私有',status:'参考素材可用',auth:'本人确认展示',licensed:true,linked:'门店讲解',material:'照片 1 张 · 视频参考 2 段'},
    {id:'p3',name:'餐饮同事',kind:'人物',owner:'小李',visibility:'私有',status:'待确认',auth:'未确认展示授权',licensed:false,linked:'无',material:'视频参考 1 段'},
    {id:'v1',name:'温和讲解',kind:'声音',owner:'林总',visibility:'团队共享',status:'可用',auth:'仅限本工作空间讲解',licensed:true,linked:'林总',material:'试听示意'},
    {id:'v2',name:'门店讲解',kind:'声音',owner:'小陈',visibility:'私有',status:'可用',auth:'仅限小陈任务',licensed:true,linked:'小陈',material:'试听示意'},
    {id:'v3',name:'新品声音',kind:'声音',owner:'小王',visibility:'团队共享',status:'克隆处理中',auth:'待确认声音授权范围',licensed:false,linked:'小王',material:'尚无可试听版本'}
  ]},
  templates:{selected:'t1',detailOpen:false,applied:'',items:[
    {id:'t1',name:'设备讲解',structure:'整体 → 部件 → 操作 → 注意事项',needs:'整机、部件近景、操作镜头',gap:'出料镜头仍在分析中'},
    {id:'t2',name:'菜品出餐',structure:'食材 → 烹饪 → 摆盘 → 上桌',needs:'食材、锅内、摆盘、上桌',gap:'摆盘镜头待解析'},
    {id:'t3',name:'门店记录',structure:'环境 → 接待 → 服务 → 收尾',needs:'门头、人物、服务、打包',gap:'打包收尾待解析'},
    {id:'t4',name:'新品介绍',structure:'外观 → 细节 → 使用场景 → 总结',needs:'包装、细节、使用场景',gap:'使用场景素材不足'}
  ]},
  scheduled:{selected:'s1',draftName:'',draftTime:'22:00',draftMode:'闲时',draftSpace:isOwner?'主空间':'门店内容',message:'',jobs:[
    {id:'s1',name:'闲时整理本地素材',owner:'林总',space:'主空间',mode:'闲时',time:'22:00',enabled:true,last:'昨天 22:10 · 素材不足，已跳过',next:'主机空闲后尝试'},
    {id:'s2',name:'夜间制作门店草稿',owner:'小陈',space:'门店内容',mode:'到点',time:'23:30',enabled:false,last:'尚未运行',next:'暂停中'}
  ]},
  publishing:{range:'7天',member:'全部成员',platform:'全部平台',selected:'r1',message:'',items:[
    {id:'r1',title:'设备使用演示',owner:'林总',platform:'视频号',status:'已导出 · 未标记发布',date:'2026-09-24',views:0},
    {id:'r2',title:'门店开门记录',owner:'小陈',platform:'小红书',status:'人工标记已发布 · 平台未确认',date:'2026-09-23',views:0},
    {id:'r3',title:'菜品出餐片段',owner:'小李',platform:'抖音',status:'平台确认 · 数据未同步',date:'2026-09-22',views:0},
    {id:'r4',title:'秋季新品预告',owner:'小王',platform:'视频号',status:'平台确认 · 示例数据',date:'2026-09-21',views:136}
  ]},
  newTask:{title:'',goal:'设备讲解',space:isOwner?'主空间':'门店内容',error:''}
};
model.knowledge.editor=model.knowledge.items[0].summary;
const knowledgeCategories={'k1':'产品知识','k2':'产品知识','k3':'销售话术','k4':'菜单条目'};
model.knowledge.items.forEach(item=>item.category=knowledgeCategories[item.id]);
model.knowledge.items.push(
  {id:'k5',title:'公司简介与服务范围.docx',kind:'文档',category:'公司文档',owner:'林总',visibility:'团队共享',source:'本地文档',time:'2026-09-23',version:'v1',product:'公司',original:'我们提供设备演示与门店培训。具体服务以合同约定为准。',summary:'公司业务介绍与服务边界。',params:'服务：设备演示、门店培训',issue:'无'},
  {id:'k6',title:'菜品B菜单卡.png',kind:'图片文字',category:'菜单条目',owner:'小李',visibility:'团队共享',source:'本地图片',time:'2026-09-23',version:'v1',product:'菜品B',original:'菜品B：现做现出餐；配料信息请以现场菜单为准。',summary:'菜品介绍可引用现做出餐；配料需要现场核对。',params:'配料：待现场核对',issue:'过敏提示待核对'},
  {id:'k7',title:'设备接待答疑.html',kind:'网页',category:'销售话术',owner:'林总',visibility:'团队共享',source:'演示网页采集',time:'2026-09-24',version:'采集版1',product:'设备A',original:'先问使用场景，再说明设备操作流程；参数以人工确认版本为准。',summary:'接待问答结构：场景、操作、参数确认。',params:'参数不预设结论',issue:'无'}
);
let activeTaskId=model.currentTask;
window.activeTaskTitle=taskData[activeTaskId].title;
const navNames={home:'首页',assets:'素材库',knowledge:'知识库',people:'人物与声音',templates:'视频模板',scheduled:'定时任务',publishing:'发布与数据',spaces:'成员与空间'};
function saveCurrentTask(){
  const task=taskData[activeTaskId];
  task.editor=window.editorTaskController.save();
  task.stage=window.stageController.save();
}
function visibleTasks(){return Object.values(taskData).filter(task=>task.owner===currentUser||task.visibility==='团队共享');}
function renderTaskList(){
  W('#taskList').innerHTML='<div class="nav-section"><span>创作任务</span><small>本页演示</small></div>'+
    visibleTasks().map(task=>'<button class="task'+(model.page==='task'&&activeTaskId===task.id?' active':'')+
      '" type="button" data-task-id="'+we(task.id)+'"><strong>'+we(task.title)+'</strong><small>'+we(task.status)+' · '+we(task.owner)+'</small></button>').join('');
}
function openTask(id){
  const task=taskData[id];
  if(!task||(task.owner!==currentUser&&task.visibility!=='团队共享'))return;
  if(model.page==='task')saveCurrentTask();
  activeTaskId=id;model.currentTask=id;model.page='task';window.activeTaskTitle=task.title;
  W('#workspace').classList.remove('global-mode');
  W('#globalView').hidden=true;W('#workspace .stage-strip').hidden=false;
  W('.top-title strong').textContent=task.title;
  W('.top-title span').textContent=task.status+(task.template?' · 模板：'+task.template:'');
  window.currentTaskSource=task.source||'';
  W('.message.user').textContent=task.goal+'。';
  window.editorTaskController.load(task.editor);
  window.stageController.load(task.stage);
  renderNavState();
}
function openPage(page){
  if(model.page==='task')saveCurrentTask();
  model.page=page;
  W('#workspace').classList.add('global-mode');
  W('#workspace .stage-strip').hidden=true;W('#editor').hidden=true;W('#stageView').hidden=true;W('#globalView').hidden=false;
  const label=page==='new'?'新建任务':navNames[page];
  W('.top-title strong').textContent=label;W('.top-title span').textContent='本机工作空间 · 演示';
  W('#assistantTarget').textContent=label;W('#assistantTime').textContent='当前页面 · '+currentUser;
  W('#scopeChip').textContent=label+' · 演示';
  W('.message.user').textContent='查看'+label+'里的内容。';
  W('#assistantReply').textContent='这里展示'+label+'的本页演示状态；不会连接真实服务。';
  renderNavState();
}
function renderNavState(){
  renderTaskList();
  document.querySelectorAll('[data-nav]').forEach(button=>button.classList.toggle('active',model.page===button.dataset.nav));
  W('#newTaskButton').classList.toggle('active',model.page==='new');
  if(model.page!=='task')renderPage();
}
function homeTrend(days,items){
  const offsets=[0,0,1,2,2,3,4,5,6,7,8,9,12,15,18,22,27];
  const bins=days===7?Array(7).fill(0):Array(6).fill(0);
  items.forEach(item=>{
    const i=model.assets.items.indexOf(item),offset=offsets[i]??0;
    if(offset<days)bins[days===7?6-offset:5-Math.floor(offset/5)]++;
  });
  return bins;
}
function trendDate(offset){
  const date=new Date(Date.UTC(2026,8,27-offset));
  return (date.getUTCMonth()+1)+'/'+date.getUTCDate();
}
function trendSvg(values,days){
  const max=Math.max(1,...values),points=values.map((v,i)=>(12+i*43)+','+(66-v/max*48)).join(' ');
  return '<svg viewBox="0 0 286 78" preserveAspectRatio="none" role="img" aria-label="演示素材接收趋势"><path d="M12 68H274" stroke="#d6e2e2"/><polyline points="'+points+'" fill="none" stroke="#327f81" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'+
    values.map((v,i)=>'<g><title>'+(days===30?trendDate((5-i)*5+4)+'–'+trendDate((5-i)*5):trendDate(6-i))+'：'+v+' 条素材</title><circle cx="'+(12+i*43)+'" cy="'+(66-v/max*48)+'" r="3.5" fill="#327f81"/><text x="'+(12+i*43)+'" y="'+Math.max(10,58-v/max*48)+'" text-anchor="middle" font-size="8" fill="#3a7476">'+v+'</text></g>').join('')+'</svg>';
}
function renderHome(){
  const accessible=model.assets.items.filter(permitted),ready=accessible.filter(item=>item.analysis==='可用').length;
  const pending=accessible.length-ready,knowledge=visibleKnowledge().length;
  const works=model.publishing.items.filter(item=>isOwner||item.owner===currentUser);
  const range=model.homeRange||7,trend=homeTrend(range,accessible);
  const types=['设备','餐饮','门店'],colors=['#327f81','#7473a6','#d59a50'];
  let offset=0;
  const arcs=types.map((type,i)=>{
    const count=accessible.filter(item=>item.type===type).length,dash=count/Math.max(1,accessible.length)*251;
    const arc='<circle cx="50" cy="50" r="40" fill="none" stroke="'+colors[i]+'" stroke-width="18" stroke-dasharray="'+dash+' 251" stroke-dashoffset="'+(-offset)+'" transform="rotate(-90 50 50)" data-work-action="home-donut" data-value="'+type+'" style="cursor:pointer"/>';
    offset+=dash;return arc;
  }).join('');
  const tags={};
  accessible.forEach(item=>new Set([...item.auto,...item.manual]).forEach(tag=>tags[tag]=(tags[tag]||0)+1));
  const cloud=Object.entries(tags).sort((a,b)=>b[1]-a[1]).slice(0,16).map(([tag,count])=>
    '<button type="button" class="cloud-word" style="font-size:'+(12+Math.min(18,count*3))+'px" data-work-action="home-tag" data-value="'+we(tag)+'" title="'+count+' 条可见素材">'+we(tag)+'<sup>'+count+'</sup></button>').join('');
  const queue=model.queue.map(item=>{
    const own=item.owner===currentUser,anonymous=!own&&(!isOwner||item.private);
    const title=anonymous?'成员任务（内容私有）':item.title;
    const reason=anonymous?'任务内容仅所属成员可见':item.reason;
    return '<div class="home-queue-row"><span class="queue-dot '+(item.stage==='制作中'?'live':'')+'"></span><div><strong>'+we(item.kind)+' · '+we(title)+'</strong><small>'+we(item.owner)+' · '+we(item.stage)+' · '+we(reason)+'</small></div>'+
      (own?wb('打开','open-task',item.owner==='小陈'?'store':'device'):isOwner&&item.stage==='排队'?wb('移到队首','queue-first',item.id):'')+'</div>';
  }).join('');
  const output=works.slice(0,3).map((item,i)=>'<div class="home-output"><span class="home-output-thumb tone-'+i+'">'+(i===0?'▥':i===1?'▣':'◌')+'</span><span><strong>'+we(item.title)+'</strong><small>'+we(item.status)+'</small></span></div>').join('');
  return '<div class="phase-head"><div><h1>工作空间总览</h1><p>'+(isOwner?'整机运行与成员汇总':'整机资源可见；内容统计仅含我的及共享素材')+' · 下列数字均为同一组演示数据</p></div><span class="phase-status">'+(model.host==='online'?'● 主机在线':'○ 主机离线')+' · 演示</span></div>'+
    '<div class="home-dashboard"><div class="home-metrics">'+
      [['可见素材',accessible.length],['分析可用',ready],['待分析',pending],['知识来源',knowledge],['作品记录',works.length]].map(([label,num])=>'<div><strong>'+num+'</strong><small>'+label+' · 演示</small></div>').join('')+
      '</div><section class="home-trend phase-card"><div class="home-title"><div><h2>素材接收趋势</h2><small>按演示素材的接收日分组 · 当前可见范围</small></div><div class="phase-choice-row">'+wb('7 天','home-range','7',range===7)+wb('30 天','home-range','30',range===30)+'</div></div>'+
      trendSvg(trend,range)+'<div class="home-chart-labels"><span>'+(range===7?trendDate(6):trendDate(29))+'</span><span>'+trendDate(0)+' · 演示日期</span></div></section>'+
      '<section class="home-distribution phase-card"><h2>素材行业分布</h2><div class="home-donut-layout"><svg viewBox="0 0 100 100" role="img" aria-label="可见素材行业占比">'+arcs+'<text x="50" y="47" text-anchor="middle" font-size="18" fill="#25383b">'+accessible.length+'</text><text x="50" y="61" text-anchor="middle" font-size="8" fill="#687579">可见素材</text></svg><div class="home-legend">'+types.map((type,i)=>'<button type="button" data-work-action="home-donut" data-value="'+type+'"><i style="background:'+colors[i]+'"></i>'+type+' <b>'+accessible.filter(item=>item.type===type).length+'</b></button>').join('')+'</div></div><small>点选行业查看素材 · 演示</small></section>'+
      '<section class="home-queue phase-card"><div class="home-title"><div><h2>任务与处理阶段</h2><small>同一时刻演示 1 项重制作</small></div>'+wb(model.host==='online'?'离线演示':'恢复在线','toggle-host')+'</div>'+
      '<div class="home-load"><span>重制作</span><div><i style="width:'+(model.host==='online'?'100':'0')+'%"></i></div><b>'+model.queue.filter(item=>item.stage==='制作中').length+'/1</b></div>'+queue+
      '<p class="home-footnote">'+we(model.homeNote||'等待资源与人工确认是不同阶段；预计时间仅供安排。')+'</p></section>'+
      '<section class="home-cloud phase-card"><div class="home-title"><div><h2>内容标签词云</h2><small>字号随可见素材中的标签次数变化 · 点词进入素材库</small></div></div><div class="cloud-field">'+cloud+'</div></section>'+
      '<section class="home-recent phase-card"><h2>近期作品</h2>'+output+'<div class="home-capacity"><span>演示素材槽位</span><strong>'+model.assets.items.length+' / 30</strong><div><i style="width:'+Math.min(100,model.assets.items.length/30*100)+'%"></i></div><small>容量示意，非真实存储读数</small></div></section></div>';
}
function renderSpaces(){
  if(!isOwner)return '';
  const d=model,used=d.spaces.length;
  const rows=d.spaces.map(item=>'<div class="global-item"><div><strong>'+we(item.name)+'</strong><small>成员：'+we(item.member)+' · '+we(item.status)+'</small></div>'+
    (item.status==='待加入'?wb('模拟接受邀请','accept-invite',item.id):'')+'</div>').join('');
  const invite=d.invite?'<div class="phase-card invite-card"><h2>邀请卡 · 演示</h2><div class="fake-qr">演示码<br>不可扫码绑定</div>'+
    '<p>空间：'+we(d.invite.name)+' · 邀请：'+we(d.invite.member)+'</p><p>状态：'+we(d.invite.status)+'</p>'+
    (d.invite.status==='待加入'?wb('模拟成员接受','accept-invite',d.invite.id,false,true):'')+
    '<div class="phase-note">这不是二维码，不会创建真实邀请或账号。</div></div>':'';
  return panel('成员与空间','按需创建空间并邀请成员；不要求把所有名额用满。',used+'/5 成员名额',
    card('现有空间','<div class="global-list">'+rows+'</div>'),
    card('新增空间','<p>当前主账号：林总。新空间独立归属与私有内容。</p>'+
      '<label class="phase-label" for="spaceName">空间名称</label><input id="spaceName" maxlength="18" placeholder="例如：新品项目" value="'+we(d.spaceDraft.name)+'">'+
      '<label class="phase-label" for="memberName">邀请成员称呼</label><input id="memberName" maxlength="12" placeholder="例如：小周" value="'+we(d.spaceDraft.member)+'">'+
      '<div class="phase-actions">'+wb('创建空间并显示邀请卡','create-space','',false,true)+'</div>'+
      (d.spaceMessage?'<p class="global-save">'+we(d.spaceMessage)+'</p>':'')+
      '<div class="phase-note">公共素材库并非公共账号；素材仍按归属和共享范围显示。</div>'+invite));
}
function permitted(item){return item.owner===currentUser||item.visibility==='团队共享'||(item.sharedWith||[]).includes(currentUser);}
function filteredAssets(){
  const d=model.assets;
  return d.items.filter(item=>permitted(item)&&
    (d.scope==='可见全部'||d.scope==='全部可用'&&item.analysis==='可用'||d.scope==='我的'&&item.owner===currentUser||d.scope==='团队共享'&&item.visibility==='团队共享')&&
    (d.type==='全部类型'||item.type===d.type)&&
    (d.analysis==='全部状态'||item.analysis===d.analysis)&&
    (d.rating==='全部评价'||item.rating===d.rating)&&
    (!d.tag||item.auto.includes(d.tag)||item.manual.includes(d.tag)));
}
function assetDetail(){
  const d=model.assets,item=d.items.find(a=>a.id===d.selected);
  if(!d.detail||!item||!permitted(item))return '';
  const auto=item.auto.map((tag,index)=>wb(tag+' ×','remove-auto-tag',index)).join('');
  const manual=item.manual.map((tag,index)=>wb(tag+' ×','remove-manual-tag',index)).join('');
  let watermark='';
  if(item.source.startsWith('https://')){
    const wm=d.watermark;
    watermark='<div class="detail-section"><h3>去水印副本 · 流程演示</h3>'+
      (wm.phase==='none'?wb('查看副本流程','watermark-start',item.id):'')+
      (wm.phase==='consent'?'<p>先确认你有权修改和使用这段来源素材。</p>'+wb(wm.authorized?'已确认修改授权':'确认修改授权','watermark-authorize','',wm.authorized)+
        wb('查看模拟对比','watermark-compare','',false,true):'')+
      (wm.phase==='compare'?'<div class="compare-grid"><div>原片示意<br>保留不变</div><div>副本示意<br>仅界面比较</div></div>'+
        wb('确认创建演示副本','watermark-create','',false,true):'')+
      (wm.phase==='done'?'<p>演示副本已加入素材库，原片保留。</p>':'')+
      '<small>没有下载、处理或去除真实水印。</small></div>';
  }
  return '<section class="global-detail" aria-label="素材详情"><header><div><strong>'+we(item.name)+'</strong><small>'+we(item.type)+' · '+we(item.analysis)+'</small></div>'+
    wb('收起','close-asset')+'</header><div class="global-detail-body">'+
    '<div class="detail-preview"><strong>'+we(item.type)+'画面示意</strong><small>'+we(item.time)+'</small></div>'+
    '<div class="phase-actions">'+wb(d.previewPlaying?'暂停播放示意':'播放示意','preview-asset')+
      (item.analysis!=='可用'?wb('推进分析示意','advance-analysis',item.id):'')+'</div>'+
    '<div class="detail-section"><h3>片段描述</h3><textarea id="assetDescription">'+we(d.descriptionDraft??item.description)+'</textarea>'+
      wb('保存描述','save-asset-description','',false,true)+'</div>'+
    '<div class="detail-section"><h3>自动标签</h3><div class="phase-choice-row">'+auto+'</div>'+
      '<h3>人工标签</h3><div class="phase-choice-row">'+manual+'</div>'+
      '<input id="manualTagDraft" placeholder="补充人工标签" value="'+we(d.manualTagDraft||'')+'">'+wb('加入标签','add-manual-tag')+'</div>'+
    '<div class="detail-section"><h3>人工评价</h3><div class="phase-choice-row">'+
      ['高','中','低','未评价'].map(v=>wb(v,'rate-asset',v,item.rating===v)).join('')+'</div>'+
      wb(item.avoid?'恢复自动创作候选':'暂不用于自动创作','toggle-avoid','',item.avoid)+
      '<small>高评价仅在内容相关、授权可用的候选中优先。</small></div>'+
    '<div class="detail-section"><h3>归属与来源</h3><p>所属人：'+we(item.owner)+' · 可见范围：'+we(item.visibility)+'</p>'+
      '<p>来源：'+we(item.source)+'</p><p>使用授权：'+we(item.authorization)+'</p></div>'+
    watermark+(d.assetMessage?'<p class="global-save">'+we(d.assetMessage)+'</p>':'')+
    '</div><footer><span>本页演示，不改原文件</span>'+wb('关闭详情','close-asset')+'</footer></section>';
}
function uploadPanel(){
  const d=model.assets,u=d.upload;
  if(!d.uploadMode)return '';
  const link=d.uploadMode==='link';
  return '<section class="global-detail" aria-label="添加素材"><header><div><strong>'+(link?'添加素材链接':'添加示例素材')+'</strong><small>先接收，后分析</small></div>'+
    wb('收起','close-upload')+'</header><div class="global-detail-body">'+
    (link?'<label class="phase-label" for="assetLink">来源链接</label><input id="assetLink" placeholder="https://example.com/clip" value="'+we(u.link)+'">'+
      '<p>链接会保留来源地址和授权状态；不会下载。</p>':
      '<h3>选择一段示例素材</h3><div class="phase-choice-row">'+
      ['设备进料侧面_新.mov','菜品出餐_新.mov','门店陈列_新.mov'].map(name=>wb(name,'choose-sample',name,u.sample===name)).join('')+'</div>')+
    '<div class="detail-section"><h3>归属与可见范围</h3><div class="phase-choice-row">'+
      (isOwner?['林总','小陈','小李'].map(name=>wb(name,'choose-upload-owner',name,u.owner===name)).join(''):
        '<span class="global-tag">所属人：小陈</span>')+'</div>'+
      '<div class="phase-choice-row">'+['私有','团队共享'].map(v=>wb(v,'choose-upload-visibility',v,u.visibility===v)).join('')+'</div></div>'+
    '<div class="detail-section"><h3>整批背景与关联产品 · 可选</h3><div class="phase-choice-row">'+
      ['无','白墙背景','门店环境'].map(v=>wb(v,'choose-background',v,u.background===v)).join('')+'</div>'+
      '<div class="phase-choice-row">'+['未关联','设备A','菜品B','门店服务'].map(v=>wb(v,'choose-product',v,u.product===v)).join('')+'</div></div>'+
    (link?'<div class="detail-section"><h3>来源授权</h3>'+wb(u.authorization==='已确认'?'已核实使用授权':'确认已核实使用授权','confirm-link-auth','',u.authorization==='已确认')+
      '<p>未确认授权的链接不能用于自动创作。</p></div>':'')+
    '<div class="phase-note">接收后先显示“待处理”；分析完成前不能作为可用片段。</div>'+
    (d.uploadMessage?'<p class="global-save">'+we(d.uploadMessage)+'</p>':'')+
    '</div><footer><span>演示接收，不上传</span>'+wb('接收示意','receive-asset','',false,true)+'</footer></section>';
}
function renderAssets(){
  const d=model.assets,items=filteredAssets();
  const ready=items.filter(item=>item.analysis==='可用').length;
  const chips=(values,action,current)=>values.map(v=>wb(v,action,v,current===v)).join('');
  const tiles=items.map(item=>'<article class="asset-tile"><button class="asset-thumb" type="button" data-work-action="open-asset" data-value="'+we(item.id)+'" aria-label="查看'+we(item.name)+'详情">'+
    '<span class="thumb-type">'+we(item.type)+'</span><strong>'+we(item.type==='设备'?'▥':item.type==='餐饮'?'◌':'▣')+'</strong><span class="thumb-length">'+we(item.time)+'</span></button>'+
    '<div class="asset-caption"><strong>'+we(item.name)+'</strong><small>'+we(item.analysis)+' · '+
      (item.rating==='未评价'?'未评价':we(item.rating)+'评价')+' · '+we(item.owner)+'</small></div>'+
    '<div class="asset-tile-actions">'+wb(d.bulk.includes(item.id)?'已选':'批量选','bulk-asset',item.id,d.bulk.includes(item.id))+
    wb('详情','open-asset',item.id)+'</div></article>').join('');
  return '<div class="phase-head"><div><h1>素材库</h1><p>方形缩略图为示意；先完成分析和授权，再进入内容相关候选。</p></div>'+
    '<span class="phase-status">'+items.length+' 条可见 · '+ready+' 可用 / '+(items.length-ready)+' 未完成分析</span></div>'+
    '<div class="asset-toolbar">'+(d.tag?'<div class="phase-note">来自首页词云：'+we(d.tag)+' · '+items.length+' 条关联素材 '+wb('清除标签','asset-clear-tag')+'</div>':'')+
    '<div class="global-filter"><strong>范围</strong>'+chips(['可见全部','全部可用','我的','团队共享'],'asset-scope',d.scope)+'</div>'+
    '<div class="global-filter"><strong>类型</strong>'+chips(['全部类型','设备','餐饮','门店'],'asset-type',d.type)+'</div>'+
    '<div class="global-filter"><strong>分析</strong>'+chips(['全部状态','可用','分析中','待处理'],'asset-analysis',d.analysis)+'</div>'+
    '<div class="global-filter"><strong>评价</strong>'+chips(['全部评价','高','中','低','未评价'],'asset-rating',d.rating)+'</div>'+
    '<div class="global-filter">'+wb('添加示例素材','upload-sample')+wb('添加链接','upload-link')+
      '<span class="asset-bulk-count">已选 '+d.bulk.length+' 条</span>'+
      ['高','中','低'].map(v=>wb('批量设'+v,'bulk-rate',v)).join('')+'</div></div>'+
    '<div class="asset-grid">'+tiles+'</div>'+
    '<div class="phase-note">“高”评价只在内容相关且授权可用的候选中提高优先级；“暂不用于自动创作”会排除该片段。</div>'+
    assetDetail()+uploadPanel();
}
function visibleKnowledge(){return model.knowledge.items.filter(permitted);}
function renderKnowledge(){
  const d=model.knowledge,visible=visibleKnowledge(),item=visible.find(entry=>entry.id===d.selected)||visible[0];
  if(item&&item.id!==d.selected){d.selected=item.id;d.editor=item.summary;}
  const categories=['全部','产品知识','公司文档','菜单条目','销售话术'];
  const filtered=visible.filter(entry=>(d.category==='全部'||entry.category===d.category)&&
    (!d.query||[entry.title,entry.product,entry.summary].join(' ').toLowerCase().includes(d.query.toLowerCase())));
  const categoryCards=categories.slice(1).map((category,index)=>{
    const count=visible.filter(entry=>entry.category===category).length;
    return '<button type="button" class="knowledge-category cat-'+index+'" data-work-action="knowledge-category" data-value="'+category+'" aria-pressed="'+(d.category===category)+'"><span>'+['▥','▤','◫','◧'][index]+'</span><strong>'+category+'</strong><small>'+count+' 条可见来源</small></button>';
  }).join('');
  const covers=filtered.map((entry,index)=>'<button type="button" class="knowledge-cover kind-'+we(entry.kind)+'" data-work-action="select-knowledge" data-value="'+we(entry.id)+'">'+
    '<span class="knowledge-art"><i>'+we(entry.kind==='图片文字'?'图片':entry.kind==='PDF'?'PDF文档':entry.kind==='网页'||entry.kind==='链接'?'网页':'文档')+'</i><b>'+we(entry.product)+'</b></span>'+
    '<strong>'+we(entry.title)+'</strong><small>'+we(entry.category)+' · '+we(entry.version)+'</small><em>'+we(entry.issue==='无'?'可引用':entry.issue.includes('冲突')||entry.issue.includes('另一份')?'冲突待确认':entry.issue==='待解析'?'待解析':'需核对')+'</em></button>').join('');
  const upload=d.uploadMode?'<div class="detail-section"><h3>'+(d.uploadMode==='link'?'收集链接':'上传示例来源')+'</h3>'+
    '<label class="phase-label" for="knowledgeTitle">来源名称</label><input id="knowledgeTitle" value="'+we(d.draftTitle)+'" placeholder="例如：设备使用说明">'+
    (d.uploadMode==='link'?'<label class="phase-label" for="knowledgeLink">来源地址</label><input id="knowledgeLink" value="'+we(d.draftLink)+'" placeholder="https://example.com/source">':
      '<div class="phase-choice-row">'+['文档','PDF','图片文字'].map(v=>wb(v,'knowledge-type',v,d.draftType===v)).join('')+'</div>')+
    '<div class="phase-choice-row">'+['设备A','菜品B','门店服务','未关联'].map(v=>wb(v,'knowledge-product',v,d.draftProduct===v)).join('')+'</div>'+
    '<div class="phase-actions">'+wb('接收来源示意','knowledge-receive','',false,true)+wb('取消','knowledge-cancel')+'</div></div>':'';
  const detail=d.detailOpen&&item?'<section class="global-detail knowledge-detail"><header><div><strong>'+we(item.title)+'</strong><small>'+we(item.kind)+' · '+we(item.version)+' · '+we(item.owner)+'</small></div>'+wb('关闭','knowledge-close')+'</header><div class="global-detail-body">'+
    '<div class="knowledge-compare"><div><h3>原始来源片段</h3><p>'+we(item.original)+'</p></div><div><h3>AI 摘要 · 演示</h3><p>'+we(item.summary)+'</p></div></div>'+
    '<div class="detail-section"><h3>参数与出处</h3><p>'+we(item.params)+'</p><small>'+we(item.source)+' · '+we(item.time)+' · '+we(item.visibility)+'</small></div>'+
    '<div class="phase-note">核对状态：'+we(item.issue)+'。冲突内容不会自动合并。</div>'+
    '<div class="detail-section"><h3>人工纠正</h3><textarea id="knowledgeSummary">'+we(d.editor)+'</textarea><label class="phase-label" for="knowledgeParams">参数记录</label><textarea id="knowledgeParams">'+we(d.paramsDraft??item.params)+'</textarea><div class="phase-actions">'+wb('保存人工纠正','knowledge-save')+
      (item.issue==='待解析'?wb('模拟解析完成','knowledge-parse'):'')+'</div></div>'+
    (d.problem?'<p class="global-save">'+we(d.problem)+'</p>':'')+'</div><footer><span>来源可追溯 · 演示</span>'+wb('用于当前任务','knowledge-use','',false,true)+'</footer></section>':'';
  return '<div class="phase-head"><div><h1>知识库</h1><p>按用途找来源，再核对原文、摘要与版本。仅展示当前角色可见内容。</p></div><span class="phase-status">'+visible.length+' 条可见 · 演示</span></div>'+
    '<div class="knowledge-page"><div class="knowledge-top"><div class="knowledge-categories">'+categoryCards+'</div><div class="knowledge-actions">'+wb('上传示例','knowledge-upload')+wb('收集链接','knowledge-link')+'</div></div>'+
    '<div class="knowledge-search"><label for="knowledgeSearch">查找来源</label><input id="knowledgeSearch" value="'+we(d.query)+'" placeholder="搜索标题、产品、摘要">'+wb('全部','knowledge-category','全部',d.category==='全部')+'</div>'+
    '<div class="knowledge-columns"><section class="phase-card"><div class="home-title"><div><h2>来源封面</h2><small>'+filtered.length+' 条结果 · 点封面查看可引用内容</small></div></div><div class="knowledge-grid">'+(covers||'<p>这个分类暂时没有可见来源。</p>')+'</div></section>'+
    '<section class="phase-card knowledge-map"><div class="home-title"><div><h2>主题关系</h2><small>产品 → 来源 → 创作引用</small></div>'+wb(d.topicOpen?'收起':'展开','knowledge-topic')+'</div>'+
      (d.topicOpen?'<div class="knowledge-nodes"><div class="topic-root">设备 A</div><div class="topic-lines"><span></span><span></span></div><div class="topic-sources">'+visible.filter(entry=>entry.product==='设备A').map(entry=>wb(entry.title,'select-knowledge',entry.id)).join('')+'</div><div class="topic-use">创作任务：'+we(taskData[activeTaskId].title)+'<br><small>引用需人工核对冲突参数</small></div></div>':'')+
      '<div class="knowledge-qa"><h3>试问一题 · 固定演示答案</h3><p>设备 A 的额定功率是多少？</p>'+wb('查看示例回答','knowledge-qa')+
      (d.qa?'<div class="phase-note">两份来源分别写 800W 与 1000W，当前有冲突，需人工确认。'+
        wb('说明书','select-knowledge','k1')+wb('参数卡','select-knowledge','k2')+'</div>':'')+'</div></section></div>'+upload+'</div>'+detail;
}
function visiblePeople(){return model.people.items.filter(permitted);}
function renderPeople(){
  const d=model.people,visible=visiblePeople().filter(item=>item.kind===d.tab);
  let selected=visible.find(item=>item.id===d.selected);
  if(!selected){selected=visible[0];if(selected)d.selected=selected.id;}
  const rows=visible.map(item=>'<div class="global-item'+(selected&&item.id===selected.id?' selected':'')+'"><div><strong>'+we(item.name)+'</strong>'+
    '<small>'+we(item.status)+' · '+we(item.owner)+' · '+we(item.visibility)+'</small></div>'+
    wb('查看','select-person',item.id,selected&&item.id===selected.id)+'</div>').join('');
  return panel('人物与声音','人物素材和声音授权分别记录；参考人物不等于生成人物视频。',d.tab+' · '+visible.length+' 条',
    card('素材与授权','<div class="phase-choice-row">'+['人物','声音'].map(v=>wb(v,'people-tab',v,d.tab===v)).join('')+'</div>'+
      '<div class="global-list">'+rows+'</div>'),
    card(selected?we(selected.name):'详情',selected?
      '<div class="detail-preview person-preview"><strong>'+we(selected.kind)+'参考示意</strong><small>'+we(selected.material)+'</small></div>'+
      '<p>所属人：'+we(selected.owner)+' · '+we(selected.visibility)+'</p><p>状态：'+we(selected.status)+'</p>'+
      '<p>授权范围：'+we(selected.auth)+'</p><p>关联'+(selected.kind==='声音'?'人物':'声音')+'：'+we(selected.linked)+'</p>'+
      '<div class="phase-actions">'+(selected.kind==='声音'?wb(d.auditioned?'已试听示意':'试听示意','people-audition'):'')+
      wb('用于当前任务','people-use','',false,true)+'</div>'+
      '<div class="phase-note">'+we(d.message||'使用前检查归属、授权范围与当前处理状态；不调用克隆或数字人能力。')+'</div>':
      '<p>当前角色没有可见素材。</p>'));
}
function renderTemplates(){
  const d=model.templates,item=d.items.find(entry=>entry.id===d.selected)||d.items[0],task=taskData[activeTaskId];
  const info={
    t1:{category:'设备',duration:'30 秒',ratio:'16:9',scenario:'产品操作讲解',shots:['整机外观','关键部件','实际操作','安全提醒'],available:'整机、部件、操作',missing:'出料镜头分析中'},
    t2:{category:'餐饮',duration:'25 秒',ratio:'9:16',scenario:'出餐过程',shots:['食材','下锅','摆盘','上桌'],available:'食材、烹饪、上桌',missing:'摆盘镜头待解析'},
    t3:{category:'门店',duration:'40 秒',ratio:'9:16',scenario:'门店日常',shots:['门头','接待','陈列','打包'],available:'门头、接待、陈列',missing:'打包收尾待解析'},
    t4:{category:'新品',duration:'20 秒',ratio:'1:1',scenario:'新品速览',shots:['包装','细节','使用','总结'],available:'包装、细节',missing:'使用场景素材不足'}
  };
  const visual=(entry,large=false)=>{
    const labels={t1:'设备 / 从细节到操作',t2:'一道菜的出餐过程',t3:'门店 / 一天的现场',t4:'新品 / 第一眼'};
    return '<div class="template-visual visual-'+entry.id+(large?' large':'')+'"><div class="visual-grain"></div>'+
      (entry.id==='t1'?'<div class="visual-machine"><i></i><b></b><em></em></div>':
       entry.id==='t2'?'<div class="visual-plate"><i></i><b></b></div>':
       entry.id==='t3'?'<div class="visual-store"><i></i><b></b><em></em></div>':
       '<div class="visual-box"><i></i><b></b></div>')+
      '<span class="visual-kicker">方案 0'+entry.id.slice(1)+'</span><strong>'+labels[entry.id]+'</strong><small>模板画面示意</small></div>';
  };
  const cards=d.items.map(entry=>{
    const x=info[entry.id];
    return '<button type="button" class="template-card" data-work-action="select-template" data-value="'+entry.id+'">'+visual(entry)+
      '<div class="template-card-copy"><span>'+x.category+' · '+x.scenario+'</span><strong>'+entry.name+'</strong><small>'+x.duration+' · '+x.ratio+'</small></div>'+
      '<div class="template-story-peek">'+x.shots.map((shot,i)=>'<span>0'+(i+1)+' '+shot+'</span>').join('')+'</div></button>';
  }).join('');
  const x=info[item.id];
  const detail=d.detailOpen?'<section class="global-detail template-detail"><header><div><strong>'+we(item.name)+'</strong><small>'+x.category+' · '+x.duration+' · '+x.ratio+'</small></div>'+wb('关闭','template-close')+'</header><div class="global-detail-body">'+visual(item,true)+
    '<div class="template-detail-meta"><span>'+x.scenario+'</span><span>'+x.duration+'</span><span>'+x.ratio+'</span></div>'+
    '<h3>四镜头故事板</h3><div class="template-detail-shots">'+x.shots.map((shot,i)=>'<div><span class="shot-scene scene-'+i+'">0'+(i+1)+'</span><strong>'+shot+'</strong></div>').join('')+'</div>'+
    '<div class="detail-section"><h3>当前任务素材</h3><p>需要：'+we(item.needs)+'</p><p>已有示意：'+x.available+'</p><p class="template-missing">缺口：'+x.missing+'</p></div>'+
    '<div class="phase-note">'+we(d.applied===item.name?'已应用到“'+task.title+'”。缺口仍需补齐，未自动生成视频。':'应用仅为当前任务记录结构；不会自动补素材。')+'</div></div>'+
    '<footer><span>应用到：'+we(task.title)+'</span>'+wb('应用模板','template-use','',false,true)+'</footer></section>':'';
  return '<div class="phase-head"><div><h1>视频模板</h1><p>从画面与镜头顺序挑选结构，再检查当前任务的素材缺口。</p></div><span class="phase-status">'+d.items.length+' 套 · 演示</span></div>'+
    '<div class="template-intro"><strong>选择视频的讲述方式</strong><span>点卡片看四镜头分镜和应用条件；悬停可快速预览。</span></div>'+
    '<div class="template-grid">'+cards+'</div>'+detail;
}
function visibleJobs(){return model.scheduled.jobs.filter(job=>isOwner||job.owner===currentUser);}
function renderScheduled(){
  const d=model.scheduled,visible=visibleJobs(),item=visible.find(job=>job.id===d.selected)||visible[0];
  if(item)d.selected=item.id;
  const rows=visible.map(job=>'<div class="global-item'+(item&&job.id===item.id?' selected':'')+'"><div><strong>'+we(job.name)+'</strong>'+
    '<small>'+we(job.space)+' · '+(job.enabled?'已启用':'暂停中')+' · '+we(job.mode)+'</small></div>'+
    wb('编辑','select-job',job.id,item&&job.id===item.id)+'</div>').join('');
  const right=item?'<h3>当前计划</h3><p>'+we(item.name)+' · '+we(item.space)+'</p>'+
    '<div class="phase-choice-row">'+['到点','闲时'].map(v=>wb(v,'job-mode',v,item.mode===v)).join('')+'</div>'+
    '<label class="phase-label" for="jobTime">演示时间</label><input id="jobTime" inputmode="numeric" value="'+we(d.draftTime)+'">'+
    '<div class="phase-actions">'+wb('保存时间','save-job-time')+wb(item.enabled?'暂停':'启动','toggle-job',item.id,item.enabled)+'</div>'+
    '<p>上次：'+we(item.last)+'</p><p>下次：'+we(item.next)+'</p>'+
    '<div class="phase-actions">'+wb('模拟主机离线','job-offline')+wb('模拟素材不足','job-missing')+'</div>':
    '<p>当前角色暂无定时任务。</p>';
  return panel('定时任务','按空间设置到点或闲时计划；常驻素材解析不依赖定时任务。',visible.length+' 项可见',
    card('空间计划','<div class="global-list">'+rows+'</div>'),
    card('编辑与新增',right+
      '<div class="detail-section"><h3>新增计划</h3><label class="phase-label" for="newJobName">任务名称</label>'+
      '<input id="newJobName" value="'+we(d.draftName)+'" placeholder="例如：夜间整理草稿">'+
      '<div class="phase-choice-row">'+(isOwner?model.spaces.map(space=>wb(space.name,'job-space',space.name,d.draftSpace===space.name)).join(''):
        '<span class="global-tag">所属空间：门店内容</span>')+'</div>'+
      '<div class="phase-actions">'+wb('创建演示计划','create-job','',false,true)+'</div></div>'+
      '<div class="phase-note">'+we(d.message||'本页不运行后台调度，也不在夜间处理文件。')+'</div>'));
}
function visibleReleases(){
  const d=model.publishing;
  return d.items.filter(item=>(isOwner||item.owner===currentUser)&&
    (d.member==='全部成员'||item.owner===d.member)&&(d.platform==='全部平台'||item.platform===d.platform));
}
function renderPublishing(){
  const d=model.publishing,visible=visibleReleases(),item=visible.find(entry=>entry.id===d.selected)||visible[0];
  if(item)d.selected=item.id;
  const rows=visible.map(entry=>'<div class="global-item'+(item&&entry.id===item.id?' selected':'')+'"><div><strong>'+we(entry.title)+'</strong>'+
    '<small>'+we(entry.owner)+' · '+we(entry.platform)+' · '+we(entry.status)+'</small></div>'+
    wb('详情','release-detail',entry.id,item&&entry.id===item.id)+'</div>').join('');
  const memberFilter=isOwner?'<div class="global-filter"><strong>成员</strong>'+
    ['全部成员','林总','小陈','小李','小王'].map(v=>wb(v,'release-member',v,d.member===v)).join('')+'</div>':'';
  return panel('发布与数据','区分导出、人工标记、平台确认与数据同步；数字均为示例。',d.range+' · '+visible.length+' 条可见',
    card('作品状态','<div class="global-filter"><strong>日期</strong>'+['7天','30天','90天'].map(v=>wb(v,'release-range',v,d.range===v)).join('')+'</div>'+
      memberFilter+'<div class="global-filter"><strong>平台</strong>'+
      ['全部平台','视频号','小红书','抖音'].map(v=>wb(v,'release-platform',v,d.platform===v)).join('')+'</div>'+
      '<div class="global-list">'+rows+(rows?'':'<p>当前筛选没有可见作品。</p>')+'</div>'),
    card(item?we(item.title):'作品详情',item?
      '<p>成员：'+we(item.owner)+' · 平台：'+we(item.platform)+'</p><p>日期：'+we(item.date)+'</p>'+
      '<div class="phase-preview"><strong>'+we(item.status)+'</strong><p>'+
      (item.status.includes('数据未同步')||item.status.includes('平台未确认')||item.status.includes('未标记')?
        '暂无平台数据':('示例浏览 '+item.views+' · 仅用于版式演示'))+'</p></div>'+
      '<div class="phase-actions">'+wb('人工标记已发布','release-mark')+wb('模拟平台确认','release-confirm')+
      wb('重试数据同步示意','release-retry')+'</div>'+
      '<div class="phase-note">'+we(d.message||'导出文件不等于发布；人工标记也不是平台确认。')+'</div>':
      '<p>请选择一条可见作品。</p>'));
}
function renderNewTask(){
  const d=model.newTask;
  return panel('新建创作任务','先说明要做什么，再进入资料阶段添加本地素材。','本页演示',
    card('任务简表','<label class="phase-label" for="taskTitle">任务题目</label>'+
      '<input id="taskTitle" maxlength="24" placeholder="例如：新品使用场景" value="'+we(d.title)+'">'+
      '<label class="phase-label">视频目标</label><div class="phase-choice-row">'+
      ['设备讲解','菜品出餐','门店记录','新品介绍'].map(v=>wb(v,'new-goal',v,d.goal===v)).join('')+'</div>'+
      '<label class="phase-label">所属空间</label><div class="phase-choice-row">'+
      (isOwner?model.spaces.filter(space=>space.status==='使用中').map(space=>wb(space.name,'new-space',space.name,d.space===space.name)).join(''):
        '<span class="global-tag">门店内容 · 小陈</span>')+'</div>'+
      '<div class="phase-actions">'+wb('创建并进入资料阶段','create-task','',false,true)+'</div>'+
      (d.error?'<p class="global-save">'+we(d.error)+'</p>':'')),
    card('创建后可做什么','<div class="phase-preview"><strong>'+we(d.title||'待填写任务题目')+'</strong><p>目标：'+we(d.goal)+' · 空间：'+we(d.space)+'</p></div>'+
      '<div class="phase-note">新任务只进入本页创作阶段，不自动分析素材、不生成视频。</div>'));
}
function renderPage(){
  const views={home:renderHome,assets:renderAssets,knowledge:renderKnowledge,people:renderPeople,
    templates:renderTemplates,scheduled:renderScheduled,publishing:renderPublishing,spaces:renderSpaces,new:renderNewTask};
  W('#globalView').innerHTML=views[model.page]();
}
document.addEventListener('click',event=>{
  const nav=event.target.closest('[data-nav]');
  if(nav){if(nav.dataset.nav==='spaces'&&!isOwner)return;openPage(nav.dataset.nav);return;}
  if(event.target.closest('#newTaskButton')){openPage('new');return;}
  const task=event.target.closest('[data-task-id]');
  if(task){openTask(task.dataset.taskId);return;}
  const target=event.target.closest('[data-work-action]');
  if(!target)return;
  const action=target.dataset.workAction,value=target.dataset.value;
  const asset=model.assets,assetItem=asset.items.find(item=>item.id===asset.selected);
  const knowledge=model.knowledge,knowledgeItem=knowledge.items.find(item=>item.id===knowledge.selected);
  if(action==='open-task'){openTask(value);return;}
  if(action==='go-assets'){openPage('assets');return;}
  if(action==='home-range')model.homeRange=Number(value);
  if(action==='home-donut'){
    asset.scope='可见全部';asset.type=value;asset.analysis='全部状态';asset.rating='全部评价';asset.tag='';
    openPage('assets');return;
  }
  if(action==='home-tag'){
    asset.scope='可见全部';asset.type='全部类型';asset.analysis='全部状态';asset.rating='全部评价';asset.tag=value;
    openPage('assets');return;
  }
  if(action==='show-home-note')model.homeNote='这项属于其他成员的任务；全机状态可见，内容不显示。';
  if(action==='toggle-host')model.host=model.host==='online'?'offline':'online';
  if(action==='queue-first'){
    const index=model.queue.findIndex(item=>item.id===value);
    if(index>0&&model.queue[index].stage==='排队')model.queue.unshift(model.queue.splice(index,1)[0]);
  }
  if(action==='create-space'){
    const name=model.spaceDraft.name.trim(),member=model.spaceDraft.member.trim();
    if(model.spaces.length>=5)model.spaceMessage='5/5 名额已用完；此页不再新增。';
    else if(!name||!member)model.spaceMessage='请填写空间名称和成员称呼。';
    else{
      const id='space'+(model.spaces.length+1);
      const entry={id,name,member,status:'待加入'};
      model.spaces.push(entry);model.invite=entry;
      model.spaceDraft={name:'',member:''};model.spaceMessage='邀请卡已生成，等待成员接受示意。';
    }
  }
  if(action==='accept-invite'){
    const entry=model.spaces.find(item=>item.id===value);
    if(entry){entry.status='使用中';model.invite=entry;model.spaceMessage=entry.member+'已在演示中接受邀请。';}
  }
  if(action==='asset-scope')asset.scope=value;
  if(action==='asset-type')asset.type=value;
  if(action==='asset-analysis')asset.analysis=value;
  if(action==='asset-rating')asset.rating=value;
  if(action==='asset-clear-tag')asset.tag='';
  if(action==='open-asset'){
    const item=asset.items.find(entry=>entry.id===value);
    if(item&&permitted(item)){
      asset.selected=value;asset.detail=true;asset.uploadMode='';
      asset.descriptionDraft=item.description;asset.manualTagDraft='';
      asset.previewPlaying=false;asset.assetMessage='';
      asset.watermark={phase:'none',authorized:false,sourceId:value};
    }
  }
  if(action==='close-asset')asset.detail=false;
  if(action==='preview-asset')asset.previewPlaying=!asset.previewPlaying;
  if(action==='advance-analysis'&&assetItem){
    assetItem.analysis=assetItem.analysis==='待处理'?'分析中':'可用';
    asset.assetMessage=assetItem.analysis==='可用'?'分析示意完成，片段现在可用。':'已进入分析中，暂不可用于创作。';
  }
  if(action==='save-asset-description'&&assetItem){
    assetItem.description=asset.descriptionDraft.trim()||assetItem.description;
    asset.assetMessage='描述已保存到本页素材记录。';
  }
  if(action==='remove-auto-tag'&&assetItem){assetItem.auto.splice(Number(value),1);asset.assetMessage='已移除自动标签示例。';}
  if(action==='remove-manual-tag'&&assetItem){assetItem.manual.splice(Number(value),1);asset.assetMessage='已移除人工标签。';}
  if(action==='add-manual-tag'&&assetItem){
    const tag=(asset.manualTagDraft||'').trim();
    if(tag&&!assetItem.manual.includes(tag)){assetItem.manual.push(tag);asset.assetMessage='人工标签已加入。';}
    asset.manualTagDraft='';
  }
  if(action==='rate-asset'&&assetItem){assetItem.rating=value;asset.assetMessage='人工评价已更新为'+value+'。';}
  if(action==='toggle-avoid'&&assetItem){
    assetItem.avoid=!assetItem.avoid;
    asset.assetMessage=assetItem.avoid?'已排除自动创作候选。':'已恢复为可被筛选的候选。';
  }
  if(action==='bulk-asset'){
    asset.bulk=asset.bulk.includes(value)?asset.bulk.filter(id=>id!==value):[...asset.bulk,value];
  }
  if(action==='bulk-rate'){
    for(const item of asset.items)if(asset.bulk.includes(item.id)&&permitted(item))item.rating=value;
    asset.assetMessage='已批量设置 '+asset.bulk.length+' 条为'+value+'评价。';
    asset.bulk=[];
  }
  if(action==='upload-sample'||action==='upload-link'){
    asset.uploadMode=action==='upload-link'?'link':'sample';asset.detail=false;asset.uploadMessage='';
    asset.upload.authorization='待确认';
  }
  if(action==='close-upload')asset.uploadMode='';
  if(action==='choose-sample')asset.upload.sample=value;
  if(action==='choose-upload-owner'&&isOwner)asset.upload.owner=value;
  if(action==='choose-upload-visibility')asset.upload.visibility=value;
  if(action==='choose-background')asset.upload.background=value;
  if(action==='choose-product')asset.upload.product=value;
  if(action==='confirm-link-auth')asset.upload.authorization=asset.upload.authorization==='已确认'?'待确认':'已确认';
  if(action==='receive-asset'){
    const u=asset.upload,link=asset.uploadMode==='link';
    if(link&&!/^https?:\/\//.test(u.link.trim()))asset.uploadMessage='请输入以 http:// 或 https:// 开头的来源链接。';
    else{
      const name=link?'链接素材_'+(asset.items.length+1):u.sample;
      const type=name.includes('菜品')?'餐饮':name.includes('门店')?'门店':'设备';
      const id='a'+(asset.items.length+1);
      asset.items.push({id,name,type,owner:isOwner?u.owner:currentUser,visibility:u.visibility,analysis:'待处理',rating:'未评价',
        avoid:false,time:'待分析',description:'已接收；整批背景：'+u.background+'；关联产品：'+u.product,
        auto:[],manual:[],source:link?u.link:'示例本地素材',authorization:link?u.authorization:'本人确认可用'});
      asset.selected=id;asset.detail=true;asset.uploadMode='';asset.descriptionDraft=asset.items.at(-1).description;
      asset.assetMessage='已接收，尚未分析可用。';
      asset.scope='可见全部';asset.type='全部类型';asset.analysis='全部状态';
    }
  }
  if(action==='watermark-start'&&assetItem)asset.watermark={phase:'consent',authorized:false,sourceId:assetItem.id};
  if(action==='watermark-authorize')asset.watermark.authorized=!asset.watermark.authorized;
  if(action==='watermark-compare'){
    if(asset.watermark.authorized)asset.watermark.phase='compare';
    else asset.assetMessage='请先确认你有权修改与使用该来源素材。';
  }
  if(action==='watermark-create'&&assetItem){
    const copy=structuredClone(assetItem);
    copy.id='a'+(asset.items.length+1);copy.name=assetItem.name+' · 演示副本';
    copy.source='原片 '+assetItem.id+' 的本页副本示意';copy.analysis='待处理';
    copy.manual.push('副本示意');asset.items.push(copy);
    asset.watermark.phase='done';asset.assetMessage='已创建演示副本；原片 '+assetItem.name+' 保留。';
  }
  if(model.page==='assets'){renderPage();return;}
  if(action==='select-knowledge'){
    const item=knowledge.items.find(entry=>entry.id===value);
    if(item&&permitted(item)){knowledge.selected=value;knowledge.editor=item.summary;knowledge.paramsDraft=item.params;knowledge.problem='';knowledge.detailOpen=true;}
  }
  if(action==='knowledge-close')knowledge.detailOpen=false;
  if(action==='knowledge-category')knowledge.category=value;
  if(action==='knowledge-topic')knowledge.topicOpen=!knowledge.topicOpen;
  if(action==='knowledge-qa')knowledge.qa=!knowledge.qa;
  if(action==='knowledge-upload'||action==='knowledge-link'){
    knowledge.uploadMode=action==='knowledge-link'?'link':'file';knowledge.problem='';
  }
  if(action==='knowledge-cancel')knowledge.uploadMode='';
  if(action==='knowledge-type')knowledge.draftType=value;
  if(action==='knowledge-product')knowledge.draftProduct=value;
  if(action==='knowledge-receive'){
    const title=knowledge.draftTitle.trim(),link=knowledge.uploadMode==='link';
    if(!title)knowledge.problem='请填写来源名称。';
    else if(link&&!/^https?:\/\//.test(knowledge.draftLink.trim()))knowledge.problem='请输入完整来源链接。';
    else{
      const id='k'+(knowledge.items.length+1);
      const item={id,title,kind:link?'链接':knowledge.draftType,owner:currentUser,visibility:'私有',
        category:knowledge.draftProduct==='菜品B'?'菜单条目':knowledge.draftProduct==='门店服务'?'销售话术':'产品知识',
        source:link?knowledge.draftLink:'示例来源',time:'2026-09-27',version:'待解析',product:knowledge.draftProduct,
        original:'来源已接收，尚未提取可引用片段。',summary:'待解析',params:'待解析',issue:'待解析'};
      knowledge.items.push(item);knowledge.selected=id;knowledge.editor=item.summary;knowledge.paramsDraft=item.params;
      knowledge.uploadMode='';knowledge.draftTitle='';knowledge.draftLink='';knowledge.detailOpen=true;
      knowledge.problem='已接收来源；解析完成前不可作为事实引用。';
    }
  }
  if(action==='knowledge-parse'&&knowledgeItem){
    knowledgeItem.summary='解析示意已完成，请人工核对原文。';
    knowledgeItem.original='来源片段示意：此处保留原文供人工核对。';
    knowledgeItem.params='参数待人工核对';knowledgeItem.issue='需要人工核对';
    knowledge.editor=knowledgeItem.summary;knowledge.paramsDraft=knowledgeItem.params;
    knowledge.problem='解析状态已更新，仍需人工核对。';
  }
  if(action==='knowledge-save'&&knowledgeItem){
    knowledgeItem.summary=knowledge.editor.trim()||knowledgeItem.summary;
    knowledgeItem.params=(knowledge.paramsDraft||'').trim()||knowledgeItem.params;
    knowledge.problem='人工纠正已保存；参数冲突仍保持待确认。';
  }
  if(action==='knowledge-use'&&knowledgeItem){
    if(knowledgeItem.issue==='待解析'||knowledgeItem.issue.includes('未授权'))knowledge.problem='来源尚未解析或使用授权不足，不可引用。';
    else{taskData[activeTaskId].source=knowledgeItem.title+' · '+knowledgeItem.version;window.currentTaskSource=taskData[activeTaskId].source;knowledge.problem='当前任务已记录来源：'+knowledgeItem.title+'。';}
  }
  if(action==='people-tab'){
    model.people.tab=value;model.people.auditioned=false;model.people.message='';
  }
  if(action==='select-person'){model.people.selected=value;model.people.auditioned=false;model.people.message='';}
  if(action==='people-audition'){
    const item=model.people.items.find(entry=>entry.id===model.people.selected);
    if(item&&item.kind==='声音'&&item.status==='可用'){model.people.auditioned=true;model.people.message='试听步骤已完成；无真实音频播放。';}
    else model.people.message='声音仍在处理中，暂无可试听版本。';
  }
  if(action==='people-use'){
    const item=model.people.items.find(entry=>entry.id===model.people.selected),task=taskData[activeTaskId];
    const allowed=item&&permitted(item)&&item.licensed&&
      (item.kind==='人物'?item.status==='参考素材可用':item.status==='可用');
    if(!allowed)model.people.message='当前状态或授权范围不足，未加入任务。';
    else if(item.kind==='声音'){
      task.stage.voice.selected=item.name;task.stage.voice.applied=item.name;
      model.people.message='已将“'+item.name+'”用于当前任务的讲解示意。';
    }else{task.personReference=item.name;model.people.message='已将“'+item.name+'”记录为人物参考；未生成人物视频。';}
  }
  if(action==='select-template'){model.templates.selected=value;model.templates.detailOpen=true;}
  if(action==='template-close')model.templates.detailOpen=false;
  if(action==='template-use'){
    const entry=model.templates.items.find(item=>item.id===model.templates.selected);
    taskData[activeTaskId].template=entry.name;model.templates.applied=entry.name;
  }
  if(action==='select-job'){
    const job=model.scheduled.jobs.find(item=>item.id===value);
    if(job&&(isOwner||job.owner===currentUser)){model.scheduled.selected=value;model.scheduled.draftTime=job.time;model.scheduled.message='';}
  }
  if(action==='job-mode'){
    const job=model.scheduled.jobs.find(item=>item.id===model.scheduled.selected);
    if(job){job.mode=value;job.next=value==='闲时'?'主机空闲后尝试':'下次 '+job.time+' 尝试';}
  }
  if(action==='job-space'&&isOwner)model.scheduled.draftSpace=value;
  if(action==='save-job-time'){
    const job=model.scheduled.jobs.find(item=>item.id===model.scheduled.selected),time=model.scheduled.draftTime;
    if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(time))model.scheduled.message='时间请使用 HH:MM。';
    else if(job){job.time=time;job.next=job.enabled?(job.mode==='闲时'?'主机空闲后尝试':'下次 '+time+' 尝试'):'暂停中';model.scheduled.message='演示时间已保存为 '+time+'。';}
  }
  if(action==='toggle-job'){
    const job=model.scheduled.jobs.find(item=>item.id===value);
    if(job&&(isOwner||job.owner===currentUser)){
      job.enabled=!job.enabled;job.next=job.enabled?(job.mode==='闲时'?'主机空闲后尝试':'下次 '+job.time+' 尝试'):'暂停中';
      model.scheduled.message=job.enabled?'计划已启用示意。':'计划已暂停示意。';
    }
  }
  if(action==='job-offline'||action==='job-missing'){
    const job=model.scheduled.jobs.find(item=>item.id===model.scheduled.selected);
    if(job){job.last=action==='job-offline'?'本次跳过 · 主机离线':'本次跳过 · 素材不足';job.next=job.enabled?'条件恢复后再尝试':'暂停中';
      model.scheduled.message='已显示'+(action==='job-offline'?'离线':'素材不足')+'结果示例。';}
  }
  if(action==='create-job'){
    const d=model.scheduled,name=d.draftName.trim();
    if(!name)d.message='请填写计划名称。';
    else{
      const id='s'+(d.jobs.length+1),space=isOwner?d.draftSpace:'门店内容';
      d.jobs.push({id,name,owner:currentUser,space,mode:'到点',time:'22:00',enabled:false,last:'尚未运行',next:'暂停中'});
      d.selected=id;d.draftName='';d.draftTime='22:00';d.message='已创建演示计划，默认暂停。';
    }
  }
  if(action==='release-range')model.publishing.range=value;
  if(action==='release-member'&&isOwner)model.publishing.member=value;
  if(action==='release-platform')model.publishing.platform=value;
  if(action==='release-detail')model.publishing.selected=value;
  if(['release-mark','release-confirm','release-retry'].includes(action)){
    const d=model.publishing,item=d.items.find(entry=>entry.id===d.selected);
    if(item&&(isOwner||item.owner===currentUser)){
      if(action==='release-mark'){item.status='人工标记已发布 · 平台未确认';d.message='已作人工标记，不代表平台确认。';}
      if(action==='release-confirm'){item.status='平台确认 · 数据未同步';d.message='平台确认示意已记录，数据仍未同步。';}
      if(action==='release-retry'){
        if(item.status.includes('数据未同步')){item.status='平台确认 · 示例数据已同步';item.views=item.views||48;d.message='重试示意完成；这里的浏览数字是样本。';}
        else d.message='当前作品没有待重试的数据同步。';
      }
    }
  }
  if(action==='new-goal')model.newTask.goal=value;
  if(action==='new-space'&&isOwner)model.newTask.space=value;
  if(action==='create-task'){
    const d=model.newTask,title=d.title.trim();
    if(!title)d.error='请填写任务题目。';
    else{
      const id='new'+(Object.keys(taskData).length+1),names=['开场画面','主题素材','细节素材','过程镜头','补充角度','人物或场景','收尾画面','结束定格'];
      const files=names.map((name,index)=>'本地素材_'+String(index+1).padStart(2,'0')+'.mov');
      const editor=makeEditor('custom',names,files,[
        '先说明这次视频主题。','这里是第一段本地素材。','看一个关键细节。','展示实际过程。','换一个角度补充。','让观众看清现场。','回顾主要内容。','本次记录到这里结束。']);
      const stage=makeStage(title,files.slice(0,3),[title+'的三个看点','从素材说明主题','一段本地记录'],
        ['先说明这次视频的主题。','再用本地镜头展示主要内容。','最后回顾重点。'],'assets');
      taskData[id]={id,title,owner:currentUser,space:isOwner?d.space:'门店内容',status:'资料待整理',kind:'custom',goal:d.goal,editor,stage,source:''};
      model.newTask={title:'',goal:'设备讲解',space:isOwner?'主空间':'门店内容',error:''};
      openTask(id);return;
    }
  }
  renderPage();
});
document.addEventListener('input',event=>{
  const id=event.target.id,value=event.target.value;
  if(id==='spaceName')model.spaceDraft.name=value;
  if(id==='memberName')model.spaceDraft.member=value;
  if(id==='assetDescription')model.assets.descriptionDraft=value;
  if(id==='manualTagDraft')model.assets.manualTagDraft=value;
  if(id==='assetLink')model.assets.upload.link=value;
  if(id==='knowledgeSummary')model.knowledge.editor=value;
  if(id==='knowledgeParams')model.knowledge.paramsDraft=value;
  if(id==='knowledgeTitle')model.knowledge.draftTitle=value;
  if(id==='knowledgeLink')model.knowledge.draftLink=value;
  if(id==='knowledgeSearch'){
    model.knowledge.query=value;
    const selection=event.target.selectionStart;
    renderPage();
    const input=W('#knowledgeSearch');
    input.focus();input.setSelectionRange(selection,selection);
  }
  if(id==='jobTime')model.scheduled.draftTime=value;
  if(id==='newJobName')model.scheduled.draftName=value;
  if(id==='taskTitle')model.newTask.title=value;
});
openPage('home');
