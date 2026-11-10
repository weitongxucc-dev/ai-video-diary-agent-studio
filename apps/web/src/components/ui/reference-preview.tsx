"use client";
import { useEffect, useState } from "react";
import { Film, Volume2 } from "lucide-react";
import { WorkspaceVideo } from "../workbench/WorkspaceMedia";

type PreviewState = "loading" | "video" | "image" | "audio" | "unavailable";
/** Native media decoding supplies the thumbnail; it never changes the reference. */
export function ReferencePreview({ assetId, mediaType }: { assetId:string; mediaType?:string }) {
  const initial:PreviewState = mediaType === "image" ? "image" : mediaType === "audio" ? "audio" : "loading";
  const [state,setState]=useState<PreviewState>(initial);
  useEffect(()=>{setState(initial);},[assetId,initial]);
  const src=`/api/production/materials/${encodeURIComponent(assetId)}/media`;
  return <span className="assistant-reference-thumbnail" aria-hidden title={state === "unavailable" ? "预览暂不可用" : undefined}>
    {state === "image" ? <img alt="" src={src} onError={()=>setState("unavailable")}/>
      : state === "audio" ? <Volume2 size={16}/>
        : state === "unavailable" ? <Film size={16}/>
          : <WorkspaceVideo muted playsInline preload="metadata" src={src} onLoadedMetadata={event=>setState(event.currentTarget.videoWidth>0?"video":"audio")} onError={()=>setState("image")}/>}
  </span>;
}
