"use client";

import { usePathname } from "next/navigation";
import {
  useEffect,
  useMemo,
  useState,
} from "react";
import { PixelIcon, type PixelIconName } from "@/components/PixelIcon";
import {
  closeHelpPanel,
  isHelpPanelOpen,
  subscribeHelpPanel,
  toggleHelpPanel,
} from "@/lib/helpPanel";

type HelpItem = {
  title: string;
  description: string;
  hint?: string;
};

type HelpContent = {
  title: string;
  icon: PixelIconName;
  intro: string;
  items: HelpItem[];
  shortcuts: { keys: string[]; label: string }[];
};

const HELP_BY_PATH: { match: (path: string) => boolean; content: HelpContent }[] = [
  {
    match: (path) => path === "/" || path === "",
    content: {
      title: "今日工作台",
      icon: "desk",
      intro: "今天的总览 + 选题复核入口",
      items: [
        {
          title: "今日创作内容",
          description: "查看当前创作内容的进度和需要确认的事项",
        },
        {
          title: "今日队列",
          description: "4 步流程：复核选题 → 生成口播稿 → 视频处理 → 写入反馈",
        },
        {
          title: "运行面板",
          description: "模型服务 / Hermes / Obsidian 实时状态",
        },
      ],
      shortcuts: [
        { keys: ["Ctrl", "1"], label: "回到工作台" },
        { keys: ["Ctrl", "2"], label: "口播稿" },
        { keys: ["Ctrl", "3"], label: "视频处理" },
      ],
    },
  },
  {
    match: (path) => path.startsWith("/topics"),
    content: {
      title: "选题池",
      icon: "note",
      intro: "从素材生成、复核、打分到选中一个今日选题",
      items: [
        {
          title: "生成 / 重新生成",
          description: "点击按钮让系统读取知识库和外链素材，生成 3–5 个候选",
        },
        {
          title: "证据",
          description: "每个候选都标注来源类型、来源新鲜度（fresh/aging/stale）",
        },
        {
          title: "反馈",
          description: "赞/踩会进入下次 prompt 上下文并影响偏好记忆",
        },
      ],
      shortcuts: [
        { keys: ["Ctrl", "1"], label: "回工作台" },
        { keys: ["?"], label: "应用帮助" },
      ],
    },
  },
  {
    match: (path) => path.startsWith("/script"),
    content: {
      title: "口播稿",
      icon: "script",
      intro: "编辑、保存、重新生成整篇/段落、确认通过",
      items: [
        {
          title: "钩子 + 段落",
          description: "开头 3 秒 + 多段落结构，每段落独立标题/秒数/正文",
          hint: "至少 1 个标题候选 + 钩子和段落必填",
        },
        {
          title: "段落反馈",
          description: "赞/踩单段时不会影响整篇，方便精修",
        },
        {
          title: "版本历史",
          description: "支持恢复任意旧版本，便于回滚",
        },
      ],
      shortcuts: [
        { keys: ["Ctrl", "1"], label: "回工作台" },
        { keys: ["Ctrl", "2"], label: "本页" },
        { keys: ["Ctrl", "3"], label: "创作任务" },
      ],
    },
  },
  {
    match: (path) => path.startsWith("/draft"),
    content: {
      title: "剪映草稿",
      icon: "clip",
      intro: "草稿素材包管理 + 发布面板",
      items: [
        {
          title: "已生成 / 已导入",
          description: "显示所有素材包，区分已自动导入剪映与待导入",
        },
        {
          title: "发布面板",
          description: "内容草稿就绪后填写发布链接和数据，再标记为已发布",
          hint: "支持更新初始数据，二次提交仅更新数据不修改 URL/时间",
        },
      ],
      shortcuts: [
        { keys: ["Ctrl", "1"], label: "回工作台" },
        { keys: ["Ctrl", "5"], label: "本页" },
      ],
    },
  },
  {
    match: (path) => path.startsWith("/learning"),
    content: {
      title: "反馈学习",
      icon: "spark",
      intro: "查看偏好记忆卡 + 历史反馈事件",
      items: [
        {
          title: "偏好记忆卡",
          description: "每张卡代表一种偏好信号，可独立禁用 / 恢复",
        },
        {
          title: "证据",
          description: "点击卡片可查看每条赞/踩对应的创作内容和原因",
        },
        {
          title: "撤销",
          description: "最近反馈可单条撤销，撤销后会从偏好记忆移除",
        },
      ],
      shortcuts: [
        { keys: ["Ctrl", "1"], label: "回工作台" },
        { keys: ["Ctrl", "6"], label: "本页" },
      ],
    },
  },
  {
    match: (path) => path.startsWith("/templates"),
    content: {
      title: "内容模板",
      icon: "product",
      intro: "为创作内容选择结构（脚本骨架和剪辑样式）",
      items: [
        {
          title: "2.0 第一组",
          description: "video_diary / tool_review / tutorial_demo",
        },
        {
          title: "生效后",
          description: "影响后续选题生成、口播稿结构、剪映样式和素材需求",
        },
      ],
      shortcuts: [],
    },
  },
  {
    match: (path) => path.startsWith("/sources"),
    content: {
      title: "信息源",
      icon: "link",
      intro: "Hermes 雷达素材 + 手动链接",
      items: [
        {
          title: "同步状态灯",
          description: "绿 = 正常 / 黄 = 近期失败 / 红 = 同步失败 / 灰 = 未配置",
        },
        {
          title: "手动链接",
          description: "粘贴 Product Hunt / 观猹 / 任意文章 URL，自动识别",
        },
        {
          title: "重试同步",
          description: "点击重试按钮立刻触发该来源重新拉取",
        },
      ],
      shortcuts: [],
    },
  },
  {
    match: (path) => path.startsWith("/knowledge"),
    content: {
      title: "知识库",
      icon: "folder",
      intro: "Obsidian Vault 里的笔记列表",
      items: [
        {
          title: "搜索",
          description: "按标题关键词过滤，可看到 matching 的 wikilinks 与 tags",
        },
      ],
      shortcuts: [],
    },
  },
  {
    match: (path) => path.startsWith("/history"),
    content: {
      title: "发布与数据",
      icon: "calendar",
      intro: "按状态筛选并查看历史创作内容",
      items: [
        {
          title: "筛选",
          description: "全部 / 草稿就绪 / 已发布 / 失败",
        },
        {
          title: "点击进入",
          description: "跳转素材包 / 重录报告 / 发布链接",
        },
      ],
      shortcuts: [],
    },
  },
  {
    match: (path) => path.startsWith("/settings"),
    content: {
      title: "设置",
      icon: "settings",
      intro: "本地运行配置 + 密钥管理",
      items: [
        {
          title: "4 个分区",
          description: "AI 模型 / 目录 / ASR / 外部信息源",
        },
        {
          title: "密钥安全",
          description: "API Key 类字段显示已配置 / 未配置徽标，留空 = 保持原值",
        },
        {
          title: "目录选择器",
          description: "点「选择目录」用浏览器原生选择文件夹，名字自动填入",
        },
      ],
      shortcuts: [
        { keys: ["Ctrl", "7"], label: "本页" },
      ],
    },
  },
  {
    match: (path) => path.startsWith("/health"),
    content: {
      title: "运行健康",
      icon: "check",
      intro: "依赖检查 + 修复动作清单",
      items: [
        {
          title: "4 类状态",
          description: "ready / missing / degraded / invalid",
        },
        {
          title: "修复动作",
          description: "每项缺失都有明确修复指引，跳到对应设置页",
        },
      ],
      shortcuts: [],
    },
  },
  {
    match: (path) => path.startsWith("/welcome"),
    content: {
      title: "首次运行",
      icon: "agent",
      intro: "首次配置向导",
      items: [
        {
          title: "4 步",
          description: "欢迎 → 模型 → 目录 → 验证并完成",
        },
      ],
      shortcuts: [],
    },
  },
];

const GLOBAL_HELP: HelpContent = {
  title: "应用帮助",
  icon: "agent",
  intro: "全局功能说明与快捷键",
  items: [
    {
      title: "侧边栏工作流",
      description: "5 阶段进度：选题 → 脚本 → 录制 → 剪辑 → 导出，点击跳转",
    },
    {
      title: "Toast 通知",
      description: "所有操作通过右下角 Toast 反馈，3 秒自动消失",
    },
    {
      title: "错误边界",
      description: "每个核心页面有 error.tsx，路由级错误不会击穿整站",
    },
  ],
  shortcuts: [
    { keys: ["Ctrl", "1"], label: "工作台" },
    { keys: ["Ctrl", "2"], label: "口播稿" },
    { keys: ["Ctrl", "3"], label: "视频处理" },
    { keys: ["Ctrl", "4"], label: "剪辑计划" },
    { keys: ["Ctrl", "5"], label: "剪映草稿" },
    { keys: ["Ctrl", "6"], label: "反馈学习" },
    { keys: ["Ctrl", "7"], label: "设置" },
    { keys: ["Ctrl", "Shift", "H"], label: "切换帮助" },
    { keys: ["?"], label: "切换帮助" },
  ],
};

function resolveHelp(path: string): HelpContent {
  for (const entry of HELP_BY_PATH) {
    if (entry.match(path)) {
      return entry.content;
    }
  }
  return GLOBAL_HELP;
}

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  return target.isContentEditable;
}

const KEY_LABEL: Record<string, string> = {
  Ctrl: "Ctrl",
  Shift: "Shift",
  Alt: "Alt",
  Meta: "⌘",
  "?": "?",
};

function ShortcutKeys({ keys }: { keys: string[] }) {
  return (
    <span className="help-panel-keys" aria-label={keys.join(" + ")}>
      {keys.map((key, index) => (
        <span
          className="help-panel-keys-item"
          key={`${key}-${index}`}
        >
          {KEY_LABEL[key] ?? key}
        </span>
      ))}
    </span>
  );
}

export function HelpPanel() {
  const pathname = usePathname();
  const [open, setOpen] = useState<boolean>(isHelpPanelOpen());

  useEffect(() => {
    return subscribeHelpPanel(setOpen);
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (isEditable(event.target)) {
        return;
      }
      if (event.key !== "?") {
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      event.preventDefault();
      toggleHelpPanel();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const help = useMemo(() => resolveHelp(pathname ?? ""), [pathname]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeHelpPanel();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!open) {
    return null;
  }

  return (
    <aside
      aria-label="应用帮助"
      className="help-panel"
      role="complementary"
    >
      <button
        aria-label="关闭帮助"
        className="help-panel-backdrop"
        onClick={() => closeHelpPanel()}
        type="button"
      />
      <div className="help-panel-card ui-panel">
        <header className="help-panel-head">
          <div className="help-panel-icon-frame" aria-hidden="true">
            <PixelIcon name={help.icon} />
          </div>
          <div>
            <span className="section-kicker">应用帮助</span>
            <h2>{help.title}</h2>
            <p>{help.intro}</p>
          </div>
          <button
            aria-label="关闭"
            className="ui-button secondary"
            onClick={() => closeHelpPanel()}
            type="button"
          >
            关闭
          </button>
        </header>

        <section className="help-panel-section">
          <h3>功能说明</h3>
          <ul>
            {help.items.map((item, index) => (
              <li className="help-panel-item" key={`${help.title}-${index}`}>
                <strong>{item.title}</strong>
                <span>{item.description}</span>
                {item.hint ? (
                  <em>{item.hint}</em>
                ) : null}
              </li>
            ))}
          </ul>
        </section>

        {help.shortcuts.length > 0 ? (
          <section className="help-panel-section">
            <h3>本页快捷键</h3>
            <ul className="help-panel-shortcut-list">
              {help.shortcuts.map((entry, index) => (
                <li
                  className="help-panel-shortcut"
                  key={`${help.title}-shortcut-${index}`}
                >
                  <ShortcutKeys keys={entry.keys} />
                  <span>{entry.label}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </aside>
  );
}

export type { HelpContent, HelpItem };
