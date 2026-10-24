"""Delivery duration and section windows, independent of media execution."""
from dataclasses import dataclass
from fractions import Fraction
import math

from app.speech_timing import estimate_speech_seconds


class DurationConflict(ValueError):
    pass


@dataclass(frozen=True)
class DurationRequirement:
    target_seconds: float
    mode: str = "exact"
    agreed_range: tuple[float, float] | None = None

    def __post_init__(self):
        if not math.isfinite(self.target_seconds) or self.target_seconds <= 0:
            raise ValueError("目标时长必须为正数")
        if self.mode not in {"exact", "approximate"}:
            raise ValueError("时长要求必须明确精确或约定范围")
        if self.mode == "approximate":
            if (self.agreed_range is None or len(self.agreed_range) != 2
                    or not all(math.isfinite(x) for x in self.agreed_range)
                    or not 0 < self.agreed_range[0] <= self.target_seconds <= self.agreed_range[1]):
                raise ValueError("近似时长必须给出包含目标的明确范围")
            object.__setattr__(self, "agreed_range", tuple(self.agreed_range))
        elif self.agreed_range is not None:
            raise ValueError("精确时长不能混入近似范围")

    @classmethod
    def from_plan(cls, plan):
        bounds = getattr(plan, "duration_range_seconds", None)
        return cls(plan.duration_target_seconds, getattr(plan, "duration_mode", "exact"),
                   tuple(bounds) if bounds is not None else None)

    def assess(self, video_duration, frame_rate):
        if video_duration is None or frame_rate is None:
            return "unknown"
        try:
            duration, fps = Fraction(str(video_duration)), Fraction(str(frame_rate))
        except (ValueError, ZeroDivisionError):
            return "unknown"
        if duration <= 0 or fps <= 0:
            return "unknown"
        if self.mode == "exact":
            return "met" if abs(duration - Fraction(str(self.target_seconds))) <= 1 / fps else "mismatch"
        lower, upper = self.agreed_range
        return "met" if Fraction(str(lower)) <= duration <= Fraction(str(upper)) else "mismatch"

    def require(self, video_duration, frame_rate):
        result = self.assess(video_duration, frame_rate)
        if result != "met":
            raise DurationConflict(f"视频交付时长未满足约定：目标 {self.target_seconds:g} 秒，"
                                   f"实际 {video_duration} 秒（{result}）；请调整合法镜头与节奏或明确修改约定。")

    def payload(self):
        return {"target_seconds": self.target_seconds, "mode": self.mode, "agreed_range": self.agreed_range}


@dataclass(frozen=True)
class SectionWindow:
    section_id: str
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self):
        return self.end_seconds - self.start_seconds


def measured_section_windows(requirement, sections, measured, *, adopted=(), frame_rate=30):
    """Fit known speech without moving adopted windows unless reconfirmation is needed."""
    weights = [estimate_speech_seconds(section.content) for section in sections]
    if not weights or any(weight <= 0 for weight in weights):
        raise DurationConflict("每段必须有可用于时长规划的采用正文")
    frames = round(requirement.target_seconds * frame_rate)
    minimum = [max(1, math.ceil(measured.get(section.id, 0) * frame_rate - 1e-9)) for section in sections]
    if sum(minimum) > frames:
        raise DurationConflict("真实配音无法放入目标时长；请明确调整目标或采用新的正文，不会截尾或变速原声。")

    def block(indices, start, end):
        available = end - start
        if available < sum(minimum[i] for i in indices):
            raise DurationConflict("已采用画面窗口需重新确认，未丢弃原配音或画面检查点。")
        if not indices:
            if available:
                raise DurationConflict("已采用画面窗口留下未分配区间，须重新确认。")
            return []
        pending, lengths = list(indices), {}
        while pending:
            total = sum(weights[i] for i in pending)
            constrained = [i for i in pending if available * weights[i] / total < minimum[i]]
            if not constrained:
                assigned = 0
                for position, i in enumerate(pending):
                    amount = (available - assigned if position == len(pending) - 1
                              else max(minimum[i], math.floor(available * weights[i] / total)))
                    lengths[i] = amount
                    assigned += amount
                break
            for i in constrained:
                lengths[i] = minimum[i]
                available -= minimum[i]
                pending.remove(i)
        result, cursor = [], start
        for i in indices:
            end = cursor + lengths[i]
            result.append(SectionWindow(sections[i].id, cursor / frame_rate, end / frame_rate))
            cursor = end
        return result

    positions = {section.id: i for i, section in enumerate(sections)}
    locks = sorted(adopted, key=lambda window: positions[window.section_id])
    result, index, cursor = [], 0, 0
    try:
        for window in locks:
            position = positions[window.section_id]
            start, end = round(window.start_seconds * frame_rate), round(window.end_seconds * frame_rate)
            if start < cursor or end - start < minimum[position]:
                raise DurationConflict("已采用窗口不能容纳真实配音，须重新确认。")
            result.extend(block(list(range(index, position)), cursor, start))
            result.append(window)
            index, cursor = position + 1, end
        result.extend(block(list(range(index, len(sections))), cursor, frames))
        return result
    except DurationConflict:
        # A new proposal may move these windows; publication must require the
        # corresponding adopted sections to be confirmed again.
        return block(list(range(len(sections))), 0, frames)


def stream_duration_seconds(stream):
    """Read a stream clock, never the container or another stream's endpoint."""
    if stream is None:
        return None
    try:
        value = float(stream.get("duration"))
    except (TypeError, ValueError):
        value = None
    if value is not None and math.isfinite(value):
        return value
    ticks, time_base = stream.get("duration_ts"), stream.get("time_base")
    if ticks is not None and time_base:
        try:
            return float(ticks) * float(Fraction(time_base))
        except (ValueError, ZeroDivisionError):
            return None
    return None
