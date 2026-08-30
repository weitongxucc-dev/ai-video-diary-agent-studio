"use client";

import { ArrowRight, Mic, Paperclip, Sparkles } from "lucide-react";
import { type FormEvent, useState } from "react";

type HomeCommandComposerProps = {
  assignLocation?: (url: string) => void;
};

function assignDocumentLocation(url: string) {
  window.location.assign(url);
}

export function HomeCommandComposer({ assignLocation = assignDocumentLocation }: HomeCommandComposerProps) {
  const [goal, setGoal] = useState("");

  function continueToPreflight(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedGoal = goal.trim();
    if (!normalizedGoal) return;
    assignLocation(`/production?${new URLSearchParams({ new: "1", goal: normalizedGoal }).toString()}`);
  }

  return (
    <form className="home-composer" onSubmit={continueToPreflight}>
      <div className="home-composer__input-row">
        <Sparkles aria-hidden="true" size={18} />
        <textarea
          aria-label="制作指令"
          onChange={(event) => setGoal(event.target.value)}
          placeholder="想制作什么内容？例如：根据已审核素材生成一条关于 AI 工作流的 60 秒视频"
          rows={3}
          value={goal}
        />
      </div>

      <div className="home-composer__toolbar">
        <div className="home-composer__context">
          <button aria-label="添加素材" className="composer-icon-button" disabled type="button"><Paperclip size={16} /></button>
          <button aria-label="语音输入" className="composer-icon-button" disabled type="button"><Mic size={16} /></button>
          <span className="home-composer__availability">先检查当前可用的资料、素材、声音与模型，再由你确认创建任务</span>
        </div>
        <button
          aria-label="检查可用内容"
          className="composer-submit"
          disabled={!goal.trim()}
          type="submit"
        >
          <ArrowRight size={17} />
          <span>检查可用内容</span>
        </button>
      </div>

      <p className="home-composer__hint" role="status">{goal.trim() ? "将先进行只读检查，不会生成选题、文案或成片。" : "先写下制作目标，再检查当前工作区可用的内容。"}</p>
    </form>
  );
}
