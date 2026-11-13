# OpenFilm 真实参考视频与真实成片验收（2026-11-13）

本次验收使用真实本地参考视频和真实 OpenFilm 成片，在项目副本中完成 `study → render → compare --fit → bounded look`，并补充同一时间窗的音频测量。原始 OpenFilm 项目没有被覆盖。

完整证据目录：

`/Volumes/Dujiangyan-VM/AI视频日记/验收/openfilm-reference-compare-2026-11-13`

关键结果：

- 参考片 `151.000s`，新渲染成片 `30.000s`；两者均为 `1080×1920 / 30 fps / AAC 48 kHz stereo`。
- `study`：`10 cuts`、`49 changes`、`30 holds`、`509 sound hits`。
- `render`：`900 frames`，使用 `h264_videotoolbox`，渲染耗时约 `148.3s`。
- `compare --fit`：cuts `0/10`，change starts `1/38`，rhythm `0.04`，palette `0.48`，layout `0.10`，SSIM `0.107`；检测到 `4` 个复用画面片段和 `1` 条复用声音轨道。
- bounded `look`：参考片和成片在 `13.3–13.6s` 均成功生成观察图；两者显示的画面和构图不同。
- bounded audio：参考片 mean/max `-19.8/-7.1 dB`，成片 `-37.3/-14.2 dB`；两条轨道均可解码，全片 integrated loudness 均为 `-16.0 LUFS`，但窗口内容和电平不一致。

结论：渲染、结构比较、有限视觉观察和音频技术测量均完成并保存；当前成片是重组选材的 highlight cut，不是逐镜头复刻，真实视觉和音频逐镜头相似度验收未通过。

报告、指标、哈希、ffprobe、原始命令日志、bounded 观察图和临时项目副本见上述证据目录中的 `acceptance.md`、`manifest.json`、`compare.md`、`study.md`、`logs/`、`observations/` 和 `project/`。
