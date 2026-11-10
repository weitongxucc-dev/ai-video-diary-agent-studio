---
name: source-speech
description: 原声口播、访谈或现场视频的证据读取、冗余取舍、声画字幕联动精剪及局部检查。
---

先读 read_current_task、read_task_object 及 read_task_edit_capabilities，核对镜头、音轨、字幕、图层与基础版本。已有作品沿原任务；尚无任务可在授权内 create_source_task。保留现场原声不要求重写文案、合成配音或重新制作口型。

从镜头的 asset_id 或真实检索候选调用 read_material_evidence。先读 segments：人工描述和 corrected_transcript 是已存纠正，视觉 facts/OCR 是有限采样，用户背景不是系统观察。再按疑点的源区间读 speech/words，并用 expected_updated_at 固定后续窗口。has_more 或 truncated_fields 表示还没读全。blocked、failed、未记录和没有音轨分别表达；转写不是实际听感。

比较重说组时，列出保留与删除的源区间、简短依据、保留的信息/数字/限定及未知：前一遍未说完、后一遍补充条件、强调和不同观点不能仅因相似而删除。“嗯、然后、就是”可能是衔接，不是删除词表。最末/最长/最短的一遍不自动最好。存在无法核实的事实矛盾先保留并集中求证；普通可撤销取舍在已有委托内自行完成。

精确边界使用实际 word/token 对齐或局部媒体证据；只有句级时间时可选完整句，不能按字数插值删除句内词。words 是原ASR的对齐，人工纠正不自动拥有这些词的时间；出现冲突先补证据，不能拼成更强承诺。

源时间转时间线：timeline = clip.timeline_start + (source - clip.source_start) / clip.playback_rate；先确认该源区间确实由当前镜头使用，同一原片多次使用要分别定位。音频毫秒、镜头秒和整条时间线毫秒不得混用。例如 source_start=40、timeline_start=10、rate=2 时，源42秒映射到时间线11秒；不是42秒。

在最新基础版本用 apply_typed_edit 的 RemoveTimelineRanges 提交 range_edit：removed_ranges_ms 为按起点排序、不重叠的当前时间线毫秒区间，reason 记录取舍证据。一个命令联动画面、声音、字幕、图层和后续时间，不必逐词拆成一串独立删除。不删除全部，不改锁定对象。若用户要先看方案，用 preview_typed_edit 并沿原提案采用。仅改字幕不等于声音已剪掉。

回读原请求、真实采用版本和源映射，确认没有吞掉要保留的事实。明确要求试听、预览或继续制作时沿现有媒体工具执行；可用的原生工作目录与 bash 可测局部波形/音量或生成片段，声明并登记输出后才称候选。read_task_observations / observe_task_output 只证明其实际模态和采样覆盖，未包含的听感/连续口型不宣称通过。

切点不准只修对应边界，保留满意段落；未知先查询原请求，版本冲突重读，不换键盲重放。资源等待保留原Job、记录与已完成单元。提供本次整组撤销；停止聊天不等于取消媒体，停止/接续沿实际执行回执。
