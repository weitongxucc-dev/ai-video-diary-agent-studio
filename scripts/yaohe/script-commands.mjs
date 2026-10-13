import {CliError} from './errors.mjs';
import {id, uuid, number, checkArguments} from './arguments.mjs';
import {taskPath, requestPath, version} from './task-binding.mjs';
import {receipt, knownReceipt, submit} from './receipts.mjs';
import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {boundedJson} from './files.mjs';

export async function scriptCommand(api,args) {
  const {positional:p,flags:f} = args;
  const [group,command,task,object] = p;
  const check = (length,allowed) => checkArguments(p,f,length,allowed);
  if (group === 'tool') {
    if (command === 'capabilities') {check(2,[]);return {data:await api('/api/media-script-tools/capabilities')};}
    if (command === 'list') {check(2,[]);return {data:await api('/api/media-script-tools')};}
    if (command === 'describe') {
      check(3,['version']);
      const query=f.version ? `?version=${number(f.version,1,Number.MAX_SAFE_INTEGER)}` : '';
      return {data:await api(`/api/media-script-tools/${id(task)}${query}`)};
    }
    if (command === 'request') {
      check(3,[]);const locator=`/api/media-script-tools/requests/${uuid(task)}`;
      try {return {data:await api(locator),request_locator:locator,submitted:false};}
      catch(error){throw new CliError(error.message,error.code,{...error.details,request_locator:locator});}
    }
    if (command === 'revoke') {
      check(3,['expected-tool-version']);
      if (!f['expected-tool-version']) throw new CliError('停用需要明确expected-tool-version');
      return {data:await api(`/api/media-script-tools/${id(task)}/revoke`,{method:'POST',
        body:{expected_version:number(f['expected-tool-version'],1,Number.MAX_SAFE_INTEGER)}})};
    }
    if (['save','update','preview'].includes(command)) {
      check(command==='preview'?4:3,command==='update'?['file']:['file','request-id']);
      if (!f.file) throw new CliError('配方动作需要 --file JSON');
      let value,file;
      try {file=await open(f.file,constants.O_RDONLY|constants.O_NONBLOCK);
        const stat=await file.stat();if(!stat.isFile()||stat.size>262144)throw new CliError('配方JSON超过读取上限');
        value=await boundedJson(file,262144);
      } catch{throw new CliError('配方动作文件须是有界完整JSON');}finally{await file?.close();}
      if(!value||Array.isArray(value)||typeof value!=='object'||
          ['request_id','assistant_turn_id','workspace_id','member_id','task_id','runtime_paths','script_source','requested_outputs'].some(key=>Object.hasOwn(value,key)))
        throw new CliError('配方动作仅使用API元数据/参数/输入字段，不提供身份、程序代码或输出槽');
      if(command==='update')return {data:await api(`/api/media-script-tools/${id(task)}`,{method:'PATCH',body:value})};
      const requestId=uuid(f['request-id']);
      if(command==='save'){
        const locator=`/api/media-script-tools/requests/${requestId}`;
        const previous=await receipt(api,locator);
        if(previous&&!previous.tool_id)return {code:4,state:'unknown',data:previous,request_locator:locator,submitted_input:value,submitted:false};
        try{return {data:await api(`${taskPath(task)}/media-script-tools`,{method:'POST',body:{...value,request_id:requestId}}),request_locator:locator,submitted:true};}
        catch(error){
          if(error.code!==4)throw new CliError(error.message,error.code,{...error.details,request_locator:locator});
          let original=null;
          try{original=await receipt(api,locator);}catch{/* Query failure stays unknown; never rePOST. */}
          throw new CliError('配方保存结果未知；仅保留原UUID查询，不用旧元数据冒充本次成功',4,
            {request_locator:locator,submitted_input:value,original_result:original});
        }
      }
      const locator=requestPath(task,requestId,'script');
      const previous=await receipt(api,locator);
      if(previous?.status==='unknown')return {...knownReceipt(previous,locator),submitted_input:value};
      let body;
      if(previous&&!Object.hasOwn(value,'expected_timeline_version_id')&&!Object.hasOwn(value,'expected_timeline_sequence')){
        const original=previous.submitted_input;
        if(!original||!Object.hasOwn(original,'expected_timeline_version_id')||!Number.isSafeInteger(original.expected_timeline_sequence))
          throw new CliError('原调用版本不可核验，不换绑当前任务',4,{request_locator:locator});
        body={...value,expected_timeline_version_id:original.expected_timeline_version_id,expected_timeline_sequence:original.expected_timeline_sequence};
      }else body=await version(api,task,value);
      return submit(api,`${taskPath(task)}/media-script-tools/${id(object)}/preview`,{...body,request_id:requestId},locator,'script');
    }
  }
  if (group === 'script') {
    if (command === 'capabilities') {
      check(3,[]);
      return {data:await api(`${taskPath(task)}/assistant/media-script-capabilities`)};
    }
    if (command === 'sources') {
      check(3,[]);
      return {data:await api(`${taskPath(task)}/graphics/sources`)};
    }
    if (command === 'submit') {
      check(3,['file','request-id']);
      if (!f.file) throw new CliError('程序提交需要 --file JSON');
      const requestId = uuid(f['request-id']), locator = requestPath(task,requestId,'script');
      let source, file;
      try {
        file = await open(f.file,constants.O_RDONLY | constants.O_NONBLOCK);
        const stat = await file.stat();
        if (!stat.isFile() || stat.size > 262144) throw new CliError('程序文件须是有界普通JSON文件');
        source = await boundedJson(file,262144);
      } catch { throw new CliError('程序文件须是有界完整JSON'); }
      finally { await file?.close(); }
      if (!source || Array.isArray(source) || typeof source !== 'object' || typeof source.script_source !== 'string'
          || ['assistant_turn_id','request_id','workspace_id','member_id','task_id','runtime_paths'].some(key => Object.hasOwn(source,key)))
        throw new CliError('程序文件只包含API声明的代码、参数、输入/输出和期望版本，不包含身份或运行时路径');
      const previous = await receipt(api,locator);
      if (previous?.status === 'unknown') return {...knownReceipt(previous,locator),submitted_input:source};
      if (previous && previous.status !== 'recorded') throw new CliError('程序请求回执无法核验',4,{request_locator:locator});
      let body;
      if (previous && !Object.hasOwn(source,'expected_timeline_version_id') && !Object.hasOwn(source,'expected_timeline_sequence')) {
        const original = previous.submitted_input;
        if (!original || !Object.hasOwn(original,'expected_timeline_version_id') || !Number.isSafeInteger(original.expected_timeline_sequence))
          throw new CliError('原程序提交版本不可核验，不能换绑新版本',4,{request_locator:locator});
        body = {...source,expected_timeline_version_id:original.expected_timeline_version_id,
          expected_timeline_sequence:original.expected_timeline_sequence};
      } else body = await version(api,task,source);
      return submit(api,`${taskPath(task)}/media-scripts/preview`,{...body,request_id:requestId},locator,'script');
    }
  }
  throw new CliError('命令未实现或参数不匹配，请查看 --help');
}
