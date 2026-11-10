import test from 'node:test';
import assert from 'node:assert/strict';
import {registerMaterialEvidence} from '../materials/evidence.ts';
import {createBusinessRequest} from '../diary/transport.ts';

test('source evidence traverses the real background transport with finite window and original revision',async()=>{
  const calls=[];
  const request=createBusinessRequest({apiBase:'',taskId:'task',selectedTaskVersion:null,
    verifyTaskVersion:()=>{},readCredentials:()=>({}),readActionSource:()=>({turnId:'turn',inputId:'input'}),
    getSignal:()=>undefined,fetchRequest:()=>{throw new Error('background must not reenter HTTP');},
    businessCall:async(operation,args)=>{calls.push({operation,args});return {ok:true,value:{asset_id:'asset',
      updated_at:'revision',kind:'words',items:[{text:'限定',start_ms:1230,end_ms:1640}],page:{has_more:false}}};}});
  let tool;registerMaterialEvidence({registerTool:value=>{tool=value;}},request);
  const result=await tool.execute('call',{asset_id:'asset',expected_updated_at:'revision',kind:'words',
    start_seconds:1,end_seconds:2,offset:3,limit:4});
  assert.equal(result.details.items[0].start_ms,1230);
  assert.deepEqual(calls,[{operation:'read_material_evidence',args:{asset_id:'asset',expected_updated_at:'revision',
    kind:'words',start_seconds:1,end_seconds:2,offset:3,limit:4}}]);
});
