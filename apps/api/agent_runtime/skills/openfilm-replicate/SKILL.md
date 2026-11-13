---
name: openfilm-replicate
description: 受控适配 OpenFilm replicate 方法：按参考视频的镜头、节奏、构图和变化建立可编辑的复刻提案。
---

# OpenFilm replicate（曜核受控适配）

这是对 `openfilm/skill-replicate` 的方法适配。它借鉴“先测量参考，再对齐镜头和变化，最后用 compare/look 回验”的工作方式，不导入 OpenFilm 运行时、外部脚本或服务。

## 触发与边界

用户提供参考视频并要求复刻、做同款、照着做、保持相同节奏或视觉结构时使用。参考视频的内容、声音、人物和品牌必须按用户授权处理；曜核只把参考分析转成当前任务可审查的编辑提案，不把参考片直接复制进成片。

## 受控流程

1. 用 `read_current_task`、`read_task_object` 和 `read_task_edit_capabilities` 核对当前任务、时间线版本和允许的编辑类型。
2. 用 `read_material_evidence` 读取参考片和工作区候选素材的真实时间证据；用 `read_task_observations`、`observe_task_output` 回读已有输出观察。缺少镜头、声音或授权证据时保留未知。
3. 形成按时间排序的镜头对齐提案，记录参考区间、目标区间、保留/替换内容、构图/节奏依据和未匹配项。内容替换必须使用当前工作区已授权素材，不能把参考片里的品牌、人物或音频偷偷带入。
4. 先用 `preview_typed_edit` 生成可审查预览；预览只说明提案，不等于采用。
5. 用户明确授权后才用 `apply_typed_edit` 写入局部编辑；沿现有 `EditProposal/TimelineVersion`、任务版本和撤销入口执行，未知回执先回读原请求。
6. 交付时分别说明镜头对齐、变化节奏、画面构图、音频和输出观察的覆盖范围；`compare` 或相似度指标不能冒充完整视听验收。

## 停止条件

没有参考视频、真实时间证据、任务版本、编辑能力或明确授权时停在读取/提案阶段；不运行外部 `openfilm` 命令、不下载媒体、不调用云服务，也不直接写入时间线。
