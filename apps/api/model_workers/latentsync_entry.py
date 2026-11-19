"""Run the locally verified LatentSync MLX corrections with brow-aligned face input."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

import cv2
import numpy as np
import torch
from einops import rearrange

import official_onnx as pose
from latentsync.utils.affine_transform import AlignRestore
from latentsync.utils.image_processor import ImageProcessor


class BrowImageProcessor(ImageProcessor):
    def __init__(self, resolution=256, device="mps", mask_image=None, *, threads=None):
        super().__init__(resolution=resolution, device="cpu", mask_image=mask_image)
        self.restorer = AlignRestore(resolution=resolution, device=device)
        pose_root = Path(os.environ["LATENTSYNC_POSE_ROOT_PATH"])
        weights = str(pose_root / "dw-ll_ucoco_384.onnx")
        if threads is None:
            self.pose = pose.build_session(weights)
        else:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
            self.pose = ort.InferenceSession(weights, sess_options=options, providers=["CPUExecutionProvider"])

    def affine_transform(self, image):
        data, center, scale = pose.preprocess(image, (288, 384))
        result = self.pose.run(None, {
            self.pose.get_inputs()[0].name: data.transpose(2, 0, 1)[None].astype(np.float32)
        })
        points, scores = pose.postprocess(result, (288, 384), center, scale)
        if scores[0, 23:91].min() < 0.5:
            raise RuntimeError("人物面部定位置信度不足，无法生成高精度口型")
        face_points = points[0, 23:91]
        landmarks = np.round([
            face_points[17:22].mean(0), face_points[22:27].mean(0),
            face_points[[29, 30, 31, 35]].mean(0),
        ])
        face, matrix = self.restorer.align_warp_face(image.copy(), landmarks3=landmarks, smooth=True)
        box = [0, 0, face.shape[1], face.shape[0]]
        face = cv2.resize(face, (self.resolution, self.resolution), interpolation=cv2.INTER_LANCZOS4)
        return rearrange(torch.from_numpy(face), "h w c -> c h w"), box, matrix


if __name__ == "__main__":
    import local_face

    local_face.LocalImageProcessor = BrowImageProcessor
    runpy.run_path(str(Path.cwd() / "inference_fidelity.py"), run_name="__main__")
