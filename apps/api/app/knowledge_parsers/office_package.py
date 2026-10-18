"""Bound ZIP expansion before handing untrusted Office packages to readers."""
from __future__ import annotations

import io
from zipfile import BadZipFile, ZipFile, ZIP_DEFLATED, ZIP_STORED

from app.knowledge_files import KnowledgeFileError

MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_PARTS = 4096
MAX_TABLE_CELLS = 100_000


def validate_office_package(data: bytes) -> None:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            parts = archive.infolist()
            if len(parts) > MAX_PACKAGE_PARTS or sum(part.file_size for part in parts) > MAX_EXPANDED_BYTES:
                raise KnowledgeFileError("Office 文件展开后过大，请拆分文档后导入（最多 64 MiB、4096 个文件部件）")
            if len({part.filename for part in parts}) != len(parts):
                raise KnowledgeFileError("Office 文件包含重复部件，无法确定真实正文，请重新另存为后导入")
            if any(part.flag_bits & 1 for part in parts):
                raise KnowledgeFileError("Office 文件已加密，请提供未加密副本")
            if any(part.compress_type not in {ZIP_DEFLATED, ZIP_STORED} for part in parts):
                raise KnowledgeFileError("Office 文件使用不支持的压缩方式，请重新另存为 docx/xlsx 后导入")
    except BadZipFile as exc:
        raise KnowledgeFileError("Office 文件损坏或不是有效的 docx/xlsx，请重新另存为后导入") from exc
