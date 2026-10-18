"use client";

import { forwardRef, useCallback, useContext, useLayoutEffect, useRef, type AudioHTMLAttributes, type VideoHTMLAttributes, type Ref } from "react";
import { MediaVisibilityContext } from "../ui/media-visibility";
import { useWorkspacePage } from "./context";

function setRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === "function") ref(value);
  else if (ref) ref.current = value;
}
function useViewMedia<T extends HTMLMediaElement>(src: VideoHTMLAttributes<HTMLVideoElement>["src"], ref: Ref<T> | undefined, positionKey: string | null) {
  const page = useWorkspacePage();
  const visible = useContext(MediaVisibilityContext);
  const active = (page?.active ?? true) && visible;
  const key = typeof src === "string" && positionKey ? `media:${positionKey}:${src}` : null;
  const saved = key ? page?.viewState.get(key) as number | undefined : undefined;
  const node = useRef<T | null>(null);
  const position = useRef({ src, time: saved ?? 0 });
  const binding = useRef({ src, active });
  binding.current = { src, active };
  if (position.current.src !== src) position.current = { src, time: saved ?? 0 };
  const remember = (media: T) => {
    position.current.time = media.currentTime;
    if (key) page?.viewState.set(key, media.currentTime);
  };
  const attach = useCallback((next: T | null) => {
    const previous = node.current;
    if (previous && !next) { previous.pause(); previous.removeAttribute("src"); previous.load(); }
    node.current = next;
    // StrictMode detaches and reattaches the same DOM node without restoring
    // attributes removed by cleanup. Restore only this active binding's URL.
    if (next && binding.current.active && typeof binding.current.src === "string"
      && next.getAttribute("src") !== binding.current.src) next.setAttribute("src", binding.current.src);
    setRef(ref, next);
  }, [ref]);
  useLayoutEffect(() => {
    if (!active && node.current) { node.current.pause(); node.current.removeAttribute("src"); node.current.load(); }
  }, [active]);
  return { active, attach, position, remember };
}

/** Hidden pages retain a position, never a playing/decoding native media source. */
export const WorkspaceVideo = forwardRef<HTMLVideoElement, VideoHTMLAttributes<HTMLVideoElement>>(function WorkspaceVideo(props, ref) {
  const { active, attach, position, remember } = useViewMedia(props.src, ref, props["aria-hidden"] === true || props["aria-hidden"] === "true" ? null : props["aria-label"] ?? "video");
  return <video {...props} ref={attach} src={active ? props.src : undefined} preload={active ? props.preload : "none"}
    onLoadedMetadata={event => { if (active) { props.onLoadedMetadata?.(event); if (position.current.time) event.currentTarget.currentTime = position.current.time; } }}
    onLoadedData={event => { if (active) props.onLoadedData?.(event); }} onCanPlay={event => { if (active) props.onCanPlay?.(event); }}
    onTimeUpdate={event => { if (active) { remember(event.currentTarget); props.onTimeUpdate?.(event); } }}
    onSeeked={event => { if (active) { remember(event.currentTarget); props.onSeeked?.(event); } }}
    onPlay={event => { if (active) props.onPlay?.(event); else event.currentTarget.pause(); }}
    onPause={event => { if (active) { remember(event.currentTarget); props.onPause?.(event); } }}
    onEnded={event => { if (active) props.onEnded?.(event); }} onError={event => { if (active) props.onError?.(event); }} />;
});
export const WorkspaceAudio = forwardRef<HTMLAudioElement, AudioHTMLAttributes<HTMLAudioElement>>(function WorkspaceAudio(props, ref) {
  const { active, attach, position, remember } = useViewMedia(props.src, ref, props["aria-hidden"] === true || props["aria-hidden"] === "true" ? null : props["aria-label"] ?? "audio");
  return <audio {...props} ref={attach} src={active ? props.src : undefined} preload={active ? props.preload : "none"}
    onLoadedMetadata={event => { if (active) { props.onLoadedMetadata?.(event); if (position.current.time) event.currentTarget.currentTime = position.current.time; } }}
    onTimeUpdate={event => { if (active) { remember(event.currentTarget); props.onTimeUpdate?.(event); } }}
    onPlay={event => { if (active) props.onPlay?.(event); else event.currentTarget.pause(); }}
    onPause={event => { if (active) { remember(event.currentTarget); props.onPause?.(event); } }}
    onError={event => { if (active) props.onError?.(event); }} />;
});
