"use client";
import type { ReactNode } from "react";
import { Captions, Clapperboard, FileText, ListChecks } from "lucide-react";
import type { CreationTaskWorkspace } from "@/lib/types";
import { TabsList, TabsTrigger } from "../ui/tabs";
import { IconButton } from "../ui/icon-button";
import { Tooltip } from "../ui/tooltip";
import { useEditorDockHost } from "../workbench/EditorDock";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../ui/dropdown-menu";
import { canOpenStage, productStages, productStageState, type WorkspaceView } from "./stage-navigation";
import styles from "../CreationTaskWorkspace.module.css";

export function TaskViewNavigation({ workspace, activeView, onOpen, editingActions }: { editingActions?: ReactNode; workspace: CreationTaskWorkspace; activeView: WorkspaceView; onOpen: (view: WorkspaceView) => void }) {
  const toolsRef = useEditorDockHost("tools");
  const views = [{ id: "montage", label: "剪辑", icon: Clapperboard }, { id: "subtitle_cover", label: "字幕与封面", icon: Captions }, { id: "script", label: "文案", icon: FileText }] as const;
  return <div className={styles.viewNavigation}>
    <TabsList variant="line" aria-label="作品视图">{views.map(view => {
      const stage = productStages.find(stage => stage.id === view.id)!;
      const fact = productStageState(view.id, workspace, workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid");
      return <Tooltip key={view.id} label={`${stage.title} · ${fact.detail}`}><TabsTrigger value={view.id} onClick={() => { if (activeView === view.id) onOpen(view.id); }} data-stage-state={fact.state} aria-label={`${stage.title}：${fact.detail}`} disabled={!canOpenStage(view.id, workspace)}><view.icon size={15}/>{view.label}</TabsTrigger></Tooltip>;
    })}</TabsList>
    {editingActions}
    <div ref={toolsRef} className={styles.viewTools} />
    <DropdownMenu><DropdownMenuTrigger render={<IconButton label="制作进度" />}><ListChecks /></DropdownMenuTrigger>
      <DropdownMenuContent align="end" aria-label="制作进度与恢复">
        {productStages.map(stage => {
          const fact = productStageState(stage.id, workspace, workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid");
          return <DropdownMenuItem key={stage.id} data-stage-state={fact.state} aria-label={`${stage.title}：${fact.detail}`} disabled={!canOpenStage(stage.id, workspace)} onClick={() => onOpen(stage.id)}>
            <span>{stage.title}{activeView === stage.id ? " · 当前视图" : ""}</span><small>{fact.detail}</small>
          </DropdownMenuItem>;
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  </div>;
}
