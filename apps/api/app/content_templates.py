from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentTemplate:
    template_id: str
    name: str
    scenario: str
    recommended_duration: str
    topic_structure: list[str]
    script_structure: list[str]
    material_requirements: list[str]
    jianying_style: str
    subtitle_style: str
    title_rules: list[str]
    cover_copy_rules: list[str]
    publish_copy_rules: list[str]
    default_feedback_dimensions: list[str]


CONTENT_TEMPLATES: tuple[ContentTemplate, ...] = (
    ContentTemplate(
        template_id="video_diary",
        name="视频日记",
        scenario="技术型个人 IP 的每日观察、真实经历和复盘表达。",
        recommended_duration="800-1000 字，约 4-6 分钟",
        topic_structure=["真实事件", "冲突问题", "个人判断", "行动建议"],
        script_structure=["开头 3 秒", "主要经历", "观点反思", "结尾行动建议"],
        material_requirements=["口播录制", "知识库笔记", "可选外链素材"],
        jianying_style="diary_minimal",
        subtitle_style="清晰白底字幕，突出关键判断句。",
        title_rules=["保留真实冲突", "避免空泛概念", "突出个人判断"],
        cover_copy_rules=["一句冲突标题", "一行辅助说明"],
        publish_copy_rules=["先讲真实经历", "再补充观点", "最后抛出讨论问题"],
        default_feedback_dimensions=["真实感", "开头速度", "观点清晰度"],
    ),
    ContentTemplate(
        template_id="tool_review",
        name="AI 工具测评",
        scenario="围绕一个 AI 产品、工具或自动化能力做实测判断。",
        recommended_duration="800-1000 字，约 4-6 分钟",
        topic_structure=["一句话结论", "工具是什么", "实测体验", "最大问题", "是否推荐"],
        script_structure=["结论先行", "工具介绍", "实测场景", "坑点", "推荐人群"],
        material_requirements=["口播录制", "产品链接", "截图或屏幕录制", "真实试用结论"],
        jianying_style="tool_review",
        subtitle_style="章节条 + 工具名高亮 + 结论句加重。",
        title_rules=["工具名必须出现", "体现结果或坑点", "避免纯介绍"],
        cover_copy_rules=["工具名", "一句价值判断", "推荐等级"],
        publish_copy_rules=["说明测试场景", "给出推荐边界", "邀请评论补充体验"],
        default_feedback_dimensions=["实测感", "坑点清晰度", "推荐判断"],
    ),
    ContentTemplate(
        template_id="tutorial_demo",
        name="教程演示",
        scenario="把一个工作流、操作步骤或自动化方法讲清楚。",
        recommended_duration="800-1000 字，约 4-6 分钟",
        topic_structure=["教程目标", "步骤拆分", "关键设置", "常见错误", "最终效果"],
        script_structure=["目标说明", "步骤 1", "步骤 2", "步骤 3", "错误提醒", "总结"],
        material_requirements=["屏幕录制", "口播补充", "步骤素材", "最终效果截图"],
        jianying_style="tutorial_demo",
        subtitle_style="步骤编号 + 关键操作提示 + 错误提醒贴纸。",
        title_rules=["说明能学会什么", "突出耗时或结果", "避免泛教程"],
        cover_copy_rules=["教程目标", "步骤数量", "最终效果"],
        publish_copy_rules=["列出步骤", "说明适用人群", "提示可复制的素材"],
        default_feedback_dimensions=["步骤清晰度", "节奏", "可复现性"],
    ),
)


def list_content_templates() -> list[ContentTemplate]:
    return [*CONTENT_TEMPLATES]


def get_content_template(template_id: str) -> ContentTemplate:
    for template in CONTENT_TEMPLATES:
        if template.template_id == template_id:
            return template
    raise KeyError("content template not found")
