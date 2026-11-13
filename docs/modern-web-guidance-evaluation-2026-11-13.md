# Modern Web Guidance 评估（2026-11-13）

来源：[GoogleChrome/modern-web-guidance](https://github.com/GoogleChrome/modern-web-guidance)。官方项目将它定位为给 coding agent 使用的现代 Web API、CSS、性能、表单和可访问性指导，并提供 `search`、`retrieve` 和 `legacy-scan` 命令。

## 结论

适合安装到开发 Agent 环境，作为 Web UI、设置页、轨迹面板和 Asset Panel 的审查辅助；不加入 `apps/web` 的生产依赖，不让产品运行时依赖它，也不把它的扫描结果当成产品验收。

本轮使用 `npx --yes modern-web-guidance@latest legacy-scan apps/web` 做了只读扫描。扫描发现并已处理两类候选问题：

- 主题切换写入 `data-theme` 时同步 `<meta name="color-scheme">`，已在 `AppShell.applyTheme` 中补齐。
- 偏好页新增条目的 Enter 处理需要避开中文输入法组合态，已同时判断 `isComposing` 和 `keyCode === 229`。

## 使用边界

- 每次处理 Web 交互、可访问性、现代 CSS 或性能问题时，可在开发环境运行定向 `search`、`retrieve` 或 `legacy-scan`。
- 只采纳与当前浏览器支持矩阵、现有组件和设计规范相容的建议；不为使用新 API 而引入无关重构。
- 扫描只能发现候选风险。最终仍需通过类型检查、定向测试、桌面界面检查和真实设备检查分别确认。

## 本轮只读页面验收结果

独立页面验收线程完成了 `legacy-scan`、相关 Vitest 和定向 ESLint 检查：`legacy-scan` 为 0 条发现，相关测试 142/142 通过，ESLint 0 errors。未执行真实浏览器、中文输入法、屏幕阅读器或真实硬件媒体验证。

已吸收的 P2 修复：

- `AssistantConversationPanel` 的运行事件列表增加最大高度和独立滚动，避免长会话把整段轨迹无限撑开。
- `EditorResourceLibraryPanel` 的资源目录请求失败时显示明确错误和“重新读取资源目录”按钮，不再把网络/服务失败误显示为空目录。
- 清理轨迹面板中的未使用引用和自动聚焦 effect 的依赖表达，定向 ESLint 只剩资源图片的原生 `<img>` 性能提示。

保留待后续真实验证的候选：`role="log"` 的实时播报量、资源图片是否适合迁移到 `next/image`、WorkspaceMedia 在真实浏览器中的媒体切换行为。它们当前没有证据表明会阻断 1.5.2。

本轮额外运行 Web 全量 `pnpm test`：`1603/1614` 通过，11 个失败集中在首页异步状态、视频编辑器媒体预览和模板预检连续性，未落在本轮修改的 Agent 轨迹或资源目录组件；它们仍属于当前工作区的既有全局红灯，不能把全量测试称为通过。受影响组件的定向测试为 `145/145`，TypeScript 无错误。
