"use client";
import type { VisualTemplate } from "@/lib/types";
import { AppSelect, AppSelectOption } from "./AppSelect";
import styles from "./VisualTemplateEditor.module.css";

const controls = {text:"单行文字", multiline:"多行文字", number:"数字", color:"颜色", font:"字体"} as const;
export function TemplateParameterFields({value, onChange, disabled}: {
  value: VisualTemplate; onChange: (value:VisualTemplate) => void; disabled:boolean;
}) {
  const change = (key:string, text:string) => onChange({...value, values:{...value.values,[key]:text}});
  return <>
    <div className={styles.fields}>{value.bindings.map(binding => {
      const text = value.values[binding.key] ?? binding.default;
      const control = binding.control ?? "text";
      return <label key={binding.key} className={control === "multiline" ? styles.wideField : undefined}>{binding.label}
        {control === "multiline" || control === "text" && text.includes("\n") ? <textarea rows={3} maxLength={4000} disabled={disabled} value={text} onChange={event=>change(binding.key,event.target.value)}/>
          : <span className={styles.parameterInput}>
            {control === "color" ? <input aria-label={`选择${binding.label}颜色`} type="color" disabled={disabled} value={/^#[0-9a-f]{6}$/i.test(text) ? text : "#000000"} onChange={event=>change(binding.key,event.target.value)}/> : null}
            <input aria-label={binding.label} type={control === "number" ? "number" : "text"} step={control === "number" ? "any" : undefined} disabled={disabled} value={text} placeholder={control === "color" || control === "font" ? "沿用模板" : undefined} onChange={event=>change(binding.key,event.target.value)}/>
          </span>}
      </label>;
    })}</div>
    <details><summary>参数控件</summary><div className={styles.fields}>{value.bindings.map(binding=><label key={binding.key}>{binding.label} · 控件<AppSelect aria-label={`${binding.label}控件`} value={binding.control ?? "text"} disabled={disabled} onValueChange={control=>onChange({...value,bindings:value.bindings.map(item=>item.key===binding.key?{...item,control:control as keyof typeof controls}:item)})}>{Object.entries(controls).map(([key,label])=><AppSelectOption key={key} value={key}>{label}</AppSelectOption>)}</AppSelect></label>)}</div></details>
  </>;
}
