import json
from datetime import date

import httpx

from app.config import Settings
from app.hermes_radar import HermesRadarSourceClient
from app.schemas import SystemStatusResponse
from app.sources import ObsidianVault, TopicSourceItem
from app.topics import DeepSeekTopicGenerator, _external_topic_sources


SAMPLE_REPORT = """🤖 **AI 产品日报 · 2026-05-27**

---

**Product Hunt 今日热门**（共 2 条）

**1. SelectPrism**
🔗 https://www.producthunt.com/products/selectprism
📝 Agents that screen and interview so you can hire faster
🇨🇳 AI筛选面试，助你快速招聘。

**2. Runway Agent**
🔗 https://www.producthunt.com/products/runway-agent
📝 Generate edited, sound-designed videos via chat
🇨🇳 通过聊天生成剪辑并配好音效的视频。

---

**观猹 产品库精选**（共 2 条）

**1. iFlow CLI**
🔗 https://watcha.cn/products/iflow-cli
🇨🇳 iFlow CLI 是一款直接在终端中运行的 AI 助手，能够分析代码仓库、执行编程任务。

**2. VibePPT**
🔗 https://watcha.cn/products/vibeppt
🇨🇳 像聊天一样做 PPT，描述想法即可即时生成和修改。

---

*生成时间: 2026-05-27 09:01*
"""

INLINE_LINK_REPORT = """# AI 产品日报 · 2026-05-28

---

## Product Hunt 今日热门（共 2 条）

**1. [AccountyCat](https://www.producthunt.com/products/accountycat)**
📝 A focus companion that actually gets context
🇨🇳 真正能读取上下文的专注伴侣

**2. [Crew44](https://www.producthunt.com/products/crew44)**
📝 Turn coding agents into specialist teams
🇨🇳 把编程 Agent 变成专业分工的团队

---

## 观猹产品库精选

> 今日观猹 sitemap 抓取失败（watcha.cn 连接超时），暂缺。

---

*生成时间: 2026-05-28 23:20*
"""


def test_hermes_radar_client_parses_product_hunt_and_watcha_items(tmp_path):
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-05-27.md").write_text(SAMPLE_REPORT, encoding="utf-8")

    items = HermesRadarSourceClient(report_dir).fetch_items(report_date=date(2026, 5, 27))

    assert [item.source_type for item in items] == [
        "Product Hunt",
        "Product Hunt",
        "观猹",
        "观猹",
    ]
    assert items[0].title == "SelectPrism"
    assert items[0].ref == "https://www.producthunt.com/products/selectprism"
    assert "Agents that screen" in items[0].excerpt
    assert "AI筛选面试" in items[0].excerpt
    assert items[2].title == "iFlow CLI"
    assert items[2].ref == "https://watcha.cn/products/iflow-cli"
    assert "终端中运行的 AI 助手" in items[2].excerpt


def test_hermes_radar_client_parses_inline_markdown_link_items(tmp_path):
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-05-28.md").write_text(INLINE_LINK_REPORT, encoding="utf-8")

    items = HermesRadarSourceClient(report_dir).fetch_items(report_date=date(2026, 5, 28))

    assert [item.source_type for item in items] == ["Product Hunt", "Product Hunt"]
    assert items[0].title == "AccountyCat"
    assert items[0].ref == "https://www.producthunt.com/products/accountycat"
    assert "A focus companion" in items[0].excerpt
    assert "真正能读取上下文" in items[0].excerpt
    assert items[1].title == "Crew44"
    assert items[1].ref == "https://www.producthunt.com/products/crew44"


def test_hermes_radar_status_counts_latest_report_without_exposing_content(tmp_path):
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-05-27.md").write_text(SAMPLE_REPORT, encoding="utf-8")
    settings = Settings(hermes_radar_path=str(report_dir))

    status = SystemStatusResponse.model_validate(settings.public_status())

    assert status.hermes_radar.configured is True
    assert status.hermes_radar.exists is True
    assert status.hermes_radar.path == str(report_dir)
    assert status.hermes_radar.latest_report == "2026-05-27.md"
    assert status.hermes_radar.total_count == 4
    assert status.hermes_radar.product_hunt_count == 2
    assert status.hermes_radar.watcha_count == 2
    assert "AI筛选面试" not in status.model_dump_json()


def test_external_topic_sources_include_hermes_report_without_product_hunt_token(tmp_path):
    report_dir = tmp_path / "daily-reports"
    report_dir.mkdir()
    (report_dir / "2026-05-27.md").write_text(SAMPLE_REPORT, encoding="utf-8")
    settings = Settings(
        product_hunt_token=None,
        topic_source_urls=(),
        hermes_radar_path=str(report_dir),
    )

    sources = _external_topic_sources(settings)

    assert {source.source_type for source in sources} == {"Product Hunt", "观猹"}
    assert {source.title for source in sources} >= {"Runway Agent", "VibePPT"}


def test_deepseek_prompt_balances_obsidian_product_hunt_and_watcha_sources(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    for index in range(16):
        (vault / f"note-{index}.md").write_text(
            f"# Obsidian Note {index}\n\n本地知识库素材 {index}",
            encoding="utf-8",
        )
    product_hunt_sources = [
        TopicSourceItem(
            source_type="Product Hunt",
            title=f"PH Product {index}",
            ref=f"https://www.producthunt.com/products/ph-{index}",
            excerpt=f"Product Hunt 新品 {index}",
        )
        for index in range(20)
    ]
    watcha_sources = [
        TopicSourceItem(
            source_type="观猹",
            title=f"观猹产品 {index}",
            ref=f"https://watcha.cn/products/watcha-{index}",
            excerpt=f"观猹国内 AI 产品 {index}",
        )
        for index in range(20)
    ]
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "topics": [
                                        {
                                            "title": "观猹产品带来的国内 AI 应用观察",
                                            "core_viewpoint": "国内产品信号应该和本地知识库一起判断。",
                                            "reason": "来源包含观猹国内产品。",
                                            "audience": "AI 产品经理",
                                            "outline": ["国内信号", "知识库融合", "选题判断"],
                                            "score": 90,
                                            "source_refs": [
                                                {
                                                    "source_type": "观猹",
                                                    "title": "观猹产品 0",
                                                    "ref": "https://watcha.cn/products/watcha-0",
                                                }
                                            ],
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    DeepSeekTopicGenerator(
        Settings(deepseek_api_key="secret", obsidian_vault_path=str(vault)),
        ObsidianVault(vault),
        external_source_provider=lambda: product_hunt_sources + watcha_sources,
        transport=httpx.MockTransport(handler),
    ).generate(day_number=1)

    user_prompt = captured["payload"]["messages"][1]["content"]
    assert "Obsidian Note" in user_prompt
    assert "PH Product" in user_prompt
    assert "观猹产品" in user_prompt
