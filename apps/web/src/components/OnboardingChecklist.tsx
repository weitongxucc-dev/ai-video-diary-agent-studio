"use client";

import { useCallback, useEffect, useState, useTransition } from "react";
import {
  getRuntimeHealth,
  getRuntimeSettings,
  getSetupPreflight,
  getSetupStatus,
  getSystemStatus,
} from "@/lib/api";
import { buildOnboardingSnapshot, summarizeOnboardingHealth } from "@/lib/onboarding";
import type {
  OnboardingChecklistEntry,
  OnboardingSnapshot,
} from "@/lib/onboarding";

type OnboardingChecklistProps = {
  initialSnapshot: OnboardingSnapshot;
};

type RefreshState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ok"; snapshot: OnboardingSnapshot; refreshedAt: string }
  | { kind: "error"; message: string };

function toneClass(state: OnboardingChecklistEntry["state"]): string {
  if (state === "ready") return "ready";
  if (state === "missing") return "missing";
  if (state === "attention") return "attention";
  return "unknown";
}

function describeError(err: unknown): string {
  if (err instanceof Error && err.message) {
    return err.message;
  }
  if (typeof err === "string") {
    return err;
  }
  return "请求失败";
}

function formatRefreshedAt(value: string): string {
  try {
    return new Date(value).toLocaleString("zh-CN", {
      hour12: false,
      timeZone: "Asia/Shanghai",
    });
  } catch {
    return value;
  }
}

export function OnboardingChecklist({ initialSnapshot }: OnboardingChecklistProps) {
  const [snapshot, setSnapshot] = useState<OnboardingSnapshot>(initialSnapshot);
  const [refresh, setRefresh] = useState<RefreshState>({ kind: "idle" });
  const [isPending, startTransition] = useTransition();

  const runCheck = useCallback(async () => {
    setRefresh({ kind: "loading" });
    try {
      const [preflightResult, setupResult, healthResult, settingsResult, statusResult] =
        await Promise.allSettled([
          getSetupPreflight(),
          getSetupStatus(),
          getRuntimeHealth(),
          getRuntimeSettings(),
          getSystemStatus(),
        ]);
      const next = buildOnboardingSnapshot({
        preflight:
          preflightResult.status === "fulfilled" ? preflightResult.value : null,
        preflightError:
          preflightResult.status === "rejected"
            ? describeError(preflightResult.reason)
            : null,
        setup: setupResult.status === "fulfilled" ? setupResult.value : null,
        setupError:
          setupResult.status === "rejected"
            ? describeError(setupResult.reason)
            : null,
        health: healthResult.status === "fulfilled" ? healthResult.value : null,
        healthError:
          healthResult.status === "rejected"
            ? describeError(healthResult.reason)
            : null,
        settings:
          settingsResult.status === "fulfilled" ? settingsResult.value : null,
        settingsError:
          settingsResult.status === "rejected"
            ? describeError(settingsResult.reason)
            : null,
        status: statusResult.status === "fulfilled" ? statusResult.value : null,
        statusError:
          statusResult.status === "rejected"
            ? describeError(statusResult.reason)
            : null,
      });
      const refreshedAt = new Date().toISOString();
      setSnapshot(next);
      setRefresh({ kind: "ok", snapshot: next, refreshedAt });
    } catch (err) {
      setRefresh({ kind: "error", message: describeError(err) });
    }
  }, []);

  useEffect(() => {
    if (refresh.kind === "ok" || refresh.kind === "error") {
      const timer = setTimeout(() => {
        setRefresh({ kind: "idle" });
      }, 4000);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [refresh]);

  const handleRefresh = useCallback(() => {
    startTransition(() => {
      void runCheck();
    });
  }, [runCheck, startTransition]);

  const summary = summarizeOnboardingHealth(snapshot);
  const lastChecked = refresh.kind === "ok" ? refresh.refreshedAt : null;

  return (
    <div className="onboarding-checklist" data-testid="onboarding-checklist">
      <div className="onboarding-checklist-actions">
        <button
          aria-busy={isPending || refresh.kind === "loading"}
          className="ui-button"
          disabled={isPending || refresh.kind === "loading"}
          onClick={handleRefresh}
          type="button"
        >
          {refresh.kind === "loading" || isPending ? "正在重新检查…" : "重新检查"}
        </button>
        {refresh.kind === "ok" ? (
          <span
            aria-live="polite"
            className={`onboarding-checklist-flash tone-${summary.tone}`}
            role="status"
          >
            已更新于 {lastChecked ? formatRefreshedAt(lastChecked) : ""}
          </span>
        ) : null}
        {refresh.kind === "error" ? (
          <span
            aria-live="assertive"
            className="onboarding-checklist-flash tone-missing"
            role="alert"
          >
            重新检查失败：{refresh.message}
          </span>
        ) : null}
        <span className="onboarding-checklist-hint">
          密钥只显示是否已配置，不会显示原文。
        </span>
      </div>

      {snapshot.sections.map((section) => (
        <section
          aria-labelledby={`onboarding-section-${section.id}`}
          className="onboarding-section"
          key={section.id}
        >
          <header className="onboarding-section-header">
            <h2 id={`onboarding-section-${section.id}`}>{section.title}</h2>
            <p>{section.description}</p>
          </header>
          <ul className="onboarding-entry-list">
            {section.entries.map((entry) => (
              <li
                className={`onboarding-entry tone-${toneClass(entry.state)}`}
                key={entry.id}
              >
                <div className="onboarding-entry-main">
                  <div className="onboarding-entry-title">
                    <span
                      aria-hidden="true"
                      className={`onboarding-entry-light tone-${toneClass(entry.state)}`}
                    />
                    <strong>{entry.title}</strong>
                    {entry.isSecret ? (
                      <span className="onboarding-entry-secret" title="仅显示状态，不展示值">
                        敏感信息
                      </span>
                    ) : null}
                  </div>
                  <p className="onboarding-entry-detail">{entry.detail}</p>
                  <p className="onboarding-entry-impact">{entry.impact}</p>
                </div>
                <div className="onboarding-entry-side">
                  <span
                    aria-label={`状态：${entry.statusLabel}`}
                    className={`onboarding-entry-state tone-${toneClass(entry.state)}`}
                  >
                    {entry.statusLabel}
                  </span>
                  {entry.fixHref.startsWith("/") ? (
                    <a className="ui-button secondary" href={entry.fixHref}>
                      {entry.fixLabel}
                    </a>
                  ) : (
                    <a
                      className="ui-button secondary"
                      href={entry.fixHref}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {entry.fixLabel}
                    </a>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
