"use client";

import { ArrowRight, Sparkles } from "lucide-react";
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
        <p className="home-composer__hint" role="status">{goal.trim() ? "下一步只检查可用内容，不会直接开始生成。" : "输入目标后，系统会先检查素材、声音和模型。"}</p>
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
    </form>
  );
}
