"use client";
import type { ReactNode } from "react";
import { Captions, Clapperboard, FileText, ListChecks } from "lucide-react";
import { creationTaskShortStatus, creationTaskStatusPresentation } from "@/lib/creation-task-status";
import type { CreationExecutionStatus, CreationTaskWorkspace } from "@/lib/types";
import { TabsList, TabsTrigger } from "../ui/tabs";
import { IconButton } from "../ui/icon-button";
import { Tooltip } from "../ui/tooltip";
import { useEditorDockHost } from "../workbench/EditorDock";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../ui/dropdown-menu";
import { canOpenStage, productStages, productStageState, type WorkspaceView } from "./stage-navigation";
import { executionStageLabels } from "./execution-labels";
import styles from "../CreationTaskWorkspace.module.css";

const languageLabels = { "zh-CN": "中文", "en-US": "英文", "zh-CN/en-US": "中英双语" };

function stagePresentation(task: CreationTaskWorkspace["task"], execution: CreationExecutionStatus | null) {
  const job = execution?.job;
  if (job?.status === "queued" || job?.status === "running") {
    const stage = execution?.current_stage ? executionStageLabels[execution.current_stage] : null;
    const waiting = job.status === "queued" && job.failure_reason === "waiting_for_user";
    return {
      text: `${stage ?? "连续制作"}，${waiting ? "待处理" : job.status === "queued" ? "已排队" : "正在处理"}`,
      tone: waiting ? "warning" : "running",
      canLocate: waiting,
    };
  }
  return {
    text: creationTaskShortStatus(task.stage_status, task.stage),
    tone: creationTaskStatusPresentation(task.stage_status, task.stage).tone,
    canLocate: task.stage_status === "failed" || task.stage_status === "waiting_for_user",
  };
}

export function TaskViewNavigation({ workspace, execution, activeView, onOpen, editingActions }: {
  editingActions?: ReactNode;
  workspace: CreationTaskWorkspace;
  execution?: CreationExecutionStatus | null;
  activeView: WorkspaceView;
  onOpen: (view: WorkspaceView) => void;
}) {
  const toolsRef = useEditorDockHost("tools");
  const views = [{ id: "montage", label: "剪辑", icon: Clapperboard }, { id: "subtitle_cover", label: "字幕与封面", icon: Captions }, { id: "script", label: "文案", icon: FileText }] as const;
  const { task, scripts, plan } = workspace;
  const confirmed = scripts.find(script => script.id === task.confirmed_script_id && script.selection_status === "confirmed");
  const draft = scripts.find(script => script.selection_status === "draft");
  const language = languageLabels[confirmed?.language ?? draft?.language ?? plan.language];
  const stage = stagePresentation(task, execution?.task_id === task.id ? execution : null);
  const subtitleCoverComplete = workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid";
  return <div className={styles.viewNavigation}>
    <TabsList variant="line" aria-label="作品视图">{views.map(view => {
      const stage = productStages.find(stage => stage.id === view.id)!;
      const fact = productStageState(view.id, workspace, subtitleCoverComplete);
      return <Tooltip key={view.id} label={`${stage.title} · ${fact.detail}`}><TabsTrigger value={view.id} onClick={() => { if (activeView === view.id) onOpen(view.id); }} data-stage-state={fact.state} aria-label={`${stage.title}：${fact.detail}`} disabled={!canOpenStage(view.id, workspace)}><view.icon size={15}/>{view.label}</TabsTrigger></Tooltip>;
    })}</TabsList>
    {editingActions}
    <div ref={toolsRef} className={styles.viewTools} />
    <div className={styles.viewProgress}>
      <div className={styles.viewFacts}>
        <span>{confirmed ? "确认稿" : draft ? "草稿" : "方案"}{language}</span>
        <span role="status" data-tone={stage.tone}>
          {stage.canLocate ? <button className={styles.locateStage} type="button" onClick={() => onOpen(task.stage)}>{stage.text}</button> : stage.text}
        </span>
      </div>
      <DropdownMenu><DropdownMenuTrigger render={<IconButton label="制作进度" />}><ListChecks /></DropdownMenuTrigger>
        <DropdownMenuContent align="end" aria-label="制作进度与恢复">
          {productStages.map(stage => {
            const fact = productStageState(stage.id, workspace, subtitleCoverComplete);
            return <DropdownMenuItem key={stage.id} data-stage-state={fact.state} aria-label={`${stage.title}：${fact.detail}`} disabled={!canOpenStage(stage.id, workspace)} onClick={() => onOpen(stage.id)}>
              <span>{stage.title}{activeView === stage.id ? " · 当前视图" : ""}</span><small>{fact.detail}</small>
            </DropdownMenuItem>;
          })}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  </div>;
}
