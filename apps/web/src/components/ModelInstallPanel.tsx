"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getModelInstallJob, installLocalModel } from "@/lib/api";
import type { LocalModelStatus, ModelInstallJob } from "@/lib/types";

type Props = {
  models: LocalModelStatus[];
  modelRootPath: string;
};

function humanSize(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
}

function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "安装失败";
  try {
    const payload = JSON.parse(error.message) as { detail?: string };
    return payload.detail ?? error.message;
  } catch {
    return error.message;
  }
}

export function ModelInstallPanel({ models, modelRootPath }: Props) {
  const router = useRouter();
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [jobs, setJobs] = useState<Record<string, ModelInstallJob>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    const active = Object.values(jobs).filter((job) =>
      ["queued", "running"].includes(job.status),
    );
    if (active.length === 0) return;
    const timer = window.setTimeout(async () => {
      const refreshed = await Promise.all(
        active.map(async (job) => {
          try {
            return await getModelInstallJob(job.id);
          } catch {
            return job;
          }
        }),
      );
      setJobs((current) => {
        const next = { ...current };
        for (const job of refreshed) next[job.model_id] = job;
        return next;
      });
      if (refreshed.some((job) => job.status === "ready")) router.refresh();
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [jobs, router]);

  async function start(model: LocalModelStatus) {
    setErrors((current) => ({ ...current, [model.model.id]: "" }));
    try {
      const job = await installLocalModel(model.model.id);
      setJobs((current) => ({ ...current, [model.model.id]: job }));
    } catch (error) {
      setErrors((current) => ({
        ...current,
        [model.model.id]: errorMessage(error),
      }));
    }
  }

  return (
    <fieldset className="settings-section purple" aria-labelledby="local-models-title">
      <legend className="settings-section-legend">
        <span className="settings-section-light purple" />
        <strong id="local-models-title">本地模型安装</strong>
      </legend>
      <p className="settings-section-description">
        先保存上方模型目录，再逐个确认许可证并安装。当前目录：
        <code>{modelRootPath || "未配置"}</code>
      </p>
      <div className="settings-grid">
        {models.map((model) => {
          const job = jobs[model.model.id];
          const active = job && ["queued", "running"].includes(job.status);
          const progress = Math.round((job?.progress ?? 0) * 100);
          return (
            <div className="pixel-card side-note" key={model.model.id}>
              <h2>{model.model.display_name}</h2>
              <p>{model.message}</p>
              <p>
                下载约 {humanSize(model.model.approximate_download_bytes)} · {model.model.license_name}
              </p>
              {job ? (
                <>
                  <div
                    aria-label={`${model.model.display_name} 安装进度`}
                    aria-valuemax={100}
                    aria-valuemin={0}
                    aria-valuenow={progress}
                    className="onboarding-progress"
                    role="progressbar"
                  >
                    <span
                      className="onboarding-progress-bar"
                      style={{ width: `${progress}%` }}
                    />
                    <em>{progress}%</em>
                  </div>
                  <small className="settings-field-helper">{job.message}</small>
                </>
              ) : null}
              {!model.ready ? (
                <div className="settings-field">
                  <label>
                    <input
                      checked={Boolean(accepted[model.model.id])}
                      disabled={Boolean(active)}
                      onChange={(event) =>
                        setAccepted((current) => ({
                          ...current,
                          [model.model.id]: event.target.checked,
                        }))
                      }
                      type="checkbox"
                    />{" "}
                    我已阅读并接受{" "}
                    <a href={model.model.license_url} rel="noreferrer" target="_blank">
                      {model.model.license_name} 许可证
                    </a>
                  </label>
                  <button
                    className="pixel-button secondary"
                    disabled={!accepted[model.model.id] || Boolean(active)}
                    onClick={() => void start(model)}
                    type="button"
                  >
                    {active ? "安装中…" : job?.status === "failed" ? "从断点重试" : "安装到指定目录"}
                  </button>
                </div>
              ) : null}
              {errors[model.model.id] ? (
                <p role="alert">{errors[model.model.id]}</p>
              ) : null}
            </div>
          );
        })}
      </div>
    </fieldset>
  );
}
