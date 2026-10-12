import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp,writeFile,chmod,symlink,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {runCli,readConfig} from './yaohe.mjs';

const REQUEST = '12345678-1234-4321-8123-123456789abc';
const TOKEN = 'FAKE-LOCAL-TOKEN-only-synthetic';
const TASK = 'task_synthetic';
const PREFIX = `/api/creation-tasks/${TASK}`;
const query = phase => `${PREFIX}/assistant/edit-requests/${REQUEST}?phase=${phase}`;
const absent = response => send(response,404,{detail:'not found'});
const send = (response,status,data) => { response.writeHead(status,{'Content-Type':'application/json'}); response.end(JSON.stringify(data)); };

async function fixture(t,handler) {
  const directory = await mkdtemp(join(tmpdir(),'yaohe-cli-'));
  const calls = [];
  const server = createServer(async (request,response) => {
    const chunks=[]; for await (const chunk of request) chunks.push(chunk);
    const text = Buffer.concat(chunks).toString();
    const call={method:request.method,url:request.url,headers:request.headers,body:text ? JSON.parse(text) : null};
    calls.push(call);
    try { await handler(call,response); } catch { send(response,500,{detail:'fixture handler failed'}); }
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const config=join(directory,'config.json');
  await writeFile(config,JSON.stringify({api_base:`http://127.0.0.1:${server.address().port}`,local_member_token:TOKEN}),{mode:0o600});
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve=>server.close(resolve)); await rm(directory,{recursive:true,force:true}); });
  return {directory,config,calls,async cli(...args) {
    let output=''; const code=await runCli(['--config',config,...args],{stdout:{write:value=>output+=value}});
    return {code,output,json:JSON.parse(output)};
  }};
}

test('ordinary capability/task/material GET consumers preserve API schemas',async t=>{
  const f=await fixture(t,(call,res)=>{
    if(call.url.endsWith('/assistant/edit-capabilities')) return send(res,200,{operations:[{operation_type:'ReplaceClip',available:true}],schema:{properties:{operation_type:{type:'string'}}},units:{source_start_seconds:'seconds'}});
    return send(res,200,{server:'actual-fixture'});
  });
  assert.equal((await f.cli('capabilities','list')).code,0);
  assert.equal((await f.cli('capabilities','list','--task',TASK)).code,0);
  const described=await f.cli('capabilities','describe','ReplaceClip','--task',TASK);
  assert.equal(described.json.data.schema.properties.operation_type.type,'string');
  await f.cli('task','inspect',TASK);
  await f.cli('material','inspect','asset_synthetic');
  await f.cli('material','search','--query','城市','--role','b_roll','--limit','8');
  assert.ok(f.calls.every(call=>call.method==='GET'&&call.headers['x-local-member-token']===TOKEN));
  assert.equal(f.calls[0].url,'/api/assistant/capabilities');
  assert.equal(new URL(f.calls.at(-1).url,'http://fixture').searchParams.get('usage_role'),'b_roll');
});

test('preview binds current version once and keeps a stable UUID without assistant identity',async t=>{
  const f=await fixture(t,(call,res)=>{
    if(call.url===query('preview')) return absent(res);
    if(call.url===PREFIX) return send(res,200,{task:{current_timeline_version_id:'version_original',timeline_sequence:7}});
    return send(res,200,{proposal:{id:'proposal_synthetic'},preview:{}});
  });
  const action=join(f.directory,'edit.json');
  await writeFile(action,JSON.stringify({operation_type:'ReplaceClip',clip_id:'clip_2',asset_id:'asset_2',segment_id:'segment_1'}));
  const result=await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST);
  assert.equal(result.code,0);
  assert.deepEqual(f.calls.map(call=>[call.method,call.url]),[['GET',query('preview')],['GET',PREFIX],['POST',PREFIX+'/edits/exact/preview']]);
  const body=f.calls.at(-1).body;
  assert.equal(body.request_id,REQUEST);
  assert.equal(body.expected_timeline_version_id,'version_original');
  assert.equal(body.expected_timeline_sequence,7);
  assert.ok(!Object.hasOwn(body,'assistant_turn_id'));
});

test('explicit preview base is preserved and API conflict never retries',async t=>{
  const f=await fixture(t,(call,res)=>call.method==='GET' ? absent(res) : send(res,409,{detail:'stale base'}));
  const action=join(f.directory,'edit.json');
  await writeFile(action,JSON.stringify({operation_type:'TrimClip',clip_id:'clip_2',source_start_seconds:1,
    expected_timeline_version_id:'version_old',expected_timeline_sequence:3}));
  const result=await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST);
  assert.equal(result.code,1); assert.equal(result.json.http_status,409);
  assert.equal(f.calls.at(-1).body.expected_timeline_version_id,'version_old');
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
  assert.ok(!f.calls.some(call=>call.url===PREFIX));
});

test('unknown identity only queries and recorded missing binding cannot use a new task base',async t=>{
  let state='unknown';
  const f=await fixture(t,(call,res)=>send(res,200,{status:state,result_status_code:state==='recorded'?200:null,
    result:state==='recorded'?{proposal:{id:'proposal_old'}}:null}));
  let result=await f.cli('edit','preview',TASK,'--file','not-read.json','--request-id',REQUEST);
  assert.equal(result.code,4); assert.equal(result.json.submitted,false);
  state='recorded';
  const action=join(f.directory,'edit.json');await writeFile(action,JSON.stringify({operation_type:'SetClipLock',clip_id:'clip_1',locked:true}));
  result=await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST);
  assert.equal(result.code,4); assert.equal(result.json.request_locator,query('preview'));
  assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('same request identity with changed preview parameters is checked by the API',async t=>{
  const f=await fixture(t,(call,res)=>call.method==='GET'?send(res,200,{status:'recorded',result_status_code:200,result:{proposal:{id:'proposal_old'}}}):send(res,409,{detail:'request bound to different input'}));
  const action=join(f.directory,'edit.json');await writeFile(action,JSON.stringify({operation_type:'SetClipLock',clip_id:'clip_1',locked:false,expected_timeline_version_id:'v1',expected_timeline_sequence:1}));
  const result=await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST);
  assert.equal(result.code,1);assert.equal(result.json.http_status,409);
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
  assert.equal(f.calls.at(-1).body.locked,false);
});

test('recorded preview reuses only original base and sends current action for API comparison',async t=>{
  const f=await fixture(t,(call,res)=>call.method==='GET'?send(res,200,{status:'recorded',result_status_code:200,
    submitted_input:{operation_type:'SetClipLock',clip_id:'clip_1',locked:true,request_id:REQUEST,
      expected_timeline_version_id:'original_v1',expected_timeline_sequence:1,assistant_turn_id:'must_not_copy'},
    result:{proposal:{id:'proposal_old'}}}):send(res,409,{detail:'different action'}));
  const action=join(f.directory,'edit.json');await writeFile(action,JSON.stringify({operation_type:'SetClipLock',clip_id:'clip_1',locked:false}));
  const result=await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST);
  assert.equal(result.code,1);assert.equal(f.calls.at(-1).body.locked,false);
  assert.equal(f.calls.at(-1).body.expected_timeline_version_id,'original_v1');
  assert.ok(!Object.hasOwn(f.calls.at(-1).body,'assistant_turn_id'));
  assert.ok(!f.calls.some(call=>call.url===PREFIX));
});

test('recorded same request with a different proposal is rejected by common API',async t=>{
  const f=await fixture(t,(call,res)=>call.method==='GET'?send(res,200,{status:'recorded',result_status_code:200,
    submitted_input:{proposal_id:'proposal_old',base_version_id:'original_base',input_digest:'a'.repeat(64),
      request:{request_id:REQUEST,expected_base_version_id:'original_base',expected_input_digest:'a'.repeat(64)}},
    result:{operation:{id:'operation_old'}}}):send(res,409,{detail:'different proposal'}));
  const result=await f.cli('edit','apply',TASK,'proposal_new','--request-id',REQUEST);
  assert.equal(result.code,1);assert.equal(result.json.http_status,409);
  assert.equal(f.calls.at(-1).url,PREFIX+'/edits/proposal_new/confirm');
  assert.equal(f.calls.at(-1).body.expected_base_version_id,'original_base');
  assert.ok(!f.calls.some(call=>call.url===PREFIX));
});

test('unknown POST queries once and returns original locator without a second POST',async t=>{
  const f=await fixture(t,(call,res)=>call.method==='GET' ?
    call.url===PREFIX ? send(res,200,{task:{current_timeline_version_id:'v1',timeline_sequence:1}}) : absent(res)
    : send(res,500,{detail:'result lost'}));
  const action=join(f.directory,'edit.json'); await writeFile(action,JSON.stringify({operation_type:'SetClipLock',clip_id:'clip_1',locked:true}));
  const result=await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST);
  assert.equal(result.code,4); assert.equal(result.json.request_locator,query('preview'));
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
  assert.equal(f.calls.filter(call=>call.url===query('preview')).length,2);
});

test('apply reads original proposal base/digest and queued never counts as completion',async t=>{
  const f=await fixture(t,(call,res)=>{
    if(call.url===query('apply')) return absent(res);
    if(call.url.endsWith('/result')) return send(res,200,{proposal_id:'proposal_1',base_version_id:'original_base',input_digest:'a'.repeat(64),status:'proposed',result:null});
    return send(res,200,{status:'queued',job_id:'job_1',request_id:REQUEST});
  });
  const result=await f.cli('edit','apply',TASK,'proposal_1','--request-id',REQUEST);
  assert.equal(result.code,3); assert.equal(result.json.state,'pending');
  assert.deepEqual(f.calls.at(-1).body,{request_id:REQUEST,expected_base_version_id:'original_base',expected_input_digest:'a'.repeat(64)});
  assert.ok(!f.calls.some(call=>call.url===PREFIX));
});

test('request query preserves failed original HTTP result',async t=>{
  const f=await fixture(t,(call,res)=>send(res,200,{status:'recorded',result_status_code:409,result:{detail:'stale'}}));
  const result=await f.cli('request','query',TASK,REQUEST,'--phase','apply');
  assert.equal(result.code,1); assert.equal(result.json.data.result_status_code,409);
  assert.equal(f.calls[0].url,query('apply'));
});

test('job wait is finite and cancellation is a single ordinary POST',async t=>{
  const f=await fixture(t,(call,res)=>send(res,200,{job_id:'job_1',status:call.method==='POST'?'cancelled':'queued',result:null}));
  const start=Date.now();
  const waiting=await f.cli('job','wait',TASK,'job_1','--timeout-seconds','0.03');
  assert.equal(waiting.code,3); assert.ok(Date.now()-start<1000);
  const cancelled=await f.cli('job','cancel',TASK,'job_1');
  assert.equal(cancelled.code,0); assert.equal(cancelled.json.data.status,'cancelled');
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
});

test('job succeeded needs result and failed remains failed',async t=>{
  let state='succeeded',result=null;
  const f=await fixture(t,(call,res)=>send(res,200,{job_id:'job_1',status:state,result}));
  assert.equal((await f.cli('job','inspect',TASK,'job_1')).code,4);
  result={artifact_id:'actual_fixture'};
  assert.equal((await f.cli('job','inspect',TASK,'job_1')).code,0);
  state='failed'; assert.equal((await f.cli('job','inspect',TASK,'job_1')).code,1);
});

test('timeline binds once, without inventing request/assistant fields',async t=>{
  const f=await fixture(t,(call,res)=>send(res,200,call.method==='GET'?
    {task:{current_timeline_version_id:'v3',timeline_sequence:3}}:{operation:{id:'op_undo'}}));
  assert.equal((await f.cli('timeline','undo',TASK)).code,0);
  assert.deepEqual(f.calls.at(-1).body,{expected_timeline_version_id:'v3',expected_timeline_sequence:3});
  await f.cli('timeline','redo',TASK,'--expected-version','v2','--expected-sequence','2');
  assert.equal(f.calls.at(-1).url,PREFIX+'/timeline/redo');
  assert.equal(f.calls.filter(call=>call.method==='GET').length,1);
});

test('401 and echoed credentials produce a redacted failure',async t=>{
  const f=await fixture(t,(call,res)=>send(res,401,{detail:`invalid ${TOKEN}`}));
  const result=await f.cli('task','inspect',TASK);
  assert.equal(result.code,1); assert.equal(result.json.http_status,401);
  assert.ok(!result.output.includes(TOKEN));
});

test('cross-origin redirect never sends any credential to the destination',async t=>{
  let leaked=false;
  const target=createServer((req,res)=>{leaked=true;send(res,200,{})});
  await new Promise(resolve=>target.listen(0,'127.0.0.1',resolve));
  t.after(async()=>{target.closeAllConnections();await new Promise(resolve=>target.close(resolve));});
  const f=await fixture(t,(call,res)=>{res.writeHead(307,{Location:`http://127.0.0.1:${target.address().port}/sink`});res.end();});
  const result=await f.cli('task','inspect',TASK);
  assert.equal(result.code,4); assert.equal(leaked,false);
});

test('config rejects permissions, symlink, wrong owner and invalid secret JSON without echo',async t=>{
  const f=await fixture(t,(call,res)=>send(res,200,{}));
  await chmod(f.config,0o644);
  assert.equal((await f.cli('task','inspect',TASK)).code,2);
  await chmod(f.config,0o600);
  const link=join(f.directory,'alias'); await symlink(f.config,link);
  await assert.rejects(readConfig(link));
  const getuid=process.getuid;
  try { process.getuid=()=>getuid()+1; await assert.rejects(readConfig(f.config),/0600/); }
  finally { process.getuid=getuid; }
  await writeFile(f.config,`{"local_member_token":"${TOKEN}", invalid`);
  const invalid=await f.cli('task','inspect',TASK);
  assert.equal(invalid.code,2); assert.ok(!invalid.output.includes(TOKEN));
  assert.equal(f.calls.length,0);
});

test('lost POST response can recover an actual receipt with one GET and no rePOST',async t=>{
  let posted=false;
  const f=await fixture(t,(call,res)=>{
    if(call.method==='POST') {posted=true;res.destroy();return;}
    if(call.url===query('preview')) return posted ? send(res,200,{status:'recorded',result_status_code:200,result:{proposal:{id:'proposal_recovered'}}}) : absent(res);
    return send(res,200,{task:{current_timeline_version_id:'v1',timeline_sequence:1}});
  });
  const action=join(f.directory,'edit.json'); await writeFile(action,JSON.stringify({operation_type:'SetClipLock',clip_id:'clip_1',locked:true}));
  const result=await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST);
  assert.equal(result.code,0); assert.equal(result.json.recovered,true);
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
});

test('CLI rejects identity injection and credential arguments before mutation',async t=>{
  const f=await fixture(t,(call,res)=>absent(res));
  const action=join(f.directory,'edit.json'); await writeFile(action,JSON.stringify({operation_type:'SetClipLock',assistant_turn_id:REQUEST}));
  assert.equal((await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST)).code,2);
  assert.equal((await f.cli('task','inspect',TASK,'--local-member-token',TOKEN)).code,2);
  assert.equal(f.calls.filter(call=>call.method==='POST').length,0);
});

test('action file is bounded and regular before any mutation',async t=>{
  const f=await fixture(t,(call,res)=>absent(res));
  const action=join(f.directory,'large.json'); await writeFile(action,' '.repeat(65537));
  assert.equal((await f.cli('edit','preview',TASK,'--file',action,'--request-id',REQUEST)).code,2);
  assert.equal((await f.cli('edit','preview',TASK,'--file','/dev/zero','--request-id',REQUEST)).code,2);
  assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('actual Node entry process emits JSON and a pending exit code',async t=>{
  const f=await fixture(t,(call,res)=>send(res,200,{job_id:'job_1',status:'queued',result:null}));
  const child=spawn(process.execPath,[fileURLToPath(new URL('./yaohe.mjs',import.meta.url)),
    '--config',f.config,'job','inspect',TASK,'job_1'],{stdio:['ignore','pipe','pipe']});
  let output='',errors='';
  child.stdout.on('data',chunk=>output+=chunk);child.stderr.on('data',chunk=>errors+=chunk);
  const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve);});
  assert.equal(code,3);assert.equal(JSON.parse(output).data.status,'queued');
  assert.equal(errors,'');assert.ok(!output.includes(TOKEN));
});

test('unavailable query and job reads retain exact recovery locators',async t=>{
  const f=await fixture(t,(call,res)=>send(res,503,{detail:'temporarily unavailable'}));
  const request=await f.cli('request','query',TASK,REQUEST,'--phase','apply');
  assert.equal(request.code,4);assert.equal(request.json.request_locator,query('apply'));
  const job=await f.cli('job','inspect',TASK,'job_1');
  assert.equal(job.code,4);assert.equal(job.json.job_locator,PREFIX+'/media-jobs/job_1');
  assert.ok(f.calls.every(call=>call.method==='GET'));
});

test('existing authorization and cookie are file-only and redacted even in API echoes',async t=>{
  const auth='FAKE-BEARER-from-file',cookie='FAKE-COOKIE-from-file';
  const f=await fixture(t,(call,res)=>send(res,200,{authorization:call.headers.authorization,
    cookie:call.headers.cookie,bare:auth}));
  const base=JSON.parse(await (await import('node:fs/promises')).readFile(f.config,'utf8')).api_base;
  await writeFile(f.config,JSON.stringify({api_base:base,authorization:'Bearer '+auth,cookie:'session='+cookie}));
  const result=await f.cli('task','inspect',TASK);
  assert.equal(result.code,0);
  assert.equal(f.calls[0].headers.authorization,'Bearer '+auth);
  assert.equal(f.calls[0].headers.cookie,'session='+cookie);
  assert.equal(f.calls[0].headers['x-local-member-token'],undefined);
  assert.ok(!result.output.includes(auth)&&!result.output.includes(cookie));
});

test('first apply of an already completed proposal still binds stable identity through confirm',async t=>{
  let bound=false;
  const operation={id:'operation_original'};
  const f=await fixture(t,(call,res)=>{
    if(call.url===query('apply')) return bound ? send(res,200,{status:'recorded',result_status_code:200,result:{operation}}) : absent(res);
    if(call.url.endsWith('/result')) return send(res,200,{proposal_id:'proposal_1',base_version_id:'original_base',
      input_digest:'a'.repeat(64),status:'confirmed',result:{operation}});
    assert.equal(call.url,PREFIX+'/edits/proposal_1/confirm');
    assert.equal(call.body.request_id,REQUEST);bound=true;
    return send(res,200,{operation});
  });
  const applied=await f.cli('edit','apply',TASK,'proposal_1','--request-id',REQUEST);
  assert.equal(applied.code,0);assert.equal(applied.json.submitted,true);
  assert.equal(applied.json.data.operation.id,'operation_original');
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
  const located=await f.cli('request','query',TASK,REQUEST,'--phase','apply');
  assert.equal(located.code,0);assert.equal(located.json.data.result.operation.id,'operation_original');
});

test('scoped receipt 404 with completed proposal cannot bypass a cross-scope stable-ID conflict',async t=>{
  const f=await fixture(t,(call,res)=>{
    if(call.url===query('apply')) return absent(res);
    if(call.url.endsWith('/result')) return send(res,200,{proposal_id:'proposal_1',base_version_id:'original_base',
      input_digest:'a'.repeat(64),status:'confirmed',result:{operation:{id:'operation_original'}}});
    return send(res,409,{detail:'request identity already belongs to another task'});
  });
  const result=await f.cli('edit','apply',TASK,'proposal_1','--request-id',REQUEST);
  assert.equal(result.code,1);assert.equal(result.json.http_status,409);
  assert.equal(result.json.request_locator,query('apply'));
  assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
});

test('proposal without result and with unknown state remains query-only',async t=>{
  const f=await fixture(t,(call,res)=>call.url===query('apply') ? absent(res) :
    send(res,200,{proposal_id:'proposal_1',base_version_id:'original_base',input_digest:'a'.repeat(64),status:'confirmed',result:null}));
  const result=await f.cli('edit','apply',TASK,'proposal_1','--request-id',REQUEST);
  assert.equal(result.code,4);assert.equal(result.json.submitted,false);
  assert.ok(f.calls.every(call=>call.method==='GET'));
});
