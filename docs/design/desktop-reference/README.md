# 桌面参考与生产落点

[DESIGN.md](../../../DESIGN.md)是当前视觉与交互权威；产品能力和对象关系分别由[产品需求](../../product-requirements.md)与[领域模型](../../domain-model.md)拥有。本目录保存参考来源和组件对应关系，不重新定义字号、布局、业务状态或验收标准。

## 可携带的参考

`sources/` 保存用户提供的组件研究中、候选02实际适配过的八份源码文本，随仓库分发，离线可读；它们不是直接执行的依赖。精确路径、大小和 SHA-256 见 [source-manifest.json](source-manifest.json)。原件分别遵循 [shadcn MIT](licenses/shadcn-MIT.txt) 和 [Kokonut MIT](licenses/kokonutui-LICENSE.txt)。这里的文件是候选适配版本，不冒称未经改动的上游原件。

| 参考与借鉴目的 | 生产实现 | 必须保持的边界 |
| --- | --- | --- |
| [Button](sources/button.tsx.txt)：统一动作尺寸、状态和键盘语义 | [button.tsx](../../../apps/web/src/components/ui/button.tsx)、[共享控件样式](../../../apps/web/src/components/ui/workbench-controls.module.css) | 页面只安排位置，不覆盖公共状态；图标提供提示与可访问名称 |
| [InputGroup](sources/input-group.tsx.txt)：输入与附件共用单层边界 | [输入组件](../../../apps/web/src/components/ui/input-group.tsx)、[对话面板](../../../apps/web/src/components/AssistantConversationPanel.tsx) | 禁用发送不等于禁用输入，引用身份由真实会话拥有 |
| [Message](sources/message.tsx.txt)：连续阅读与轻量消息 | [消息折叠](../../../apps/web/src/components/ui/conversation-message.tsx)、[对话面板](../../../apps/web/src/components/AssistantConversationPanel.tsx) | 完成后突出回答，过程可查；不搬入演示回复和假进度 |
| [Citations](sources/ai-citations.tsx.txt)：可辨识引用与就近详情 | [引用选择](../../../apps/web/src/components/ui/reference-picker.tsx)、[素材使用定位](../../../apps/web/src/components/ui/material-usage.tsx) | 预览、引用、采用不同；保留真实对象、版本和精确范围 |
| [Tabs](sources/tabs.tsx.txt)：同级内容切换 | [工作台](../../../apps/web/src/components/workbench/Workbench.tsx) | 资源浏览不换任务对话；明确切作品恢复对应上下文 |
| [Dialog](sources/dialog.tsx.txt)、[Popover](sources/popover.tsx.txt)：焦点和覆盖层级 | [对话框](../../../apps/web/src/components/ui/overlay.tsx)、[浮层](../../../apps/web/src/components/ui/popover.tsx) | 长期精调用停靠面板，短操作用浮层，关闭不丢草稿 |
| [DiffReview](sources/ai-diff-review.tsx.txt)：修改范围与决定就近展示 | [编辑提案](../../../apps/web/src/components/creation-task/EditPreviewCard.tsx) | 采用、撤销和版本来自正式用例，不执行参考中的计时演示 |

设置表单的排布由 [SettingsForm.module.css](../../../apps/web/src/components/SettingsForm.module.css)统一，设置页只组织类别与内容。资源列表/网格由 [MaterialResults](../../../apps/web/src/components/material-library/MaterialResults.tsx)共同呈现；详情保留精确时间范围和业务限制。它们使用上述公共控件，不另造第二套组件运行时。

## 参考使用边界

开发从本目录的相关参考与真实生产实现开始，源码来源与本地适配由清单记录。

候选用于比较主工作面、对话、预览、时间线、动作层级和对象辨识，不用于限定产品能力。候选中的固定栏宽、演示任务、音视频截图、模拟回复与旧版过程顺序均不自动成为现行规则。用户最新要求和根设计规范优先；生产体验按 [DESIGN §6](../../../DESIGN.md#6-验收与候选边界)在同一实际构建验证。
