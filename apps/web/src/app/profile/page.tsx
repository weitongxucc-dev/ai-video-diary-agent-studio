"use client";

import { useCallback, useEffect, useState } from "react";
import { Skeleton } from "@/components/Skeleton";
import {
  getCreatorProfile,
  updateCreatorProfile,
  learnCreatorProfile,
} from "@/lib/profile-api";
import type { CreatorProfile, CreatorProfileLearnResult } from "@/lib/types";

type PageState = "loading" | "ready" | "error";

export default function ProfilePage() {
  const [state, setState] = useState<PageState>("loading");
  const [profile, setProfile] = useState<CreatorProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{
    kind: "success" | "error";
    message: string;
  } | null>(null);

  // ── Inline editing state ──
  const [newExpression, setNewExpression] = useState("");
  const [newBannedWord, setNewBannedWord] = useState("");
  const [learning, setLearning] = useState(false);
  const [saving, setSaving] = useState(false);

  const loadProfile = useCallback(async () => {
    setState("loading");
    setError(null);
    try {
      const data = await getCreatorProfile();
      setProfile(data);
      setState("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法加载创作者画像");
      setState("error");
    }
  }, []);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const showToast = useCallback(
    (kind: "success" | "error", message: string) => {
      setToast({ kind, message });
      setTimeout(() => setToast(null), 3000);
    },
    [],
  );

  // ── Add / remove expressions ──
  const addExpression = useCallback(() => {
    const expr = newExpression.trim();
    if (!expr || !profile) return;
    if (profile.common_expressions.includes(expr)) {
      setNewExpression("");
      return;
    }
    setProfile({
      ...profile,
      common_expressions: [...profile.common_expressions, expr],
    });
    setNewExpression("");
  }, [newExpression, profile]);

  const removeExpression = useCallback(
    (expr: string) => {
      if (!profile) return;
      setProfile({
        ...profile,
        common_expressions: profile.common_expressions.filter(
          (e) => e !== expr,
        ),
      });
    },
    [profile],
  );

  // ── Add / remove banned words ──
  const addBannedWord = useCallback(() => {
    const word = newBannedWord.trim();
    if (!word || !profile) return;
    if (profile.banned_words.includes(word)) {
      setNewBannedWord("");
      return;
    }
    setProfile({
      ...profile,
      banned_words: [...profile.banned_words, word],
    });
    setNewBannedWord("");
  }, [newBannedWord, profile]);

  const removeBannedWord = useCallback(
    (word: string) => {
      if (!profile) return;
      setProfile({
        ...profile,
        banned_words: profile.banned_words.filter((w) => w !== word),
      });
    },
    [profile],
  );

  // ── Style preference changes ──
  const setPreference = useCallback(
    (key: string, value: string) => {
      if (!profile) return;
      setProfile({
        ...profile,
        style_preferences: { ...profile.style_preferences, [key]: value },
      });
    },
    [profile],
  );

  // ── Save ──
  const handleSave = useCallback(async () => {
    if (!profile) return;
    setSaving(true);
    try {
      const updated = await updateCreatorProfile({
        common_expressions: profile.common_expressions,
        banned_words: profile.banned_words,
        style_preferences: profile.style_preferences,
      });
      setProfile(updated);
      showToast("success", "画像已保存");
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "保存失败，请重试",
      );
    } finally {
      setSaving(false);
    }
  }, [profile, showToast]);

  // ── Learn ──
  const handleLearn = useCallback(async () => {
    setLearning(true);
    try {
      const result: CreatorProfileLearnResult =
        await learnCreatorProfile();
      setProfile(result.profile);
      const { summary } = result;
      const parts: string[] = [];
      if (
        Array.isArray(summary.new_expressions) &&
        summary.new_expressions.length > 0
      ) {
        parts.push(
          `新增 ${summary.new_expressions.length} 个表达方式`,
        );
      }
      if (
        Array.isArray(summary.new_banned_words) &&
        summary.new_banned_words.length > 0
      ) {
        parts.push(
          `新增 ${summary.new_banned_words.length} 个禁用词`,
        );
      }
      if (
        summary.style_changes &&
        Object.keys(summary.style_changes).length > 0
      ) {
        parts.push("风格偏好已更新");
      }
      showToast(
        "success",
        parts.length > 0 ? parts.join("，") : "未发现新的画像特征",
      );
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "学习失败，请重试",
      );
    } finally {
      setLearning(false);
    }
  }, [showToast]);

  // ── Render states ──

  if (state === "loading") {
    return (
      <div className="page profile-page" aria-busy="true">
        <header className="page-header">
          <h1>创作者画像</h1>
          <p className="page-subtitle">让 AI 学习你的表达风格</p>
        </header>
        <div className="profile-section">
          <Skeleton height="120px" />
          <Skeleton height="120px" />
          <Skeleton height="100px" />
        </div>
      </div>
    );
  }

  if (state === "error" || !profile) {
    return (
      <div className="page profile-page">
        <header className="page-header">
          <h1>创作者画像</h1>
        </header>
        <div className="pixel-form-error" role="alert">
          <span aria-hidden="true" className="pixel-form-error-light" />
          <div className="pixel-form-error-body">
            <strong>加载失败</strong>
            <span>{error ?? "未知错误"}</span>
          </div>
          <button
            aria-label="重试"
            className="pixel-form-error-close"
            onClick={loadProfile}
            type="button"
          >
            ↻
          </button>
        </div>
      </div>
    );
  }

  const isEmpty =
    profile.common_expressions.length === 0 &&
    profile.banned_words.length === 0 &&
    Object.keys(profile.style_preferences).length === 0;

  return (
    <div className="page profile-page">
      <header className="page-header">
        <h1>创作者画像</h1>
        <p className="page-subtitle">让 AI 学习你的表达风格</p>
      </header>

      {toast ? (
        <div
          aria-live="polite"
          className={`toast toast-${toast.kind}`}
          role="status"
        >
          {toast.message}
        </div>
      ) : null}

      {isEmpty ? (
        <div className="profile-empty" role="status">
          <p>还没有任何画像数据。</p>
          <p>
            点击「从历史学习」让 AI 分析你已有的脚本和反馈，自动提取表达习惯；
            也可以手动添加常用表达方式和风格偏好。
          </p>
        </div>
      ) : null}

      {/* ── Common Expressions ── */}
      <section className="profile-section">
        <h2>
          <span aria-hidden="true">📝</span> 常用表达方式
        </h2>
        <div className="profile-tags">
          {profile.common_expressions.map((expr) => (
            <span key={expr} className="profile-tag">
              {expr}
              <button
                aria-label={`删除表达方式：${expr}`}
                className="profile-tag-remove"
                onClick={() => removeExpression(expr)}
                type="button"
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <div className="profile-add-row">
          <input
            aria-label="添加表达方式"
            className="profile-input"
            onChange={(e) => setNewExpression(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addExpression();
              }
            }}
            placeholder="例如：说白了、你品你细品"
            type="text"
            value={newExpression}
          />
          <button
            className="btn btn-sm"
            disabled={!newExpression.trim()}
            onClick={addExpression}
            type="button"
          >
            添加
          </button>
        </div>
      </section>

      {/* ── Banned Words ── */}
      <section className="profile-section">
        <h2>
          <span aria-hidden="true">🚫</span> 避免使用的词汇
        </h2>
        <div className="profile-tags">
          {profile.banned_words.map((word) => (
            <span key={word} className="profile-tag profile-tag-banned">
              {word}
              <button
                aria-label={`删除禁用词：${word}`}
                className="profile-tag-remove"
                onClick={() => removeBannedWord(word)}
                type="button"
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <div className="profile-add-row">
          <input
            aria-label="添加禁用词"
            className="profile-input"
            onChange={(e) => setNewBannedWord(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addBannedWord();
              }
            }}
            placeholder="例如：众所周知、毋庸置疑"
            type="text"
            value={newBannedWord}
          />
          <button
            className="btn btn-sm"
            disabled={!newBannedWord.trim()}
            onClick={addBannedWord}
            type="button"
          >
            添加
          </button>
        </div>
      </section>

      {/* ── Style Preferences ── */}
      <section className="profile-section">
        <h2>
          <span aria-hidden="true">⚙️</span> 风格偏好
        </h2>
        <div className="profile-preferences">
          <label className="profile-pref-row">
            <span>语气</span>
            <select
              className="profile-select"
              onChange={(e) => setPreference("tone", e.target.value)}
              value={profile.style_preferences.tone ?? ""}
            >
              <option value="">未设置</option>
              <option value="casual">随意口语</option>
              <option value="formal">正式书面</option>
              <option value="conversational">对话聊天</option>
            </select>
          </label>
          <label className="profile-pref-row">
            <span>句长</span>
            <select
              className="profile-select"
              onChange={(e) =>
                setPreference("sentence_length", e.target.value)
              }
              value={profile.style_preferences.sentence_length ?? ""}
            >
              <option value="">未设置</option>
              <option value="short">短句（15字以内）</option>
              <option value="medium">中句（15-30字）</option>
              <option value="long">长句（30字以上）</option>
            </select>
          </label>
          <label className="profile-pref-row">
            <span>开篇 hook</span>
            <select
              className="profile-select"
              onChange={(e) =>
                setPreference("hook_style", e.target.value)
              }
              value={profile.style_preferences.hook_style ?? ""}
            >
              <option value="">未设置</option>
              <option value="question">提问式</option>
              <option value="exclamation">感叹式</option>
              <option value="statement">陈述式</option>
            </select>
          </label>
        </div>
      </section>

      {/* ── Actions ── */}
      <footer className="profile-actions">
        <button
          className="btn"
          disabled={learning}
          onClick={handleLearn}
          type="button"
        >
          {learning ? "学习中..." : "🧠 从历史学习"}
        </button>
        <button
          className="btn btn-primary"
          disabled={saving}
          onClick={handleSave}
          type="button"
        >
          {saving ? "保存中..." : "💾 保存修改"}
        </button>
      </footer>
    </div>
  );
}
