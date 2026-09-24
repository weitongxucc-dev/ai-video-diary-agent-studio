"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, Clock3, PlayCircle, Video } from "lucide-react";
import { useEffect, useState } from "react";
import { HomeCardDialog } from "@/components/HomeCardDialog";
import { creationTaskStatusPresentation } from "@/lib/creation-task-status";
import type { CreationTask, ProductionFoundation } from "@/lib/types";

function displayTaskName(name: string) {
  return name
    .replace(/\bb-?roll\b/gi, "补充画面")
    .replace(/\bbatch\b/gi, "创作任务")
    .replace(/\bepisode\b/gi, "内容")
    .replace(/\brecipe\b/gi, "制作方案")
    .replace(/\bprovider\b/gi, "服务");
}

function readinessLabel(code?: string) {
  switch (code) {
    case "brand_profile_missing": return "品牌未设置";
    case "versioned_voice_authorization_missing": return "声音未授权";
    case "voice_reference_missing": return "声音缺资料";
    case "voice_model_not_ready": return "配音未备好";
    case "source_pool_empty": return "选题缺来源";
    case "approved_materials_empty": return "素材未选好";
    default: return "制作未备好";
  }
}

export function HomeWorkspace({ tasks, foundation }: { tasks: CreationTask[]; foundation: ProductionFoundation | null }) {
  const router = useRouter();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null;
  const attentionCount = tasks.filter((task) => task.stage_status === "waiting_for_user" || task.stage_status === "failed").length;
  const runningCount = tasks.filter((task) => task.stage_status === "running").length;

  useEffect(() => {
    const refreshVisible = () => { if (document.visibilityState === "visible") router.refresh(); };
    const timer = window.setInterval(refreshVisible, 10000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refreshVisible); };
  }, [router]);

  return <div className="home-workspace">
    <section aria-label="任务执行" className="home-execution-pane">
      <header className="home-pane-heading"><div><h2>我的任务</h2></div><Link href="/production">全部任务 <ArrowRight size={14} /></Link></header>
      <div className="home-execution-summary" aria-label="任务概况">
        <div><strong>{runningCount}</strong><span>进行中</span></div>
        <div><strong>{attentionCount}</strong><span>待处理</span></div>
        <div><strong>{tasks.length}</strong><span>全部</span></div>
      </div>
      {!foundation ? <div className="home-execution-note"><strong>暂时连不上</strong><Link href="/health">查看原因</Link></div>
        : !foundation.readiness.ready ? <div className="home-execution-note"><strong>{readinessLabel(foundation.readiness.blockers[0]?.code)}</strong><Link href="/health">查看原因</Link></div> : null}
      <section className="home-start-card"><Video size={28} /><h2>把随手拍的，变成好视频</h2><p>选好素材，告诉我这次想讲什么。</p><Link className="modern-primary-button" href="/library">选择素材</Link><div className="home-start-links"><Link href="/knowledge">内容资料</Link><Link href="/profiles">人物声音</Link><Link href="/templates">视频模板</Link></div></section>
      {tasks.length ? <div className="home-execution-list">
        {tasks.slice(0, 12).map((task) => {
          const state = creationTaskStatusPresentation(task.stage_status, task.stage);
          return <button className="home-execution-task" key={task.id} onClick={() => setSelectedTaskId(task.id)} type="button">
            <span className={`home-execution-task__icon status-${state.tone}`}><PlayCircle size={19} /></span>
            <span className="home-execution-task__body"><strong>{displayTaskName(task.name)}</strong><small>{state.stageLabel}</small><span aria-label={`进度 ${task.progress}%`} className="home-execution-task__progress"><i style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }} /></span></span>
            <span className={`home-execution-task__state status-${state.tone}`}>{state.label}</span>
            <ArrowRight size={15} />
          </button>;
        })}
      </div> : <div className="home-execution-empty"><Video size={25} /><strong>还没有任务</strong><p>在左边说说想做什么。</p></div>}
    </section>

    {selectedTask ? <HomeCardDialog onClose={() => setSelectedTaskId(null)} title="任务卡片">
      <div className="home-task-card">
        <h3>{displayTaskName(selectedTask.name)}</h3>
        <p>{creationTaskStatusPresentation(selectedTask.stage_status, selectedTask.stage).stageLabel}</p>
        <div className="home-task-card__progress"><span>进度</span><strong>{selectedTask.progress}%</strong><i><b style={{ width: `${Math.max(0, Math.min(100, selectedTask.progress))}%` }} /></i></div>
        {selectedTask.stage_reason ? <div className="home-task-card__detail"><strong>需要处理</strong><p>{selectedTask.stage_reason}</p>{selectedTask.stage_impact ? <p>{selectedTask.stage_impact}</p> : null}{selectedTask.stage_repair_action ? <p>{selectedTask.stage_repair_action}</p> : null}</div> : null}
        <div className="home-task-card__detail"><Clock3 size={16} /><span>{new Date(selectedTask.updated_at).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</span></div>
        <div className="home-task-card__actions"><button className="home-task-card__open" onClick={() => { router.push(`/tasks/${encodeURIComponent(selectedTask.id)}`); }} type="button">对话修改</button><Link href={`/tasks/${encodeURIComponent(selectedTask.id)}`}>打开任务 <ArrowRight size={15} /></Link></div>
      </div>
    </HomeCardDialog> : null}
  </div>;
}
