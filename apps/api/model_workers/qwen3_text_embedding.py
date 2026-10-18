"""Exact, file-backed text lookup for the Qwen3-TTS worker's BF16 table."""
from __future__ import annotations

import json
import os
import stat
import struct
from pathlib import Path


TENSOR_NAME = "talker.model.text_embedding.weight"
# The safetensors format's maximum JSON header length.
MAX_HEADER_BYTES = 100_000_000


class Qwen3TextEmbedding:
    """Read only the requested rows; deliberately expose no dense parameter."""

    def __init__(self, weight_path: Path, *, expected_shape: tuple[int, int]):
        self._path = Path(weight_path)
        self._fd = os.open(self._path, os.O_RDONLY)
        try:
            info = os.fstat(self._fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Qwen text embedding requires a regular weight file")
            self._identity = self._file_identity(info)
            header_size = struct.unpack("<Q", self._read_exact(8, 0))[0]
            if not 2 <= header_size <= min(MAX_HEADER_BYTES, info.st_size - 8):
                raise ValueError("invalid Qwen safetensors header length")
            header = json.loads(self._read_exact(header_size, 8))
            if not isinstance(header, dict) or not isinstance(header.get(TENSOR_NAME), dict):
                raise ValueError("Qwen text embedding tensor is missing")
            tensor = header[TENSOR_NAME]
            shape = tensor.get("shape")
            if (
                not isinstance(shape, list)
                or len(shape) != 2
                or any(type(dim) is not int or dim <= 0 for dim in shape)
                or tuple(shape) != tuple(expected_shape)
            ):
                raise ValueError("Qwen text embedding shape does not match the model")
            if tensor.get("dtype") != "BF16":
                raise ValueError("Qwen text embedding must retain BF16 weights")
            offsets = tensor.get("data_offsets")
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or any(type(offset) is not int for offset in offsets)
            ):
                raise ValueError("invalid Qwen text embedding offsets")
            start, end = offsets
            self._rows, self._dims = shape
            self._row_bytes = self._dims * 2
            data_start = 8 + header_size
            if (
                start < 0
                or end - start != self._rows * self._row_bytes
                or end > info.st_size - data_start
            ):
                raise ValueError("Qwen text embedding offsets exceed its tensor or file")
            self._data_start = data_start + start
            self._ensure_unchanged()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _file_identity(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _ensure_unchanged(self):
        if self._fd is None:
            raise RuntimeError("Qwen text embedding is closed")
        if (
            self._file_identity(os.fstat(self._fd)) != self._identity
            or self._file_identity(self._path.stat()) != self._identity
        ):
            raise RuntimeError("Qwen weight file changed while its embedding was open")

    def _read_exact(self, size: int, offset: int) -> bytes:
        data = os.pread(self._fd, size, offset)
        if len(data) != size:
            raise OSError("short read from Qwen text embedding weight file")
        return data

    def __call__(self, token_ids):
        import mlx.core as mx
        import numpy as np

        self._ensure_unchanged()
        indices = np.asarray(token_ids)
        if indices.dtype.kind not in "iu":
            raise ValueError("Qwen text token IDs must be integers")
        if indices.size and (np.any(indices < 0) or np.any(indices >= self._rows)):
            raise ValueError("Qwen text token ID is outside the embedding table")
        unique, inverse = np.unique(indices.reshape(-1), return_inverse=True)
        data = b"".join(
            self._read_exact(self._row_bytes, self._data_start + int(index) * self._row_bytes)
            for index in unique
        )
        self._ensure_unchanged()
        words = np.frombuffer(data, dtype="<u2").reshape(len(unique), self._dims)
        # view is a bit reinterpretation, not a numeric conversion of BF16 values.
        rows = mx.array(words, dtype=mx.uint16).view(mx.bfloat16)
        return rows[mx.array(inverse, dtype=mx.int32)].reshape(*indices.shape, self._dims)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
