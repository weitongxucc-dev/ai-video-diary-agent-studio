"""任务作业进度：只读既有持久化的 JobRun，不新增进度事实源。

任务页看到的进度、已耗时、更新时间、失败原因都来自后台已经落库的作业行；页面
关闭不改变这些事实，重开同一任务读到的是同一批记录。

等待与处理估时分开记录：只有作业行里真的有对应事实才给值，否则明确未知。
估时的计算依据和更新时间一起给出；没有任何可信样本时写「尚无法预计」，不编造
百分比或完成倒计时。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.production import JobRun

# 作业正在占用设备的哪种状态：这些要分开说，忙碌不是错误。
_ACTIVE_STATUSES = frozenset({"queued", "running", "interrupted"})


@dataclass(frozen=True)
class JobProgressView:
    """一条作业在任务页上的真实读数。"""

    id: str
    kind: str
    status: str
    progress: int
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    elapsed_seconds: float | None
    failure_reason: str | None
    impact: str | None
    repair_action: str | None
    # 等待估时和处理估时各自独立；没有可信依据就是 None（界面写未知）。
    wait_estimate_seconds: float | None = None
    processing_estimate_seconds: float | None = None
    estimate_basis: str | None = None


def _elapsed(run: JobRun, now: datetime) -> float | None:
    """已耗时：运行中按「服务端现在 − 开始时间」实时算，终态用结束时间减开始时间。

    没有开始时间就是未知，不用更新时间或其他时间充数。
    """
    if run.started_at is None:
        return None
    end = now if run.finished_at is None else run.finished_at
    return max(0.0, (end - run.started_at).total_seconds())


def job_progress_view(run: JobRun, *, now: datetime) -> JobProgressView:
    """把一条 JobRun 投影成任务页读数，并显式说明哪些估时未知。"""
    return JobProgressView(
        id=run.id,
        kind=run.kind,
        status=run.status,
        # 只报告作业自己写下来的进度；阶段序号不是进度。
        progress=run.progress,
        attempt=run.attempt,
        started_at=run.started_at,
        finished_at=run.finished_at,
        updated_at=run.updated_at,
        elapsed_seconds=_elapsed(run, now),
        failure_reason=run.failure_reason,
        impact=run.impact,
        repair_action=run.repair_action,
        # 本单不产出估时：当前这一次已耗时不是对下一次的可信预测样本，没有
        # 排队的可考依据，两项都保持未知，界面写明尚无法预计。
        wait_estimate_seconds=None,
        processing_estimate_seconds=None,
        estimate_basis=None,
    )


def summarize_job_progress(
    runs: list[JobRun], *, now: datetime
) -> tuple[list[JobProgressView], JobProgressView | None]:
    """返回（全部作业读数，当前作业读数）；当前作业取最新一条活跃作业。"""
    views = [job_progress_view(run, now=now) for run in runs]
    active = [view for view, run in zip(views, runs) if run.status in _ACTIVE_STATUSES]
    current = active[-1] if active else (views[-1] if views else None)
    return views, current