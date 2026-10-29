"""Explicit creator preferences and member-authorized memory HTTP routes."""
import re

from fastapi import APIRouter, HTTPException, Request, Response
from app.creator_profile import (
    CreatorProfileConflict, change_creator_preference, get_or_create_profile,
    upsert_creator_profile,
)
from app.schemas import (
    CreatorProfileResponse, CreatorProfileUpdate, CreatorProfileLearnResult,
    CreatorPreferenceChangeRequest,
)


def build_creator_profile_router(*, workspace_for, reader_for, member_for,
                                 database_for, conversations_for, profile_response) -> APIRouter:
    router = APIRouter()

    def _creator_profile_scope(request: Request) -> tuple[str, str | None]:
        workspace_id = workspace_for(request)
        owner_id, diagnostic = reader_for(request, workspace_id)
        if owner_id is None and not diagnostic:
            raise HTTPException(status_code=403, detail="请先确认当前成员身份，再读取创作偏好。")
        return workspace_id, owner_id


    @router.get("/api/creator-profile", response_model=CreatorProfileResponse)
    def get_creator_profile_endpoint(request: Request) -> CreatorProfileResponse:
        workspace_id, owner_id = _creator_profile_scope(request)
        return profile_response(get_or_create_profile(
            database_for(), workspace_id=workspace_id, owner_user_id=owner_id,
        ))


    @router.patch("/api/creator-profile", response_model=CreatorProfileResponse)
    def update_creator_profile_endpoint(body: CreatorProfileUpdate, request: Request) -> CreatorProfileResponse:
        workspace_id, owner_id = _creator_profile_scope(request)
        profile = get_or_create_profile(database_for(), workspace_id=workspace_id, owner_user_id=owner_id)
        if body.common_expressions is not None:
            profile.common_expressions = body.common_expressions
        if body.banned_words is not None:
            profile.banned_words = body.banned_words
        if body.style_preferences is not None:
            profile.style_preferences = body.style_preferences
        try:
            saved = upsert_creator_profile(profile, database_for(), workspace_id=workspace_id,
                                           owner_user_id=owner_id, expected_revision=body.expected_revision)
        except CreatorProfileConflict as exc:
            raise HTTPException(status_code=409, detail="创作偏好已更新，请重新读取并核对；你的修改没有覆盖已保存内容。") from exc
        return profile_response(saved)


    @router.get("/api/assistant/memory", response_model=CreatorProfileResponse)
    def read_assistant_memory(request: Request) -> CreatorProfileResponse:
        workspace_id = workspace_for(request)
        owner_id = member_for(request, workspace_id)
        return profile_response(get_or_create_profile(
            database_for(), workspace_id=workspace_id, owner_user_id=owner_id,
        ))


    @router.get("/api/assistant/memory/user.md")
    def export_assistant_memory(request: Request) -> Response:
        profile = read_assistant_memory(request)
        lines = ["# 我的创作偏好", "", "这些是我明确保存的偏好；当前任务要求优先，不授予工具或外发权限。", ""]
        for title, values in (("常用表达", profile.common_expressions), ("避免用词", profile.banned_words)):
            lines.extend([f"## {title}", ""])
            if not values:
                lines.append("未设置。")
            for value in values:
                lines.extend("> " + part for part in value.splitlines())
                lines.append("")
            lines.append("")
        lines.extend(["## 创作偏好", ""])
        if not profile.style_preferences:
            lines.append("未设置。")
        for key, value in profile.style_preferences.items():
            lines.append("> " + " ".join(key.splitlines()) + "：")
            lines.extend("> " + part for part in value.splitlines())
            lines.append("")
        return Response("\n".join(lines) + "\n", media_type="text/markdown",
                        headers={"Content-Disposition": 'attachment; filename="user.md"'})


    def _explicit_memory_action(message: str, action: str, key: str) -> bool:
        # This is the write boundary, not an intent planner: inferred habits and a
        # one-off production request must not silently become long-term preferences.
        if action == "remember":
            if re.search(r"(?:不要|别|不用|无需|不必).{0,4}(?:记住|保存|记录)", message):
                return False
            return bool(re.search(r"记住|\bremember\b|(?:保存|记录).{0,8}(?:偏好|习惯)|(?:以后|今后).{0,12}(?:默认|都用)", message, re.IGNORECASE))
        return bool(re.search(r"忘记|\bforget\b", message, re.IGNORECASE)
                    or (re.search(r"删除|移除|不再使用", message) and (key in message or "偏好" in message)))


    @router.patch("/api/assistant/memory", response_model=CreatorProfileResponse)
    def update_assistant_memory(body: CreatorPreferenceChangeRequest, request: Request) -> CreatorProfileResponse:
        workspace_id = workspace_for(request)
        owner_id = member_for(request, workspace_id)
        turn = conversations_for().get_turn(body.assistant_turn_id)
        if turn is None or conversations_for().get(turn["conversation_id"], workspace_id, owner_id) is None:
            raise HTTPException(status_code=404, detail="当前对话轮次不存在或不可访问。")
        if turn["status"] != "running":
            raise HTTPException(status_code=409, detail="这轮对话已结束，未再次修改长期偏好。")
        quote = body.source_quote.strip()
        message = turn["user_message"]
        if not quote or quote not in message or not _explicit_memory_action(message, body.action, body.key):
            raise HTTPException(status_code=409, detail="本轮没有明确要求长期保存或忘记这条偏好，未修改记忆。")
        key = body.key.strip()
        value = body.value.strip() if body.value is not None else None
        if not key or (body.action == "remember" and not value) or (body.action == "forget" and body.value is not None):
            raise HTTPException(status_code=422, detail="请明确要记住的偏好内容，或要忘记的偏好名称。")
        try:
            profile = change_creator_preference(
                database_for(), workspace_id=workspace_id, owner_user_id=owner_id,
                key=key, value=value, expected_revision=body.expected_revision,
                source_turn_id=turn["id"], source_quote=quote,
            )
        except CreatorProfileConflict as exc:
            raise HTTPException(status_code=409, detail="创作偏好已更新，请先读取最新内容；没有覆盖新的偏好。") from exc
        return profile_response(profile)


    @router.post("/api/creator-profile/learn", response_model=CreatorProfileLearnResult)
    def learn_creator_profile_endpoint(request: Request) -> CreatorProfileLearnResult:
        _creator_profile_scope(request)
        raise HTTPException(status_code=410, detail="旧的历史自动学习入口已停用。请明确保存创作偏好；助手推测需要先由你采纳。")


    return router
