# CSS 组件化治理计划

## 背景

`apps/web/src/app/globals.css` 已超过 6300 行，页面、基础像素组件、工作台布局和表单样式混在一起。Windows 95 像素视觉已经稳定，下一步重点不是重做风格，而是降低继续改 UI 时的互相影响。

## 目标

1. 保留现有 Windows 95 像素视觉体系。
2. 把全局 CSS 拆成按职责命名的模块。
3. 每次迁移只做机械搬运和命名澄清，不改变页面 DOM 行为。
4. 每批迁移后跑 `npm run test:web-contract`、`npm run typecheck:web`、`npm run lint:web`，必要时用浏览器截图检查。

## 拆分顺序

1. `styles/tokens.css`
   - `:root` 变量、字体、基础 reset。
2. `styles/pixel-components.css`
   - `.pixel-card`、`.pixel-button`、`.pixel-icon-slot`、meter、badge、通用窗口/边框。
3. `styles/shell.css`
   - `RetroChrome`、顶部栏、左侧栏、底部语音命令栏。
4. `styles/workspace-layout.css`
   - `.workspace-*`、`.workbench-*`、通用左右栏布局。
5. `styles/forms.css`
   - `.settings-field`、表单网格、输入框、textarea、radio/checkbox。
6. 页面模块
   - `topics.css`
   - `script.css`
   - `cover.css`
   - `video.css`
   - `edit-plan.css`
   - `draft.css`
   - `publish-feedback.css`
   - `health-logs-settings.css`

## 第一批已完成

- 发布反馈组件样式从 `draft-*` 命名迁移为 `publish-feedback-*` / `publish-comment-*`。
- 草稿页不再承载发布反馈 UI；后续 `draft.css` 只保留草稿、素材包、剪映导入相关样式。

## 验收标准

- `globals.css` 只保留 `@import` 顺序和极少量兜底样式。
- 任一页面样式文件不超过 800 行。
- 页面专属类名不跨页面复用，公共类名只放在 `pixel-components.css` 或 `workspace-layout.css`。
- 移动端媒体查询按模块跟随迁移，避免集中堆在文件底部。
