"use client";

import { AppShell } from "@/components/AppShell";
import {
  PixelBlockedState,
  PixelButton,
  PixelEmptyState,
  PixelErrorState,
  PixelInput,
  PixelLoadingState,
  PixelPanel,
  PixelScrollableRegion,
  PixelStatus,
} from "@/components/PixelPrimitives";

const stages = [
  "信息摄入",
  "选题生成",
  "选题复核",
  "脚本撰写",
  "视频等待",
  "视频处理",
  "剪辑计划",
  "剪映草稿",
  "发布反馈",
];

export default function PhaseAPreviewPage() {
  return (
    <AppShell
      inspector={
        <div className="phase-a-preview__states">
          <PixelStatus kind="waiting_for_user" label="等待确认选题" />
          <p>当前对象：Episode #18</p>
          <p>影响：确认后将生成脚本，不会立即执行视频处理。</p>
          <PixelButton variant="secondary">查看选题依据</PixelButton>
        </div>
      }
      inspectorTitle="当前任务依据"
    >
      <div className="phase-a-preview">
        <PixelPanel>
          <div className="phase-a-preview__task">
            <div>
              <PixelStatus kind="waiting_for_user" label="需要你的决定" />
              <h1>确认今天要制作的选题</h1>
              <p>
                Agent 已整理候选和来源。确认后才会进入脚本生成，不会直接执行后续步骤。
              </p>
            </div>
            <PixelButton data-testid="phase-a-primary" variant="primary">
              确认选题
            </PixelButton>
          </div>
        </PixelPanel>

        <PixelPanel
          description="当前阶段完整可达；在紧凑桌面中使用明确的局部横向滚动。"
          title="生产阶段"
        >
          <PixelScrollableRegion label="生产阶段轨道" showControls>
            <div className="phase-a-preview__rail">
              {stages.map((stage, index) => (
                <div
                  className={`phase-a-preview__rail-item${index === 2 ? " is-current" : ""}`}
                  data-scroll-item={index === stages.length - 1 ? "last" : undefined}
                  key={stage}
                >
                  <strong>{stage}</strong>
                  <span>{index < 2 ? "已完成" : index === 2 ? "等待确认" : "待处理"}</span>
                </div>
              ))}
            </div>
          </PixelScrollableRegion>
        </PixelPanel>

        <PixelPanel
          description="标签保持可见，正文使用可读字体，主动作保持唯一。"
          title="输入与反馈"
        >
          <div className="phase-a-preview__form">
            <PixelInput
              hint="输入会先进入预览，不会直接执行。"
              label="补充要求"
              placeholder="例如：保留真实经历，控制在 60 秒内"
            />
            <PixelButton variant="secondary">保存要求</PixelButton>
          </div>
        </PixelPanel>

        <PixelPanel
          description="状态使用文字、形状与语义色；错误和阻断都提供恢复路径。"
          title="基础状态"
        >
          <div className="phase-a-preview__states">
            <PixelEmptyState
              action={{ kind: "button", label: "添加素材", onClick: () => undefined }}
              description="当前 Episode 还没有可处理的视频。"
              title="等待第一段素材"
            />
            <PixelLoadingState
              description="正在读取本地视频目录，完成后会自动刷新。"
              title="正在检查素材"
            />
            <PixelBlockedState
              action={{ kind: "button", label: "检查目录权限", onClick: () => undefined }}
              description="视频目录不可访问；已有草稿不会丢失。"
              title="目录权限阻断"
            />
            <PixelErrorState
              action={{ kind: "button", label: "重新检查", onClick: () => undefined }}
              description="处理服务未响应，本次操作尚未写入草稿。"
              title="视频处理失败"
            />
          </div>
        </PixelPanel>
      </div>
    </AppShell>
  );
}
