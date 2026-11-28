"""Local PDF evidence owned by a conversation, without a library entry."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.knowledge_file_store import (
    KnowledgeFileStore, KnowledgeOriginalIntegrityError, KnowledgeOriginalUnavailable,
)
from app.knowledge_files import KnowledgeFileError, PdfParseLimits, parse_knowledge_file


MAX_PDF_BYTES = 2 * 1024 * 1024
MAX_PDF_PAGES = 24
MAX_PDF_TEXT_BYTES = 16 * 1024
MAX_PDF_BASE64 = 4 * ((MAX_PDF_BYTES + 2) // 3)
PDF_KIND = "turn_pdf"


class AssistantTurnPdfReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["turn_pdf"]
    attachment_id: str = Field(pattern="^[0-9a-f]{64}$")
    label: str = Field(min_length=1, max_length=180)
    size_bytes: int = Field(gt=0, le=MAX_PDF_BYTES)
    page_count: int = Field(gt=0, le=MAX_PDF_PAGES)


class TurnPdfUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(min_length=4, max_length=MAX_PDF_BASE64)
    expected_reference_version: int = Field(ge=0)


def reference_items(reference: dict | None) -> list[dict]:
    if reference is None:
        return []
    return reference["items"] if reference["kind"] == "selection" else [reference]


def pdf_reference(reference: dict | None) -> dict | None:
    return next((item for item in reference_items(reference) if item["kind"] == PDF_KIND), None)


def without_pdf(reference: dict | None) -> dict | None:
    if pdf_reference(reference) is None:
        return reference
    items = [item for item in reference_items(reference) if item["kind"] != PDF_KIND]
    return {"kind": "selection", "items": items} if items else None


class TurnPdfError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


class TurnPdfStore:
    def __init__(self, database_path: Path):
        self._files = KnowledgeFileStore(database_path.parent)

    @staticmethod
    def scope(member_id: str, conversation_id: str) -> str:
        return json.dumps(["assistant-turn-pdf", member_id, conversation_id])

    @staticmethod
    def parse(filename: str, data: bytes):
        if (not filename or len(filename) > 180 or Path(filename).suffix.lower() != ".pdf"
                or any(char in filename for char in ("/", "\\", "\r", "\n", "\x00"))):
            raise KnowledgeFileError("请选择一个本地 PDF 文件。")
        if not 0 < len(data) <= MAX_PDF_BYTES:
            raise KnowledgeFileError("本轮 PDF 须为非空文件，且不超过 2 MiB。")
        return parse_knowledge_file(filename, data, pdf_limits=PdfParseLimits(MAX_PDF_PAGES, MAX_PDF_TEXT_BYTES))

    def prepare(self, payload: TurnPdfUpload):
        try:
            data = base64.b64decode(payload.content_base64, validate=True)
        except ValueError as exc:
            raise KnowledgeFileError("PDF 文件内容无效，请重新选择。") from exc
        parsed = self.parse(payload.filename, data)
        reference = AssistantTurnPdfReference(kind=PDF_KIND, attachment_id=parsed.content_hash,
            label=parsed.filename, size_bytes=len(data), page_count=len(parsed.sections))
        return reference.model_dump(), data, parsed.full_text

    def put(self, workspace_id, member_id, conversation_id, reference, data):
        try:
            self._files.put(workspace_id, self.scope(member_id, conversation_id), data, reference["attachment_id"])
        except KnowledgeOriginalIntegrityError as exc:
            raise TurnPdfError(409, "原 PDF 校验失败，请重新选择附件。") from exc
        except OSError as exc:
            raise TurnPdfError(503, "附件存储暂不可用，请重试。") from exc

    def read(self, workspace_id, member_id, conversation_id, reference):
        try:
            return self._files.read(workspace_id, self.scope(member_id, conversation_id),
                reference["attachment_id"], reference["size_bytes"])
        except KnowledgeOriginalUnavailable as exc:
            raise TurnPdfError(404, "原 PDF 已不可读取，请重新选择附件。") from exc
        except KnowledgeOriginalIntegrityError as exc:
            raise TurnPdfError(409, "原 PDF 校验失败，请重新选择附件。") from exc
        except OSError as exc:
            raise TurnPdfError(503, "附件存储暂不可用，请重试。") from exc

    def pages(self, workspace_id, member_id, conversation_id, reference):
        data = self.read(workspace_id, member_id, conversation_id, reference)
        try:
            parsed = self.parse(reference["label"], data)
        except KnowledgeFileError as exc:
            raise TurnPdfError(409, "原 PDF 无法重新解析，请重新选择附件。") from exc
        if len(parsed.sections) != reference["page_count"] or parsed.content_hash != reference["attachment_id"]:
            raise TurnPdfError(409, "原 PDF 的摘要或页码无法核对。")
        return [{"locator": page.locator, "text": page.text} for page in parsed.sections]
