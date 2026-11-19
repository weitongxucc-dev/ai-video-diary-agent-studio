# LatentSync calibration fixture

`visual.mp4` contains 16 frames at 25 fps in 1080 × 1920. A fictional portrait was generated with ImageGen from text alone. The input contained no photograph, likeness reference, user material, names or identifying data. The portrait is encoded as a brief H.264 video.

`driver.wav` contains two synthesized formant envelopes and exactly 10,240 mono samples at 16 kHz. `make_driver.py` uses only the Python standard library and contains no recordings or third-party voice models.

The installer copies both files and checks the pinned SHA-256 values. This fixture exercises real model execution and media timing. Its result does not establish perceptual lip-sync quality. [ImageGen output terms](https://openai.com/policies/terms-of-use/#content)
