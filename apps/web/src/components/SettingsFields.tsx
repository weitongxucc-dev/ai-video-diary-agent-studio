"use client";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { useState } from "react";
import styles from "./SettingsFields.module.css";
import { DirectoryPickerButton } from "./DirectoryPickerButton";

export type FieldDef = {
  name: string;
  label: string;
  helper?: string;
  kind: "text" | "secret" | "directory" | "file" | "textarea" | "select";
  options?: { value: string; label: string }[];
  defaultValue?: string;
  placeholder?: string;
  configured?: boolean;
};

function statusTone(configured: boolean): "green" | "red" {
  return configured ? "green" : "red";
}

/**
 * 密钥类字段的徽章只说明「这一格填了没有」——这些服务（腾讯云、Product Hunt）
 * 没有服务状态线，任何「已配置」都只等于字段非空。这里不说「可用」。
 */
function statusLabel(configured: boolean): string {
  return configured ? "已填写" : "未填写";
}

export function FieldRow({ field }: { field: FieldDef }) {
  if (field.kind === "select") {
    return <label className={`settings-field ${styles.row}`}><span>{field.label}</span>
      <AppSelect aria-label={field.label} name={field.name} defaultValue={field.defaultValue}>
        {field.options?.map(option => <AppSelectOption key={option.value} value={option.value}>{option.label}</AppSelectOption>)}
      </AppSelect>
      {field.helper ? <details className={styles.help}><summary>说明</summary><p>{field.helper}</p></details> : null}
    </label>;
  }
  if (field.kind === "secret") {
    return (
      <label className={`settings-field ${styles.row}`}>
        <span>
          {field.label}
          <span
            className={`settings-secret-badge ${statusTone(Boolean(field.configured))}`}
          >
            {statusLabel(Boolean(field.configured))}
          </span>
        </span>
        <input
          aria-label={field.label}
          autoComplete="off"
          maxLength={field.name === "deepseek_api_key" ? 256 : 512}
          minLength={field.name === "deepseek_api_key" ? 20 : 4}
          name={field.name}
          pattern={
            field.name === "deepseek_api_key"
              ? "^sk-[A-Za-z0-9_-]{8,}$"
              : undefined
          }
          placeholder={field.placeholder}
          title={
            field.name === "deepseek_api_key"
              ? "DeepSeek API Key 必须以 sk- 开头，长度 ≥ 20"
              : `${field.label} 至少 4 字符`
          }
          type="password"
        />
        {field.helper ? (
          <small className="settings-field-helper">{field.helper}</small>
        ) : null}
      </label>
    );
  }

  if (field.kind === "directory" || field.kind === "file") {
    return (
      <label className={`settings-field ${styles.row}`}>
        <span>{field.label}</span>
        <div className="settings-field-row">
          <input
            aria-label={field.label}
            maxLength={512}
            name={field.name}
            placeholder={field.placeholder}
            title="支持 POSIX 与 Windows 绝对路径，留空保持现有值"
            type="text"
            defaultValue={field.defaultValue ?? ""}
          />
          <DirectoryPickerButton kind={field.kind} targetName={field.name} />
        </div>
        {field.helper ? (
          field.name === "database_path" ? <small className="settings-field-helper">{field.helper}</small> : <details className={styles.help}><summary>说明</summary><p>{field.helper}</p></details>
        ) : null}
      </label>
    );
  }

  if (field.kind === "textarea") {
    return (
      <label className={`settings-field wide ${styles.row}`}>
        <span>{field.label}</span>
        <textarea
          aria-label={field.label}
          maxLength={2000}
          name={field.name}
          rows={4}
          title="每行一个以 http(s):// 开头的 URL"
          defaultValue={field.defaultValue ?? ""}
        />
        {field.helper ? (
          <details className={styles.help}><summary>说明</summary><p>{field.helper}</p></details>
        ) : null}
      </label>
    );
  }

  return (
    <label className={`settings-field ${styles.row}`}>
      <span>{field.label}</span>
      <input
        aria-label={field.label}
        maxLength={512}
        minLength={1}
        name={field.name}
        placeholder={field.placeholder}
        title={`${field.label} 最多 512 字符`}
        type="text"
        defaultValue={field.defaultValue ?? ""}
      />
      {field.helper ? (
        <details className={styles.help}><summary>说明</summary><p>{field.helper}</p></details>
      ) : null}
    </label>
  );
}


export function AsrFields({ fields }: { fields: FieldDef[] }) {
  const providerField = fields.find(field => field.name === "asr_provider")!;
  const backendField = fields.find(field => field.name === "local_whisper_backend")!;
  const [provider, setProvider] = useState(providerField.defaultValue);
  const [backend, setBackend] = useState(backendField.defaultValue);
  const local = provider === "local_whisper";
  const cpp = backend === "whisper_cpp";
  return <>
    <label className={`settings-field ${styles.row}`}>
      <span>{providerField.label}</span>
      <AppSelect aria-label={providerField.label} name={providerField.name} value={provider} onValueChange={newValue => setProvider(newValue)}>
        {providerField.options?.map(option => <AppSelectOption key={option.value} value={option.value}>{option.label}</AppSelectOption>)}
      </AppSelect>
    </label>
    <fieldset className={`settings-grid ${styles.group}`} hidden={!local} style={!local ? { display: "none" } : undefined} disabled={!local}>
      <legend>本地听写</legend>
      <label className={`settings-field ${styles.row}`}>
        <span>{backendField.label}</span>
        <AppSelect aria-label={backendField.label} name={backendField.name} value={backend} onValueChange={newValue => setBackend(newValue)}>
          {backendField.options?.map(option => <AppSelectOption key={option.value} value={option.value}>{option.label}</AppSelectOption>)}
        </AppSelect>
      </label>
      <fieldset className={styles.group} aria-label="Python 听写运行环境" hidden={cpp} disabled={cpp}>
        {fields.filter(field => field.name === "local_whisper_python_path").map(field => <FieldRow field={field} key={field.name} />)}
      </fieldset>
      <fieldset className={styles.group} aria-label="原生听写运行环境" hidden={!cpp} disabled={!cpp}>
        {fields.filter(field => field.name === "local_whisper_cli_path").map(field => <FieldRow field={field} key={field.name} />)}
      </fieldset>
      {fields.filter(field => field.name === "local_whisper_model_path" || field.name === "local_whisper_language").map(field => <FieldRow field={field.name === "local_whisper_model_path" ? {
        ...field, helper: cpp ? "选择 whisper.cpp 使用的 GGML .bin 模型文件。" : "选择 Python Whisper 使用的 .pt 模型文件。",
        placeholder: cpp ? "/Volumes/External/models/ggml-medium.bin" : "/Volumes/External/models/medium.pt",
      } : field} key={field.name} />)}
    </fieldset>
    <fieldset className={`settings-grid ${styles.group}`} hidden={local} style={local ? { display: "none" } : undefined} disabled={local}>
      <legend>腾讯云听写</legend>
      <p>选择腾讯云后，听写时会把音频发送到腾讯云。服务商可能收取费用；失败时不会自动改用其他服务。</p>
      {fields.filter(field => field.name.startsWith("tencentcloud_")).map(field => <FieldRow field={field} key={field.name} />)}
    </fieldset>
  </>;
}
