"""Validate saved source facts against the current calibrated audio unit."""


def validate_audio_source_specs(inputs, budget):
    for index, source in enumerate(inputs, 1):
        for field, label, cap in (
            ('duration_seconds', '原音频源完整时长（秒）', budget.max_source_duration_seconds),
            ('sample_rate', '采样率（Hz）', budget.max_sample_rate),
            ('channels', '声道数', budget.max_channels),
        ):
            value = source[field]
            if value is None:
                continue  # Unknown metadata still requires the existing admitted probe.
            if type(value) not in {int, float} or not 0 < value <= cap:
                detail = f'实际 {value:g}，已准备 {cap:g}' if type(value) in {int, float} else '未取得有效数值'
                raise ValueError(f'已保存的音频源规格超出标定预算：第 {index} 个源的{label}{detail}。'
                    '请按原源文件规格完成CPU媒体准备并核验，不用裁剪后的成片时长代替原源完整时长；'
                    '保留原Job与已完成单元，不提高资源阈值。')
