import json
import os

import httpx

from app.config import Settings
from app.external_sources import HtmlPageSourceClient
from app.sources import ObsidianVault, TopicSourceItem
from app.topics import DeepSeekTopicGenerator


def test_obsidian_vault_scans_markdown_notes_and_ignores_config(tmp_path):
    vault = tmp_path / "vault"
    notes_dir = vault / "AI工程"
    notes_dir.mkdir(parents=True)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "config.md").write_text("# 不应该被索引", encoding="utf-8")
    (notes_dir / "agent.md").write_text(
        "# Agentic Engineering\n\n流程即护城河，人在关键节点复核。",
        encoding="utf-8",
    )

    notes = ObsidianVault(vault).recent_notes(limit=10)

    assert len(notes) == 1
    assert notes[0].title == "Agentic Engineering"
    assert notes[0].ref == "AI工程/agent.md"
    assert "流程即护城河" in notes[0].excerpt


def test_obsidian_note_extracts_tags_wikilinks_and_outlinks(tmp_path):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    (vault_path / "agent.md").write_text(
        """---
tags:
  - AI产品
  - 工作流
---
# Agent 日记

今天从 [[剪映草稿自动化]] 想到 #视频日记 的流程。
参考 [Product Hunt](https://www.producthunt.com/) 和 https://watcha.cn/products/demo
""",
        encoding="utf-8",
    )

    note = ObsidianVault(vault_path).recent_notes()[0]

    assert note.tags == ["AI产品", "工作流", "视频日记"]
    assert note.wikilinks == ["剪映草稿自动化"]
    assert note.outlinks == [
        "https://www.producthunt.com/",
        "https://watcha.cn/products/demo",
    ]
    source = note.to_topic_source()
    assert "标签：AI产品、工作流、视频日记" in source.excerpt
    assert "双链：剪映草稿自动化" in source.excerpt


def test_public_status_tolerates_interrupted_obsidian_markdown_scan(
    tmp_path,
    monkeypatch,
):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    def interrupted_rglob(self, pattern):
        raise InterruptedError("scan interrupted")

    monkeypatch.setattr("app.config.Path.rglob", interrupted_rglob)

    status = Settings(
        obsidian_vault_path=str(vault_path),
        hermes_radar_path=None,
    ).public_status()

    assert status["obsidian"]["exists"] is True
    assert status["obsidian"]["markdown_count"] == 0


def test_public_status_does_not_count_obsidian_notes_during_status_check(
    tmp_path,
    monkeypatch,
):
    vault_path = tmp_path / "vault"
    vault_path.mkdir()
    for index in range(20):
        (vault_path / f"note-{index}.md").write_text("note", encoding="utf-8")

    def fail_rglob(self, pattern):
        raise AssertionError("system status should not scan Obsidian markdown files")

    monkeypatch.setattr("app.config.Path.rglob", fail_rglob)

    status = Settings(
        obsidian_vault_path=str(vault_path),
        hermes_radar_path=None,
    ).public_status()

    assert status["obsidian"]["exists"] is True
    assert status["obsidian"]["markdown_count"] == 0


def test_public_status_does_not_scan_video_inbox_for_pending_count(
    tmp_path,
    monkeypatch,
):
    video_inbox = tmp_path / "videos"
    video_inbox.mkdir()

    def fail_iterdir(self):
        raise AssertionError("system status should not scan video directories")

    monkeypatch.setattr("app.config.Path.iterdir", fail_iterdir)

    status = Settings(
        hermes_radar_path=None,
        video_inbox_path=str(video_inbox),
    ).public_status()

    assert status["video"]["inbox_exists"] is True
    assert status["video"]["pending_count"] == 0


def test_deepseek_topic_generator_uses_real_obsidian_notes_and_json_completion(tmp_path):
    vault = tmp_path / "vault"
    notes_dir = vault / "AI工程"
    notes_dir.mkdir(parents=True)
    (notes_dir / "agent.md").write_text(
        "# Agentic Engineering\n\nAI Agent 真正的护城河不是模型，而是工作流和人工复核。",
        encoding="utf-8",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
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
                                            "title": "AI Agent 真正的护城河不是模型",
                                            "core_viewpoint": "工作流和人工复核才是可积累资产。",
                                            "reason": "来自 Obsidian 笔记《Agentic Engineering》。",
                                            "audience": "AI 产品经理、创业者",
                                            "outline": ["模型趋同", "流程沉淀", "人工复核"],
                                            "score": 93,
                                            "source_refs": [
                                                {
                                                    "source_type": "Obsidian",
                                                    "title": "Agentic Engineering",
                                                    "ref": "AI工程/agent.md",
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

    generator = DeepSeekTopicGenerator(
        Settings(
            deepseek_api_key="secret",
            deepseek_model="deepseek-v4-flash",
            obsidian_vault_path=str(vault),
        ),
        ObsidianVault(vault),
        transport=httpx.MockTransport(handler),
    )

    topics = generator.generate(day_number=1)

    assert topics[0].title == "AI Agent 真正的护城河不是模型"
    assert topics[0].source_refs[0].source_type == "Obsidian"
    assert topics[0].source_refs[0].ref == "AI工程/agent.md"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["authorization"] == "Bearer secret"
    user_prompt = captured["payload"]["messages"][1]["content"]
    assert "AI工程/agent.md" in user_prompt
    assert "AI Agent 真正的护城河不是模型" in user_prompt


def test_deepseek_topic_generator_accepts_product_hunt_source_refs(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "agent.md").write_text(
        "# Agentic Engineering\n\n人工复核是 Agent 工作流稳定的关键。",
        encoding="utf-8",
    )
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
                                            "title": "Product Hunt 上的 Agent 产品给了我一个提醒",
                                            "core_viewpoint": "AI 产品不是越自动越好，越能嵌入工作流越有机会。",
                                            "reason": "结合 Product Hunt 新品和本地 Agent 笔记。",
                                            "audience": "AI 产品经理、创业者",
                                            "outline": ["新品信号", "工作流价值", "人工复核"],
                                            "score": 92,
                                            "source_refs": [
                                                {
                                                    "source_type": "Product Hunt",
                                                    "title": "AgentFlow",
                                                    "ref": "producthunt:post_1",
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

    generator = DeepSeekTopicGenerator(
        Settings(deepseek_api_key="secret", obsidian_vault_path=str(vault)),
        ObsidianVault(vault),
        external_source_provider=lambda: [
            TopicSourceItem(
                source_type="Product Hunt",
                title="AgentFlow",
                ref="producthunt:post_1",
                excerpt="Build reliable AI workflows",
            )
        ],
        transport=httpx.MockTransport(handler),
    )

    topics = generator.generate(day_number=1)

    assert topics[0].source_refs[0].source_type == "Product Hunt"
    assert topics[0].source_refs[0].ref == "producthunt:post_1"
    user_prompt = captured["payload"]["messages"][1]["content"]
    assert "source_items" in user_prompt
    assert "Product Hunt" in user_prompt
    assert "producthunt:post_1" in user_prompt
    assert "source_refs 至少 1 个，且 ref 必须完全等于 source_items 里的 ref" in user_prompt


def test_html_page_source_client_fetches_watcha_page_as_topic_source():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://watcha.cn/"
        return httpx.Response(
            200,
            html="""
            <html>
              <head><title>观猹 - AI 产品的大众点评</title></head>
              <body>
                <h1>观猹</h1>
                <p>今日热门 AI 产品和真实用户评价。</p>
              </body>
            </html>
            """,
        )

    client = HtmlPageSourceClient(
        urls=["https://watcha.cn/"],
        transport=httpx.MockTransport(handler),
    )

    items = client.fetch_items()

    assert items[0].source_type == "观猹"
    assert items[0].title == "观猹 - AI 产品的大众点评"
    assert items[0].ref == "https://watcha.cn/"
    assert "今日热门 AI 产品" in items[0].excerpt


def test_deepseek_topic_generator_repairs_invalid_source_ref_by_note_similarity(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "daily.md").write_text(
        "# 每日记录\n\n今天记录项目交付风险。",
        encoding="utf-8",
    )
    (vault / "rag.md").write_text(
        "# 混合检索 RAG 实战\n\nES 全文检索 + 向量检索 + Rerank 可以提升召回率。",
        encoding="utf-8",
    )
    os.utime(vault / "rag.md", (1_700_000_000, 1_700_000_000))
    os.utime(vault / "daily.md", (1_800_000_000, 1_800_000_000))

    def handler(request: httpx.Request) -> httpx.Response:
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
                                            "title": "混合检索实战：Rerank 如何提升召回率",
                                            "core_viewpoint": "检索系统不是单一路径，而是多路召回加重排。",
                                            "reason": "来自 RAG 实战笔记。",
                                            "audience": "AI 工程师",
                                            "outline": ["全文检索", "向量召回", "Rerank"],
                                            "score": 91,
                                            "source_refs": [
                                                {
                                                    "source_type": "Obsidian",
                                                    "title": "模型写错的来源",
                                                    "ref": "missing.md",
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

    topics = DeepSeekTopicGenerator(
        Settings(deepseek_api_key="secret", obsidian_vault_path=str(vault)),
        ObsidianVault(vault),
        transport=httpx.MockTransport(handler),
    ).generate(day_number=1)

    assert topics[0].source_refs[0].title == "混合检索 RAG 实战"
    assert topics[0].source_refs[0].ref == "rag.md"


def test_settings_status_reports_obsidian_without_note_content(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text("# 私密笔记\n\n不要出现在状态接口里。", encoding="utf-8")
    settings = Settings(obsidian_vault_path=str(vault))

    status = settings.public_status()

    assert status["obsidian"]["configured"] is True
    assert status["obsidian"]["exists"] is True
    assert status["obsidian"]["path"] == str(vault)
    assert status["obsidian"]["markdown_count"] == 0
    assert "私密笔记" not in json.dumps(status, ensure_ascii=False)
