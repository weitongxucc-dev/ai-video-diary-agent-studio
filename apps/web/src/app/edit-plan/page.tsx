import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { PixelIcon } from "@/components/PixelIcon";
import { PixelMascot } from "@/components/PixelMascot";

const timelineSegments = [
  {
    id: "seg-01",
    label: "开头",
    time: "00:00-00:07",
    action: "删除",
    tone: "cut",
    note: "寒暄和找状态，信息密度低。",
    width: "12%",
  },
  {
    id: "seg-02",
    label: "冲突",
    time: "00:07-00:28",
    action: "保留",
    tone: "keep",
    note: "提出客户想要锤子、自己却用螺丝刀的核心矛盾。",
    width: "24%",
  },
  {
    id: "seg-03",
    label: "案例",
    time: "00:28-00:52",
    action: "保留",
    tone: "keep",
    note: "真实项目经历，适合做主体口播。",
    width: "28%",
  },
  {
    id: "seg-04",
    label: "卡顿",
    time: "00:52-01:02",
    action: "复核",
    tone: "review",
    note: "疑似重复表达，等待人工确认是否删除。",
    width: "14%",
  },
  {
    id: "seg-05",
    label: "结尾",
    time: "01:02-01:20",
    action: "字幕",
    tone: "effect",
    note: "加重点字幕和封面标题候选。",
    width: "22%",
  },
];

const editDecisions = [
  {
    title: "删除开场停顿",
    command: "timeline_cut",
    range: "00:00-00:07",
    confidence: "92%",
    reason: "无关寒暄，进入主题较慢。",
  },
  {
    title: "保留核心冲突",
    command: "keep_segment",
    range: "00:07-00:28",
    confidence: "88%",
    reason: "有明确观点和用户痛点。",
  },
  {
    title: "复核疑似重录",
    command: "manual_review",
    range: "00:52-01:02",
    confidence: "64%",
    reason: "重复语义相近，但语气更自然。",
  },
  {
    title: "添加剪映效果",
    command: "jianying_effect",
    range: "01:02-01:20",
    confidence: "76%",
    reason: "结尾适合加重点字幕和轻微推近。",
  },
];

const previewMarkers = [
  { label: "开头", time: "00:00", memo: "原片起点" },
  { label: "冲突", time: "00:07", memo: "观点出现" },
  { label: "案例", time: "00:28", memo: "当前画面" },
  { label: "卡顿", time: "00:52", memo: "需试听" },
  { label: "结尾", time: "01:02", memo: "收束段" },
];

export default function EditPlanPage() {
  return (
    <AppShell>
      <div className="edit-plan-page">
        <section className="edit-plan-main pixel-card">
          <div className="edit-plan-header">
            <div>
              <span className="section-kicker">剪辑计划</span>
              <h1>自动剪辑可视化控制台</h1>
              <p>
                Agent 先把口播转写、重录检测、剪辑动作和剪映草稿导入计划摆出来，再等待人工确认。
              </p>
            </div>
            <Link className="pixel-button secondary" href="/video">
              返回视频处理
            </Link>
          </div>

          <div className="edit-plan-status-grid" aria-label="自动剪辑阶段">
            <div className="edit-plan-status active">
              <PixelIcon name="script" />
              <strong>1 转写对齐</strong>
              <span>字幕与语义片段已匹配</span>
            </div>
            <div className="edit-plan-status active">
              <PixelIcon name="clip" />
              <strong>2 重录检测</strong>
              <span>发现 1 个待复核片段</span>
            </div>
            <div className="edit-plan-status">
              <PixelIcon name="spark" />
              <strong>3 效果建议</strong>
              <span>字幕、推近、封面候选</span>
            </div>
            <div className="edit-plan-status">
              <PixelIcon name="publish" />
              <strong>4 草稿导入</strong>
              <span>确认后写入剪映草稿箱</span>
            </div>
          </div>

          <section className="video-preview-window" aria-label="视频浏览窗口">
            <div className="pixel-window-title">
              <strong>视频浏览窗口</strong>
              <span>原片预览 · 01:20 · 9:16</span>
            </div>
            <div className="video-preview-body">
              <div className="video-preview-screen" aria-label="当前视频画面">
                <div className="video-scanline" />
                <span className="preview-rec">PREVIEW</span>
                <span className="preview-timecode">00:28.4</span>
                <div className="preview-frame-card">
                  <PixelIcon name="video" />
                  <strong>当前帧：真实案例段</strong>
                  <p>用户在这里讲项目经历，Agent 建议保留为主体内容。</p>
                </div>
              </div>

              <div className="video-preview-inspector">
                <strong>当前片段</strong>
                <dl>
                  <div><dt>动作</dt><dd>保留</dd></div>
                  <div><dt>范围</dt><dd>00:28-00:52</dd></div>
                  <div><dt>来源</dt><dd>本地录制</dd></div>
                </dl>
                <div className="video-preview-controls" aria-label="视频浏览控制">
                  <button className="pixel-button secondary" type="button">上一段</button>
                  <button className="pixel-button" type="button">播放/暂停</button>
                  <button className="pixel-button secondary" type="button">下一段</button>
                </div>
              </div>
            </div>
            <div className="source-marker-strip" aria-label="原片标记">
              <div className="source-marker-title">
                <strong>原片标记</strong>
                <span>只用于浏览跳转，不代表剪辑动作</span>
              </div>
              <div className="source-marker-list">
                {previewMarkers.map((marker, index) => (
                  <button className="source-marker" key={marker.label} type="button">
                    <span>M{index + 1}</span>
                    <strong>{marker.time}</strong>
                    <em>{marker.label}</em>
                    <small>跳转 · {marker.memo}</small>
                  </button>
                ))}
              </div>
            </div>
          </section>

          <section className="pixel-window-panel" aria-label="剪辑计划时间轴">
            <div className="pixel-window-title">
              <strong>剪辑计划时间轴</strong>
              <span>VID_20260605_1031_preview.mp4</span>
            </div>
            <div className="edit-plan-timeline">
              {timelineSegments.map((segment) => (
                <div
                  className="timeline-segment"
                  key={segment.id}
                  style={{ flexBasis: segment.width }}
                >
                  <strong>{segment.label}</strong>
                  <span>{segment.time}</span>
                  <small>{segment.note}</small>
                  <em className={`timeline-action-chip ${segment.tone}`}>{segment.action}</em>
                </div>
              ))}
            </div>
            <div className="timeline-ruler">
              <span>00:00</span>
              <span>00:20</span>
              <span>00:40</span>
              <span>01:00</span>
              <span>01:20</span>
            </div>
          </section>

          <section className="edit-decision-grid" aria-label="剪辑动作清单">
            {editDecisions.map((decision) => (
              <article className="edit-decision-card" key={decision.title}>
                <div>
                  <PixelIcon
                    name={
                      decision.command === "timeline_cut"
                        ? "thumb-down"
                        : decision.command === "manual_review"
                          ? "note"
                          : "check"
                    }
                  />
                  <strong>{decision.title}</strong>
                </div>
                <dl>
                  <div>
                    <dt>动作</dt>
                    <dd>{decision.command}</dd>
                  </div>
                  <div>
                    <dt>范围</dt>
                    <dd>{decision.range}</dd>
                  </div>
                  <div>
                    <dt>置信度</dt>
                    <dd>{decision.confidence}</dd>
                  </div>
                </dl>
                <p>{decision.reason}</p>
              </article>
            ))}
          </section>
        </section>

        <aside className="edit-plan-side">
          <section className="pixel-card edit-agent-next">
            <div className="edit-agent-robot">
              <PixelMascot />
            </div>
            <div>
              <h2>Agent 下一步</h2>
              <p>等待你确认复核片段。确认后会生成 preview，并自动进入剪映草稿导入流程。</p>
            </div>
            <button className="pixel-button" type="button">
              确认剪辑计划
            </button>
          </section>

          <section className="pixel-card edit-side-panel">
            <h2>语音定位结果</h2>
            <dl>
              <div>
                <dt>原始指令</dt>
                <dd>把开头废话剪掉</dd>
              </div>
              <div>
                <dt>命中片段</dt>
                <dd>00:00-00:07</dd>
              </div>
              <div>
                <dt>解析动作</dt>
                <dd>timeline_cut</dd>
              </div>
              <div>
                <dt>风险等级</dt>
                <dd>低</dd>
              </div>
            </dl>
          </section>

          <section className="pixel-card edit-side-panel">
            <h2>剪映轨道预览</h2>
            <ul className="track-preview">
              <li><span>V1</span><strong>主视频</strong><em>4 段</em></li>
              <li><span>S1</span><strong>字幕</strong><em>自动生成</em></li>
              <li><span>E1</span><strong>效果</strong><em>轻微推近</em></li>
              <li><span>C1</span><strong>封面</strong><em>待确认</em></li>
            </ul>
          </section>
        </aside>
      </div>
    </AppShell>
  );
}
