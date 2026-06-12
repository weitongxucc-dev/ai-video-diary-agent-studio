from datetime import date

from app.llm import TemplateScriptGenerator
from app.models import EpisodeStatus
from app.repository import EpisodeRepository
from app.services import EpisodeService
from app.topics import TemplateTopicGenerator
from app.workflow import LangGraphEpisodeWorkflow


def test_langgraph_workflow_generates_topics_and_waits_for_human_approval():
    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 28))
    episode = repo.today()
    workflow = LangGraphEpisodeWorkflow(
        EpisodeService(
            script_generator=TemplateScriptGenerator(),
            topic_generator=TemplateTopicGenerator(),
        )
    )

    updated = workflow.run("generate_topics", episode)

    assert updated.status == EpisodeStatus.TOPICS_READY
    assert len(updated.topics) == 5
    assert "generate_topics" in workflow.node_names()
    assert "approve_topic" in workflow.node_names()
    assert workflow.human_review_nodes() == ["approve_topic", "approve_script", "mark_published"]


def test_langgraph_workflow_runs_human_approved_path_to_script():
    repo = EpisodeRepository(today_provider=lambda: date(2026, 5, 28))
    episode = repo.today()
    workflow = LangGraphEpisodeWorkflow(
        EpisodeService(
            script_generator=TemplateScriptGenerator(),
            topic_generator=TemplateTopicGenerator(),
        )
    )
    episode = workflow.run("generate_topics", episode)
    episode = workflow.run("approve_topic", episode, topic_id=episode.topics[0].id)

    updated = workflow.run("generate_script", episode)

    assert updated.status == EpisodeStatus.SCRIPT_READY
    assert updated.script is not None


def test_workflow_exposes_script_edit_and_feedback_revert_actions():
    workflow = LangGraphEpisodeWorkflow(EpisodeService())

    assert "update_script" in workflow.node_names()
    assert "regenerate_script" in workflow.node_names()
    assert "regenerate_script_section" in workflow.node_names()
    assert "revert_script_version" in workflow.node_names()
    assert "revert_feedback" in workflow.node_names()
