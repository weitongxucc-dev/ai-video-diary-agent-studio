import test from 'node:test';
import assert from 'node:assert/strict';
import {registerCreationCandidates} from '../diary/creation-candidates.ts';
import {createBusinessRequest} from '../diary/transport.ts';

test('candidate discovery and fixed readback share the background business boundary', async () => {
  const calls = [], registered = new Map();
  const request = createBusinessRequest({apiBase:'', taskId:'task', selectedTaskVersion:null,
    verifyTaskVersion:()=>{}, readCredentials:()=>({}), readActionSource:()=>({turnId:'turn'}),
    getSignal:()=>undefined, fetchRequest:()=>{throw Error('must use business owner');},
    businessCall: async (operation,args) => {
      calls.push({operation,args});
      return {ok:true,value:{schema:operation==='search_creation_candidates'?'candidate-discovery/v1':'candidate-readback/v1'}};
    }});
  registerCreationCandidates({registerTool:t=>registered.set(t.name,t)},request,'task');
  await registered.get('search_creation_candidates').execute('search',{query:'钢琴',limit_per_domain:3});
  await registered.get('read_creation_candidate').execute('read',{domain:'voice',object_id:'voice/a',version:'v2'});
  assert.deepEqual(calls,[
    {operation:'search_creation_candidates',args:{query:'钢琴',limit_per_domain:3,task_id:'task'}},
    {operation:'read_creation_candidate',args:{domain:'voice',object_id:'voice/a',version:'v2',task_id:'task'}},
  ]);
});

test('standalone discovery needs no selected material and rejects an unexpected schema', async () => {
  const tools = new Map(), paths=[];
  registerCreationCandidates({registerTool:t=>tools.set(t.name,t)},async path=>{
    paths.push(path);return {schema:'wrong'};
  });
  await assert.rejects(tools.get('search_creation_candidates').execute('search',{query:'voice'}),/契约版本/);
  assert.equal(paths[0],'/api/creation-candidates/search?q=voice&limit_per_domain=5');
  await assert.rejects(tools.get('read_creation_candidate').execute('read',{
    domain:'knowledge',object_id:'entry',version:'1'}),/契约版本/);
  assert.equal(paths[1],'/api/creation-candidates/knowledge/entry?version=1');
});
