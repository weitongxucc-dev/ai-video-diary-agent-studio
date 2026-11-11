"use client";
import { ChevronDown, ListChecks } from "lucide-react";
import type { CreationTaskWorkspace } from "@/lib/types";
import { TabsList, TabsTrigger } from "../ui/tabs";
import { Button } from "../ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../ui/dropdown-menu";
import { canOpenStage, productStages, productStageState, type WorkspaceView } from "./stage-navigation";
import styles from "../CreationTaskWorkspace.module.css";

export function TaskViewNavigation({ workspace, activeView, onOpen }: { workspace: CreationTaskWorkspace; activeView: WorkspaceView; onOpen: (view: WorkspaceView) => void }) {
  const views = [{ id: "montage", label: "剪辑" }, { id: "subtitle_cover", label: "字幕与封面" }, { id: "script", label: "文案" }] as const;
  return <div className={styles.viewNavigation}>
    <TabsList variant="line" aria-label="作品视图">{views.map(view => {
      const stage = productStages.find(stage => stage.id === view.id)!;
      const fact = productStageState(view.id, workspace, workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid");
      return <TabsTrigger key={view.id} value={view.id} onClick={() => { if (activeView === view.id) onOpen(view.id); }} title={fact.detail} data-stage-state={fact.state} aria-label={`${stage.title}：${fact.detail}`} disabled={!canOpenStage(view.id, workspace)}>{view.label}</TabsTrigger>;
    })}</TabsList>
    <DropdownMenu><DropdownMenuTrigger render={<Button variant="ghost" />}><ListChecks />制作进度<ChevronDown /></DropdownMenuTrigger>
      <DropdownMenuContent align="end" aria-label="制作进度与恢复">
        {productStages.map(stage => {
          const fact = productStageState(stage.id, workspace, workspace.subtitle_tracks.some(track => track.status === "valid") && workspace.cover_asset?.status === "valid");
          return <DropdownMenuItem key={stage.id} title={fact.detail} data-stage-state={fact.state} aria-label={`${stage.title}：${fact.detail}`} disabled={!canOpenStage(stage.id, workspace)} onClick={() => onOpen(stage.id)}>
            <span>{stage.title}{activeView === stage.id ? " · 当前视图" : ""}</span><small>{fact.detail}</small>
          </DropdownMenuItem>;
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  </div>;
}
