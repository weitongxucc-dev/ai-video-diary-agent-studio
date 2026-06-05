import { PixelIcon } from "@/components/PixelIcon";

const menuItems = ["文件(F)", "编辑(E)", "视图(V)", "工具(T)", "帮助(H)"];

type RetroChromeProps = {
  todayLabel: string;
};

function WindowButton({ label, title }: { label: string; title: string }) {
  return (
    <button className="window-button" type="button" aria-label={title} disabled>
      {label}
    </button>
  );
}

export function RetroChrome({ todayLabel }: RetroChromeProps) {
  return (
    <>
      <header className="system-titlebar">
        <div className="system-title">
          <PixelIcon name="agent" />
          <strong>AI视频日记 Agent Studio</strong>
          <span>v1.0.0</span>
        </div>
        <div className="window-controls" aria-label="窗口控制">
          <WindowButton label="-" title="最小化" />
          <WindowButton label="□" title="最大化" />
          <WindowButton label="X" title="关闭" />
        </div>
      </header>

      <nav className="system-menubar" aria-label="系统菜单">
        {menuItems.map((item) => (
          <button key={item} type="button" disabled>
            {item}
          </button>
        ))}
      </nav>

      <div className="system-toolbar" aria-label="工作区工具栏">
        <div className="toolbar-navigation">
          <button className="toolbar-button" type="button" aria-label="返回" disabled>
            <span aria-hidden="true">←</span>
          </button>
          <button className="toolbar-button" type="button" aria-label="前进" disabled>
            <span aria-hidden="true">→</span>
          </button>
          <button className="toolbar-button" type="button" aria-label="主页" disabled>
            <span aria-hidden="true">⌂</span>
          </button>
        </div>

        <div className="address-box" aria-label="本地地址">
          <span>127.0.0.1:3000</span>
          <button type="button" aria-label="展开地址" disabled>▾</button>
        </div>

        <label className="toolbar-search">
          <PixelIcon name="search" />
          <input disabled placeholder="搜索 Episode、选题、素材..." />
        </label>

        <div className="toolbar-date">
          <PixelIcon name="calendar" />
          <span>{todayLabel}</span>
        </div>
      </div>
    </>
  );
}
