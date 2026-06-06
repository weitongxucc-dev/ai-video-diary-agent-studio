import { PixelIcon } from "@/components/PixelIcon";

type VoiceCommandConsoleProps = {
  contextLabel?: string;
};

export function VoiceCommandConsole({ contextLabel = "全局工作区" }: VoiceCommandConsoleProps) {
  return (
    <section className="voice-command-console" aria-label="语音指令控制台">
      <div className="voice-command-status">
        <PixelIcon name="agent" />
        <div>
          <strong>COMMAND</strong>
          <span>语音指令 · {contextLabel}</span>
        </div>
      </div>

      <div className="voice-command-line">
        <span>&gt;</span>
        <p>按住说出剪辑指令</p>
      </div>

      <div className="voice-command-preview" aria-label="语音指令解析预览">
        <div>
          <span>转写</span>
          <strong>把开头废话剪掉，保留第二段真实案例</strong>
        </div>
        <div>
          <span>解析</span>
          <strong>timeline_cut</strong>
        </div>
        <div>
          <span>状态</span>
          <strong>等待确认</strong>
        </div>
      </div>

      <div className="voice-command-actions">
        <button className="pixel-button secondary" type="button">
          预览
        </button>
        <button className="pixel-button" type="button">
          执行
        </button>
        <button className="pixel-button secondary" type="button">
          取消
        </button>
      </div>
    </section>
  );
}
