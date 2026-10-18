"""Bounded lookup checks: tiny fixtures and a few real rows, never a full model."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "qwen3_text_embedding", Path(__file__).parents[1] / "model_workers" / "qwen3_text_embedding.py"
)
embedding_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(embedding_module)
Qwen3TextEmbedding = embedding_module.Qwen3TextEmbedding
TENSOR_NAME = embedding_module.TENSOR_NAME


def write_fixture(path, *, shape=(4, 8), dtype="BF16", offsets=None, data=None):
    if data is None:
        # Includes +/-zero, infinities, NaNs and subnormals as raw BF16 words.
        words = [0, 0x8000, 0x3F80, 0xBF80, 0x7F80, 0xFF80, 0x7FC1, 1]
        words += [0x3F00 + row * 8 + column for row in range(1, 4) for column in range(8)]
        data = struct.pack("<32H", *words)
    header = json.dumps({TENSOR_NAME: {
        "dtype": dtype, "shape": list(shape),
        "data_offsets": [0, len(data)] if offsets is None else offsets,
    }}).encode()
    path.write_bytes(struct.pack("<Q", len(header)) + header + data)
    return data


class FixtureTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "model.safetensors"
        write_fixture(self.path)


class FileBoundaryTests(FixtureTestCase):
    def test_header_contract_and_range_failures_close_the_file(self):
        cases = [
            ({"shape": (2, 16)}, "shape"),
            ({"shape": (True, 8)}, "shape"),
            ({"dtype": "F16"}, "BF16"),
            ({"offsets": [-1, 63]}, "offsets"),
            ({"offsets": [1, 65]}, "offsets"),
            ({"offsets": [0, 63]}, "offsets"),
            ({"offsets": [0, "64"]}, "offsets"),
        ]
        for kwargs, message in cases:
            with self.subTest(kwargs=kwargs):
                write_fixture(self.path, **kwargs)
                closed = []
                real_close = os.close
                def close(fd):
                    closed.append(fd)
                    real_close(fd)
                with patch.object(embedding_module.os, "close", side_effect=close):
                    with self.assertRaisesRegex(ValueError, message):
                        Qwen3TextEmbedding(self.path, expected_shape=(4, 8))
                self.assertEqual(len(closed), 1)

    def test_missing_tensor_bad_header_and_short_read_fail(self):
        with self.assertRaisesRegex(ValueError, "regular weight file"):
            Qwen3TextEmbedding(self.path.parent, expected_shape=(4, 8))
        for header in (b"{}", b"not JSON"):
            self.path.write_bytes(struct.pack("<Q", len(header)) + header)
            with self.assertRaises(ValueError):
                Qwen3TextEmbedding(self.path, expected_shape=(4, 8))
        self.path.write_bytes(struct.pack("<Q", 100_000_001))
        with self.assertRaisesRegex(ValueError, "header length"):
            Qwen3TextEmbedding(self.path, expected_shape=(4, 8))
        write_fixture(self.path)
        with patch.object(embedding_module.os, "pread", return_value=b""):
            with self.assertRaisesRegex(OSError, "short read"):
                Qwen3TextEmbedding(self.path, expected_shape=(4, 8))

    def test_detects_in_place_change_replacement_and_closed_reader(self):
        with Qwen3TextEmbedding(self.path, expected_shape=(4, 8)) as reader:
            with self.path.open("ab") as file:
                file.write(b"changed")
            with self.assertRaisesRegex(RuntimeError, "changed"):
                reader._ensure_unchanged()
        write_fixture(self.path)
        with Qwen3TextEmbedding(self.path, expected_shape=(4, 8)) as reader:
            replacement = self.path.with_name("replacement.safetensors")
            write_fixture(replacement)
            replacement.replace(self.path)
            with self.assertRaisesRegex(RuntimeError, "changed"):
                reader._ensure_unchanged()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            reader._ensure_unchanged()


@unittest.skipUnless(importlib.util.find_spec("mlx"), "requires the model runtime's MLX")
class ExactLookupTests(FixtureTestCase):
    def setUp(self):
        super().setUp()
        import mlx.core as mx
        import mlx.nn as nn
        import numpy as np
        self.mx, self.nn, self.np = mx, nn, np

    def assert_bits_equal(self, actual, expected):
        self.assertEqual(actual.dtype, self.mx.bfloat16)
        self.np.testing.assert_array_equal(
            self.np.asarray(actual.view(self.mx.uint16)), expected
        )

    def test_matches_embedding_bits_for_order_duplicates_and_shapes(self):
        mx, nn, np = self.mx, self.nn, self.np
        # Native load and native embedding both stay below one KiB here.
        baseline = nn.Embedding(4, 8)
        baseline.weight = mx.load(str(self.path))[TENSOR_NAME]
        with Qwen3TextEmbedding(self.path, expected_shape=(4, 8)) as reader:
            for ids in (mx.array([[3, 0, 2, 3]]), mx.array(1), mx.array([], dtype=mx.int32)):
                with self.subTest(shape=ids.shape):
                    expected = np.asarray(baseline(ids).view(mx.uint16))
                    self.assert_bits_equal(reader(ids), expected)
            model = nn.Module()
            model.text_embedding = reader
            self.assertEqual(model.parameters(), {})

    def test_each_call_reads_only_unique_rows_without_retaining_a_cache(self):
        with Qwen3TextEmbedding(self.path, expected_shape=(4, 8)) as reader:
            with patch.object(embedding_module.os, "pread", wraps=os.pread) as pread:
                for _ in range(2):
                    self.mx.eval(reader(self.mx.array([[3, 0, 3]])))
                self.assertEqual(pread.call_count, 4)
                self.assertTrue(all(call.args[1] == 16 for call in pread.call_args_list))

    def test_invalid_ids_short_rows_and_mid_read_changes_fail(self):
        mx = self.mx
        with Qwen3TextEmbedding(self.path, expected_shape=(4, 8)) as reader:
            for ids in (mx.array([-1]), mx.array([4]), mx.array([1.0])):
                with self.subTest(ids=str(ids)):
                    with self.assertRaises(ValueError):
                        reader(ids)
            with patch.object(embedding_module.os, "pread", return_value=b"short"):
                with self.assertRaisesRegex(OSError, "short read"):
                    reader(mx.array([0]))
            real_pread = os.pread
            def read_and_change(*args):
                data = real_pread(*args)
                with self.path.open("ab") as file:
                    file.write(b"changed")
                return data
            with patch.object(embedding_module.os, "pread", side_effect=read_and_change):
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    reader(mx.array([0]))

    def test_real_weight_rows_match_raw_file_bits(self):
        path = Path("/Volumes/Dujiangyan-VM/AI视频日记/models/qwen3-tts-12hz-0.6b-base-8bit/model.safetensors")
        if not path.is_file():
            self.skipTest("local Qwen model is not installed")
        mx, np = self.mx, self.np
        with path.open("rb") as file:
            size = struct.unpack("<Q", file.read(8))[0]
            header = json.loads(file.read(size))
            tensor = header[TENSOR_NAME]
            ids = [151935, 0, 42, 42]
            expected = []
            for index in ids:
                file.seek(8 + size + tensor["data_offsets"][0] + index * 4096)
                expected.append(np.frombuffer(file.read(4096), dtype="<u2"))
        with Qwen3TextEmbedding(path, expected_shape=(151936, 2048)) as reader:
            with patch.object(embedding_module.os, "pread", wraps=os.pread) as pread:
                self.assert_bits_equal(reader(mx.array([ids])), np.stack(expected)[None, ...])
                self.assertEqual(pread.call_count, 3)
                self.assertEqual(sum(call.args[1] for call in pread.call_args_list), 3 * 4096)


if __name__ == "__main__":
    unittest.main()
