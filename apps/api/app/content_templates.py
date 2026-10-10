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
        recommended_duration="30-90 秒",
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
        recommended_duration="60-90 秒",
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
        recommended_duration="60-90 秒",
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
    ContentTemplate(
        template_id="product_intro",
        name="产品介绍",
        scenario="个人创作者或小团队用已核实的资料介绍产品用途、差异与适用范围。",
        recommended_duration="30-90 秒",
        topic_structure=["目标用户", "使用问题", "产品价值", "使用条件", "下一步"],
        script_structure=["使用问题", "产品与用途", "关键能力与依据", "适用范围", "行动建议"],
        material_requirements=["产品名称与目标用户", "已核实的功能或参数资料", "产品实拍或演示素材", "使用限制与下一步入口"],
        jianying_style="tutorial_demo",
        subtitle_style="步骤编号 + 关键操作提示 + 错误提醒贴纸。",
        title_rules=["写明产品与用途", "只使用有资料依据的能力", "避免无法证实的效果承诺"],
        cover_copy_rules=["产品名称", "具体使用场景"],
        publish_copy_rules=["说明适用人群", "列出能力依据", "说明下一步"],
        default_feedback_dimensions=["用途清晰度", "事实准确度", "适用边界"],
    ),
    ContentTemplate(
        template_id="customer_faq",
        name="常见问题",
        scenario="围绕客户的一个真实问题，给出有出处的回答、操作步骤与适用边界。",
        recommended_duration="30-90 秒",
        topic_structure=["客户问题", "直接回答", "事实依据", "处理方式", "例外条件"],
        script_structure=["复述问题", "一句话回答", "依据与解释", "操作建议", "边界与补充"],
        material_requirements=["客户原问题", "已确认的答复与资料出处", "可展示的操作步骤", "例外或不适用情况"],
        jianying_style="tutorial_demo",
        subtitle_style="步骤编号 + 关键操作提示 + 错误提醒贴纸。",
        title_rules=["标题直接表达问题", "聚焦一个问题", "不编造客户原话"],
        cover_copy_rules=["一个明确问题", "一句简短答复"],
        publish_copy_rules=["先给回答", "附适用条件", "邀请补充具体问题"],
        default_feedback_dimensions=["回答直接性", "依据可追溯性", "操作清晰度"],
    ),
    ContentTemplate(
        template_id="customer_case",
        name="客户案例",
        scenario="小团队基于可公开的真实案例，讲清问题、实施过程、可核实结果与复用条件。",
        recommended_duration="60-90 秒",
        topic_structure=["客户背景", "原始问题", "实施方案", "实际结果", "适用条件"],
        script_structure=["背景与问题", "采取的行动", "过程与证据", "结果与限制", "复用条件"],
        material_requirements=["可公开的案例背景", "实施步骤与过程素材", "可核实结果及其出处", "公开范围与适用限制"],
        jianying_style="tool_review",
        subtitle_style="章节条 + 工具名高亮 + 结论句加重。",
        title_rules=["突出解决的问题", "仅引用可核实结果", "未经允许不公开客户身份"],
        cover_copy_rules=["案例场景", "一个有依据的结果"],
        publish_copy_rules=["说明实施过程", "标明结果条件", "说明可复用边界"],
        default_feedback_dimensions=["过程完整性", "结果可信度", "复用边界"],
    ),
)


def list_content_templates() -> list[ContentTemplate]:
    return [*CONTENT_TEMPLATES]


def get_content_template(template_id: str) -> ContentTemplate:
    for template in CONTENT_TEMPLATES:
        if template.template_id == template_id:
            return template
    raise KeyError("content template not found")
