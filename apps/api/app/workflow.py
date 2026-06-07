from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models import Episode, FeedbackEvent, FeedbackValue
from app.services import EpisodeService

WorkflowAction = Literal[
    "generate_topics",
    "regenerate_topics",
    "approve_topic",
    "generate_script",
    "update_script",
    "regenerate_script",
    "regenerate_script_section",
    "revert_script_version",
    "approve_script",
    "revert_feedback",
    "bind_ad_hoc_video",
    "bind_ad_hoc_video_batch",
    "bind_video",
    "bind_processed_video",
    "bind_draft_package",
    "mark_published",
]


class EpisodeWorkflowState(TypedDict, total=False):
    action: WorkflowAction
    episode: Episode
    topic_id: str
    feedback_history: list[FeedbackEvent]
    script: Any
    section_id: str
    version_id: str
    feedback_event_id: str
    reason: str | None
    video: Any
    videos: list[Any]
    package_name: str
    package_path: str
    publish_url: str | None
    play_count: int | None
    like_count: int | None
    comment_count: int | None
    feedback_value: FeedbackValue | None
    feedback_reason: str | None


class LangGraphEpisodeWorkflow:
    human_nodes = ["approve_topic", "approve_script", "mark_published"]

    def __init__(self, service: EpisodeService) -> None:
        self._service = service
        self._node_names = [
            "generate_topics",
            "regenerate_topics",
            "approve_topic",
            "generate_script",
            "update_script",
            "regenerate_script",
            "regenerate_script_section",
            "revert_script_version",
            "approve_script",
            "revert_feedback",
            "bind_ad_hoc_video",
            "bind_ad_hoc_video_batch",
            "bind_video",
            "bind_processed_video",
            "bind_draft_package",
            "mark_published",
        ]
        self._graph = self._build_graph().compile()

    def node_names(self) -> list[str]:
        return [*self._node_names]

    def human_review_nodes(self) -> list[str]:
        return [*self.human_nodes]

    def run(self, action: WorkflowAction, episode: Episode, **kwargs: Any) -> Episode:
        state: EpisodeWorkflowState = {
            "action": action,
            "episode": episode,
            **kwargs,
        }
        result = self._graph.invoke(state)
        return result["episode"]

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(EpisodeWorkflowState)
        graph.add_node("generate_topics", self._generate_topics)
        graph.add_node("regenerate_topics", self._regenerate_topics)
        graph.add_node("approve_topic", self._approve_topic)
        graph.add_node("generate_script", self._generate_script)
        graph.add_node("update_script", self._update_script)
        graph.add_node("regenerate_script", self._regenerate_script)
        graph.add_node("regenerate_script_section", self._regenerate_script_section)
        graph.add_node("revert_script_version", self._revert_script_version)
        graph.add_node("approve_script", self._approve_script)
        graph.add_node("revert_feedback", self._revert_feedback)
        graph.add_node("bind_ad_hoc_video", self._bind_ad_hoc_video)
        graph.add_node("bind_ad_hoc_video_batch", self._bind_ad_hoc_video_batch)
        graph.add_node("bind_video", self._bind_video)
        graph.add_node("bind_processed_video", self._bind_processed_video)
        graph.add_node("bind_draft_package", self._bind_draft_package)
        graph.add_node("mark_published", self._mark_published)
        graph.add_conditional_edges(
            START,
            self._route_action,
            {node_name: node_name for node_name in self._node_names},
        )
        for node_name in self._node_names:
            graph.add_edge(node_name, END)
        return graph

    def _route_action(self, state: EpisodeWorkflowState) -> WorkflowAction:
        return state["action"]

    def _generate_topics(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.generate_topics(
                state["episode"],
                feedback_history=state.get("feedback_history"),
            )
        }

    def _regenerate_topics(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.regenerate_topics(
                state["episode"],
                feedback_history=state.get("feedback_history"),
            )
        }

    def _approve_topic(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.approve_topic(
                state["episode"],
                state["topic_id"],
            )
        }

    def _generate_script(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.generate_script(
                state["episode"],
                feedback_history=state.get("feedback_history"),
            )
        }

    def _update_script(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.update_script(
                state["episode"],
                state["script"],
                reason=state.get("reason"),
            )
        }

    def _regenerate_script(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.regenerate_script(
                state["episode"],
                feedback_history=state.get("feedback_history"),
                reason=state.get("reason"),
            )
        }

    def _regenerate_script_section(
        self,
        state: EpisodeWorkflowState,
    ) -> EpisodeWorkflowState:
        return {
            "episode": self._service.regenerate_script_section(
                state["episode"],
                state["section_id"],
                feedback_history=state.get("feedback_history"),
                reason=state.get("reason"),
            )
        }

    def _revert_script_version(
        self,
        state: EpisodeWorkflowState,
    ) -> EpisodeWorkflowState:
        return {
            "episode": self._service.revert_script_version(
                state["episode"],
                state["version_id"],
                reason=state.get("reason"),
            )
        }

    def _approve_script(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {"episode": self._service.approve_script(state["episode"])}

    def _revert_feedback(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.revert_feedback(
                state["episode"],
                state["feedback_event_id"],
                reason=state.get("reason"),
            )
        }

    def _bind_ad_hoc_video(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.bind_ad_hoc_video(
                state["episode"],
                state["video"],
            )
        }

    def _bind_video(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.bind_video(
                state["episode"],
                state["video"],
            )
        }

    def _bind_ad_hoc_video_batch(
        self,
        state: EpisodeWorkflowState,
    ) -> EpisodeWorkflowState:
        return {
            "episode": self._service.bind_ad_hoc_video_batch(
                state["episode"],
                state["videos"],
            )
        }

    def _bind_processed_video(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.bind_processed_video(
                state["episode"],
                state["video"],
            )
        }

    def _bind_draft_package(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.bind_draft_package(
                state["episode"],
                state["package_name"],
                state["package_path"],
            )
        }

    def _mark_published(self, state: EpisodeWorkflowState) -> EpisodeWorkflowState:
        return {
            "episode": self._service.mark_published(
                state["episode"],
                publish_url=state.get("publish_url"),
                play_count=state.get("play_count"),
                like_count=state.get("like_count"),
                comment_count=state.get("comment_count"),
                feedback_value=state.get("feedback_value"),
                feedback_reason=state.get("feedback_reason"),
            )
        }
