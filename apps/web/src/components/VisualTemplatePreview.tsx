"use client";
import {useEffect, useRef, useState} from 'react';
import type {VisualTemplate} from '@/lib/types';
import {visualTemplateDocument} from '@/lib/video-template-api';
import styles from './VisualTemplateEditor.module.css';
import ui from './VideoEditor.module.css';

export function VisualTemplatePreview({definition}: {definition: VisualTemplate}) {
  const frame = useRef<HTMLIFrameElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const [scale,setScale]=useState(1);
  const [document, setDocument] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice,setNotice]=useState<string|null>(null);
  const [time, setTime] = useState(1);
  const [playing, setPlaying] = useState(false);
  useEffect(()=>{
    if(!viewport.current)return;
    const observer=new ResizeObserver(entries=>setScale(entries[0].contentRect.width/definition.width));
    observer.observe(viewport.current);return()=>observer.disconnect();
  },[document,definition.width]);
  useEffect(() => {
    let active = true; setDocument(''); setError(null);setNotice(null);
    void visualTemplateDocument(definition).then(value => {if (active) {setDocument(value.document??'');setNotice(value.reason??null);}})
      .catch(cause => {if (active) setError(String(cause.message));});
    return () => {active = false;};
  }, [definition]);
  useEffect(() => {
    const draw = () => frame.current?.contentWindow?.postMessage({kind: 'draw', ticket: time,
      t: time, bindings: definition.bindings, values: definition.values}, '*');
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return;
      if (event.data?.kind === 'loaded') draw();
      if (event.data?.kind === 'drawn' && typeof event.data.error==='string') {setError(event.data.error.slice(0,2000)); setPlaying(false);}
    };
    window.addEventListener('message', receive); draw();
    return () => window.removeEventListener('message', receive);
  }, [document, time, definition]);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => setTime(value => (value + .1) % definition.duration_seconds), 100);
    return () => clearInterval(timer);
  }, [playing, definition.duration_seconds]);
  return <section aria-label="视觉模板预览" className={styles.preview}>
    {document ? <div ref={viewport} className={`${ui.player} ${styles.viewport}`} style={{aspectRatio: `${definition.width}/${definition.height}`}}>
      <iframe ref={frame} title="视觉模板画面" sandbox="allow-scripts" referrerPolicy="no-referrer"
        srcDoc={document} width={definition.width} height={definition.height}
        style={{width: definition.width, height: definition.height, transform: `scale(${scale})`}}/>
    </div> : <p>{error || notice || '正在准备预览…'}</p>}
    <div className={styles.actions}><button className={ui.button} type="button" onClick={() => setPlaying(value => !value)} disabled={!document || !!error}>{playing ? '暂停' : '播放动画'}</button>
      <label>时刻 <input type="range" aria-label="模板预览时刻" min={0} max={definition.duration_seconds} step={1/30} value={time} onChange={e => {setPlaying(false); setTime(Number(e.target.value));}}/></label><output>{time.toFixed(2)} 秒</output></div>
    {error && document ? <p role="alert">{error} 源码草稿已保留，请修正后继续。</p> : null}
    <small>当前源码与参数的预览；作品位置、关键帧和画幅以制作结果为准。</small>
  </section>;
}
