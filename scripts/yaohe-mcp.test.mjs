import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,writeFile,rm,copyFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StdioClientTransport} from '@modelcontextprotocol/sdk/client/stdio.js';

const uuid='12345678-1234-4321-8123-123456789abc';
const token='synthetic-test-member-not-a-secret';
async function fixture(t,handler) {
  const directory=await mkdtemp(join(tmpdir(),'yaohe-mcp-'));
  const calls=[];
  const http=createServer(async(req,res)=>{
    let body=''; for await(const part of req)body+=part;
    const call={path:req.url,method:req.method,headers:req.headers,body:body?JSON.parse(body):null}; calls.push(call);
    const {status=200,data={}}=await handler(call);
    res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));
  });
  await new Promise(resolve=>http.listen(0,'127.0.0.1',resolve));
  const config=join(directory,'private.json');
  const writeConfig=credential=>writeFile(config,JSON.stringify({api_base:`http://127.0.0.1:${http.address().port}`,local_member_token:credential}),{mode:0o600});
  await writeConfig(token);
  let entry=fileURLToPath(new URL('./yaohe-mcp.mjs',import.meta.url));
  if(process.env.YAOHE_TEST_BUNDLE) {
    entry=join(directory,'yaohe-mcp.mjs');
    await copyFile(process.env.YAOHE_TEST_BUNDLE,entry);
  }
  const transport=new StdioClientTransport({command:process.execPath,args:[entry,'--config',config],cwd:directory,stderr:'pipe'});
  const client=new Client({name:'yaohe-acceptance-client',version:'1.0'});
  t.after(async()=>{await client.close();http.closeAllConnections();await new Promise(resolve=>http.close(resolve));await rm(directory,{recursive:true,force:true});});
  await client.connect(transport);
  return {client,calls,writeConfig,async call(name,input){return client.callTool({name,arguments:input});}};
}

test('real stdio negotiation, scoped resource, identity injection and revoked binding',async t=>{
  const f=await fixture(t,call=>call.headers['x-local-member-token']===token
    ? {data:{id:'task_a',current_timeline_version_id:'version_a'}} : {status:403,data:{detail:'revoked'}});
  const listed=await f.client.listTools();
  assert.ok(listed.tools.some(t=>t.name==='conversation_turn'));
  assert.ok(listed.tools.some(t=>t.name==='edit_apply'));
  assert.equal((await f.call('task_inspect',{task_id:'task_a'})).structuredContent.data.id,'task_a');
  const resource=await f.client.readResource({uri:'yaohe://tasks/task_a'});
  assert.match(resource.contents[0].text,/version_a/);
  const before=f.calls.length;
  assert.equal((await f.call('task_inspect',{task_id:'task_a',workspace_id:'someone_else'})).isError,true);
  assert.equal(f.calls.length,before);
  await f.writeConfig('revoked-synthetic-token');
  const denied=await f.call('task_inspect',{task_id:'task_a'});
  assert.equal(denied.isError,true);assert.equal(denied.structuredContent.http_status,403);
});

test('MCP exact preview/apply shares CLI CAS and receipt logic without rewriting identity',async t=>{
  const f=await fixture(t,call=>{
    if(call.path.includes('edit-requests/'))return {status:404,data:{detail:'absent'}};
    if(call.path.endsWith('/edits/exact/preview'))return {data:{proposal_id:'proposal_a',status:'proposed'}};
    if(call.path.endsWith('/edits/proposal_a/result'))return {data:{proposal_id:'proposal_a',status:'proposed',base_version_id:'version_a',input_digest:'e'.repeat(64)}};
    return {data:{proposal_id:'proposal_a',status:'applied',result:{timeline_version_id:'version_b'}}};
  });
  await f.call('edit_preview',{task_id:'task_a',request_id:uuid,action:{operation_type:'TrimClip',clip_id:'clip_a',source_start_seconds:2},expected_timeline_version_id:'version_a',expected_timeline_sequence:3});
  const preview=f.calls.find(c=>c.path.endsWith('/edits/exact/preview'));
  assert.equal(preview.body.expected_timeline_version_id,'version_a');
  assert.equal(preview.body.request_id,uuid);assert.equal(preview.body.member_id,undefined);
  await f.call('edit_apply',{task_id:'task_a',request_id:uuid,proposal_id:'proposal_a'});
  const applied=f.calls.find(c=>c.path.endsWith('/confirm'));
  assert.equal(applied.body.expected_base_version_id,'version_a');
  assert.equal(applied.body.expected_input_digest,'e'.repeat(64));
});

test('MCP failed conversation submit returns original locator and query never resends',async t=>{
  const f=await fixture(t,call=>call.path==='/api/assistant/plan/turn'
    ? {status:503,data:{detail:'response unavailable'}}
    : {data:{client_request_id:uuid,status:'completed',reply:'original reply'}});
  const result=await f.call('conversation_turn',{conversation_id:uuid,request_id:uuid,message:'先讨论文案',expected_draft_version:2,expected_reference_version:0});
  assert.equal(result.isError,true);assert.equal(result.structuredContent.state,'unknown');
  assert.equal(result.structuredContent.request_locator,`/api/assistant/conversations/${uuid}/requests/${uuid}`);
  const query=await f.call('conversation_request',{conversation_id:uuid,request_id:uuid});
  assert.equal(query.structuredContent.data.reply,'original reply');
  assert.equal(f.calls.filter(c=>c.method==='POST').length,1);
  assert.equal(f.calls[0].body.expected_draft_version,2);
});
