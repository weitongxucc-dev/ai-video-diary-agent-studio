export const HELP = `曜核 CLI — Node >=22，普通业务 API 消费者
  conversation create --file conversation.json
  conversation inspect CONVERSATION
  conversation draft CONVERSATION --file draft.json
  conversation turn CONVERSATION --file turn.json --request-id UUID [--task TASK] [--timeout-seconds 120]
  conversation request CONVERSATION UUID
  capabilities list [--task TASK]
  capabilities describe OPERATION --task TASK
  task inspect TASK
  material inspect ASSET
  material search --query TEXT [--role a_roll|b_roll] [--limit 20]
  tool capabilities|list
  tool describe TOOL [--version N]
  tool save TASK --file tool.json --request-id UUID
  tool request UUID
  tool update TOOL --file update.json
  tool revoke TOOL --expected-tool-version N
  tool preview TASK TOOL --file invocation.json --request-id UUID
  script capabilities TASK
  script sources TASK
  script submit TASK --file script.json --request-id UUID
  edit preview TASK --file edit.json --request-id UUID
  edit apply TASK PROPOSAL --request-id UUID
  execution preview|status TASK
  execution start TASK --file execution.json
  execution resume TASK
  execution wait TASK JOB [--timeout-seconds 30]
  output download TASK --file PATH
  run trace TURN [--task TASK]
  request query TASK UUID [--phase preview|apply|script]
  job inspect|wait|cancel TASK JOB [--timeout-seconds 30]
  timeline undo|redo TASK [--expected-version VERSION --expected-sequence N]
会话：先inspect取得草稿/引用版本，用draft显式保存文字，再turn提交相同文字和新草稿版本。turn文件含message、expected_draft_version、expected_reference_version；指定--task还需context_stage。只讨论/自主制作/定稿意图在message中表达，处理结果以业务回执为准。未知结果先conversation request查询原请求；不根据最新一轮或重建会话猜成功。create和draft不自动重试。
全局：--config FILE（默认 ~/.config/yaohe/config.json，POSIX mode 0600）
预览版本可写入 edit.json；缺少时只读一次当前任务绑定。
音频/画面动作也用 edit preview/apply，字段以 capabilities describe 的共同API schema为准：EditAudioTimeline提交audio_edit(action/target_id/values)，AdjustVisualEffects提交clip_ids/effects。values/effects只写非空指定patch，遗漏保持；音频淡入淡出毫秒、画面秒、gain_db绝对dB。支持音频增删、分割、复制、移动/裁切、锁定和局部音量关键点；target_id按action选择当前音轨或片段。solo持久化和独立音频变速未开放。采用保存草稿，不自动出片。
制作先读 execution preview；start 文件仅含实际 script_id、input_digest 和显式 requirements_confirmed:true。start/resume 只由明确命令提交；结果未知只回读制作状态，不重发。wait 最多300秒，始终绑定指定JOB，任务换作业不冒充原作业完成。
脚本仅消费API的Python/Pillow单PNG来源预览；不自动应用或出片。配方命令复用普通API；不继承旧任务授权，不自动应用。扩展加载和导出尚未接入。
退出：0 已完成；1 API/终态失败；2 参数/配置；3 尚未完成；4 结果未知。
`;
