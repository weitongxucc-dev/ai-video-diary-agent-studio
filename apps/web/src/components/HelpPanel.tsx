"use client";
import { HomeCardDialog } from "./HomeCardDialog";

import { CircleHelp, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import {
  closeHelpPanel,
  isHelpPanelOpen,
  subscribeHelpPanel,
  toggleHelpPanel,
} from "@/lib/helpPanel";

type HelpContent = {
  title: string;
  intro: string;
  steps: string[];
};

const HELP_BY_PATH: Array<{ match: (path: string) => boolean; content: HelpContent }> = [
  {
    match: (path) => path === "/" || path === "",
    content: {
      title: "从一个制作目标开始",
      intro: "首页只负责检查条件并创建任务，不会在你确认前直接运行模型。",
      steps: ["写下想制作的内容", "检查资料、素材、声音和模型", "确认方案后进入创作任务"],
    },
  },
  {
    match: (path) => path.startsWith("/library"),
    content: {
      title: "管理可进入成片的素材",
      intro: "素材必须经过用途、版权和品牌安全审核，才能被任务自动选用。",
      steps: ["扫描素材目录或搜索文件", "在右侧预览识别片段", "批准用途、排除素材或补充自己的标签"],
    },
  },
  {
    match: (path) => path.startsWith("/knowledge") || path.startsWith("/sources"),
    content: {
      title: "区分已有内容与外部信息",
      intro: "知识库保存你已经拥有的内容；信息源负责从外部获取候选内容。",
      steps: ["在知识库搜索或添加已有内容", "在信息源配置连接和获取规则", "确认授权后再保存到知识库或素材库"],
    },
  },
  {
    match: (path) => path.startsWith("/profiles"),
    content: {
      title: "建立可执行的人物与声音版本",
      intro: "人物、声音和授权分别版本化；缺少真实模型或授权时不会生成占位结果。",
      steps: ["从已审核素材创建人物或导入声音 WAV", "登记参考文字与授权证据", "确认模型状态后生成配音或口型预览"],
    },
  },
  {
    match: (path) => path.startsWith("/templates"),
    content: {
      title: "用成熟结构开始任务",
      intro: "模板定义内容结构和素材需要，不代表人物、声音或发布能力已经可用。",
      steps: ["选择一种内容结构", "在右侧检查时长、文案和素材要求", "用当前结构进入制作预检"],
    },
  },
  {
    match: (path) => path.startsWith("/tasks/"),
    content: {
      title: "按确认点推进一条成片",
      intro: "每个阶段只保留一个主要动作；生成结果先检查，再由你确认推进。",
      steps: ["确认选题和文案", "生成授权配音并检查故事板", "渲染、播放并通过原生对话框保存成片"],
    },
  },
  {
    match: (path) => path.startsWith("/production"),
    content: {
      title: "查看并行创作任务",
      intro: "任务和自动任务是不同对象；尚未接入的自动能力会明确说明原因。",
      steps: ["创建或打开一项创作任务", "处理等待确认和失败阻断", "从最近确认点继续执行"],
    },
  },
  {
    match: (path) => path.startsWith("/history"),
    content: {
      title: "检查发布结果和数据",
      intro: "当前没有平台连接时，只显示真实的本地创作记录，不模拟发布成功。",
      steps: ["按状态筛选记录", "检查失败原因或成片状态", "连接平台后再创建发布和监控任务"],
    },
  },
  {
    match: (path) => path.startsWith("/settings") || path.startsWith("/health"),
    content: {
      title: "修复运行条件",
      intro: "模型、目录、授权和本地服务都必须显示真实状态与可执行修复动作。",
      steps: ["定位需要处理的能力", "进入对应设置完成配置", "重新检查后回到原任务继续"],
    },
  },
];

const GLOBAL_HELP: HelpContent = {
  title: "AI 视频日记工作台",
  intro: "左侧选择模块和并行任务，中间完成当前工作，右侧只显示所选对象的参数、证据和阻断。",
  steps: ["⌘K 搜索页面和任务", "⌘N 开始制作新视频", "⌘1–7 切换主要模块"],
};

function resolveHelp(path: string): HelpContent {
  return HELP_BY_PATH.find((entry) => entry.match(path))?.content ?? GLOBAL_HELP;
}

function isEditable(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable);
}

export function HelpPanel() {
  const pathname = usePathname() ?? "";
  const [open, setOpen] = useState(isHelpPanelOpen());
  const help = useMemo(() => resolveHelp(pathname), [pathname]);

  useEffect(() => subscribeHelpPanel(setOpen), []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "?" && !isEditable(event.target) && !event.ctrlKey && !event.metaKey && !event.altKey) {
        event.preventDefault();
        toggleHelpPanel();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!open) return null;

  return (
    <HomeCardDialog kind="detail" title="应用帮助" onClose={closeHelpPanel}>
      <aside aria-label="应用帮助" className="help-panel-content" role="complementary">
        <header className="help-panel-head">
          <span className="help-panel-icon-frame" aria-hidden="true"><CircleHelp size={19} /></span>
          <div><h2>{help.title}</h2><p>{help.intro}</p></div>
          <button aria-label="关闭帮助" className="shell-icon-button" onClick={closeHelpPanel} type="button"><X size={16} /></button>
        </header>
        <ol className="help-panel-steps">
          {help.steps.map((step, index) => <li key={step}><span>{index + 1}</span><p>{step}</p></li>)}
        </ol>
        <footer><kbd>?</kbd> 打开帮助 <span>·</span> <kbd>Esc</kbd> 关闭</footer>
      </aside>
    </HomeCardDialog>
  );
}

export type { HelpContent };
