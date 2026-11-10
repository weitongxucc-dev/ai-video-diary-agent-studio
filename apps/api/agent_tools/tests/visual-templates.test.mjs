import test from 'node:test';
import assert from 'node:assert/strict';
import {createBusinessRequest} from '../diary/transport.ts';
import {registerVisualTemplateTools} from '../diary/visual-template-tools.ts';

test('template list, fixed source, save and update use typed host operations',async()=>{
  const calls=[];
  const request=createBusinessRequest({taskId:'task',selectedTaskVersion:{timeline_version_id:'v1',timeline_sequence:1},
    verifyTaskVersion(){},readCredentials(){throw Error('no HTTP credentials');},getSignal:()=>undefined,
    fetchRequest(){throw Error('no HTTP fallback');},businessCall:async(operation,args)=>{
      calls.push({operation,args});return {ok:true,value:{id:'mine',version:1,origin:'user',name:'Card',
        settings:{style_instruction:'preserve',visual:{source:'window.film={frame(t){}}',bindings:[],values:{},provenance:[]}}}};
    }});
  await request('/api/video-templates');
  await request('/api/video-templates/mine?version=1');
  await request('/api/video-templates',{method:'POST',body:JSON.stringify({name:'copy'})});
  await request('/api/video-templates/mine/versions',{method:'POST',body:JSON.stringify({expected_version:1,name:'v2'})});
  assert.deepEqual(calls.map(item=>item.operation),['read_video_templates','read_visual_template','save_visual_template','update_visual_template']);
  assert.deepEqual(calls[1].args,{template_id:'mine',version:1});
  assert.deepEqual(calls[3].args,{template_id:'mine',payload:{expected_version:1,name:'v2'}});
});

test('unknown save keeps original locator and cannot repeat under a new effect',async()=>{
  const tools=new Map(),writes=[];
  const definition={engine:'openfilm-html-v1',source:'window.film={frame(t){}}',bindings:[],values:{},provenance:[]};
  const request=async(path,init)=>{
    if(init?.method==='POST'){writes.push({path,payload:JSON.parse(init.body)});throw Error('reply lost');}
    return {id:'builtin',version:1,origin:'built_in',settings:{visual:definition}};
  };
  registerVisualTemplateTools({registerTool:tool=>tools.set(tool.name,tool)},{request,
    readActionSource:()=>({turnId:'turn',userInstruction:'保存为我的模板'}),
    typedRequestId:(_turn,effect)=>effect.startsWith('visual-template-save:')?'32b9a80b-c605-4e93-b273-87c6cd559034':'da0602e6-d4a7-473a-836e-089a1d32429f'});
  const params={source_template_id:'builtin',source_template_version:1,name:'mine',visual:definition};
  await assert.rejects(()=>tools.get('save_visual_template').execute('save',params),/先读取/);
  const read=await tools.get('read_visual_template').execute('read',{template_id:'builtin',version:1,source_limit:10});
  assert.equal(read.details.source_window.truncated,true);
  const saved=await tools.get('save_visual_template').execute('save',params);
  assert.equal(saved.isError,true);
  assert.equal(saved.details.query_url,'/api/video-templates/template_32b9a80bc6054e93b27387c6cd559034?version=1');
  await assert.rejects(()=>tools.get('save_visual_template').execute('new-call-after-lost-reply',params),/原保存已提交/);
  assert.equal(writes.length,1);
});
