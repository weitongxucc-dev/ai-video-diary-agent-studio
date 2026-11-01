"""整机首页的真实聚合（WIL-98 / R07）。

一次调用给出当前身份可见的总量、真实日趋势、可进入的队列/待办/成果、
匿名整机运行数量和真实存储事实。

三条规矩：
1. 可见性不重写。素材、知识、任务一律走既有读过滤入口
   （``production_repo.list_assets``、``source_repository.list_knowledge_items_readable_by``、
   ``production_repo.task_records.list_creation_tasks``），本模块只是它们的调用方——
   聚合本身如果另写一套判据，就会泄露别人的私有内容。
2. 每个数字都带 scope/source/as_of。没有真实载体的项如实进 ``unknown``，
   不猜趋势口径、不把素材文件大小冒充整机用量、不造行业分布。
3. 整机匿名只给计数，不给路径、文件名或归属；记录数和在跑/排队数分开，
   表读不到就说未知而不是 0；待办/成果/队列只带真实 task_id，
   没有真实任务就不出现。
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta

import psutil

from app.models import utc_now
from app.creation.contracts import CreationTask
from app.production import MaterialAsset, ProductionRepository
from app.schemas import (
    HomeAggregationResponse,
    HomeCountResponse,
    HomeDeviceQueueResponse,
    HomeEntriesResponse,
    HomeFactMeta,
    HomeQueueEntryResponse,
    HomeStorageCapacityResponse,
    HomeStorageResponse,
    HomeTodoEntryResponse,
    HomeTrendPointResponse,
    HomeTrendResponse,
    HomeUnknownFact,
    HomeWorkEntryResponse,
)
from app.source_repository import SourceRepository

#: 素材接收既有的五个状态，与 ``material_ingestion_state_counts`` 同一组。
INTAKE_STATES = ("observing", "running", "ready", "failed", "blocked_disk")

#: ``job_runs`` 的状态全集，与建表 CHECK 约束一致，不另造状态词。
JOB_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled", "interrupted")

#: 排队的制作作业：与 ``list_pending_creation_executions`` 同一组判据。
#: 被中断的作业仍是待恢复的排队项，既不是完成也不是在跑。
PENDING_JOB_STATUSES = ("queued", "interrupted")

#: 首页队列只放真正还没做完的任务。已完成和已失败的不是在跑也不是在排队，
#: 混进来会让"队列长度"变成历史任务条数。
QUEUE_TASK_STATUSES = ("created", "running", "waiting_for_user")

#: 队列/待办/成果在首页各显示几条。条目本身来自真实任务，截断只影响展示。
ENTRY_LIMIT = 5


def _caller_scope(viewer_user_id: str | None, local_diagnostic: bool) -> str:
    """内容口径标签。认证态下的真实身份一律算 ``caller_visible``。"""
    if local_diagnostic and viewer_user_id is None:
        return "local_diagnostic"
    return "caller_visible"


def _day_span(days: int, today: date) -> list[date]:
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _utc_date(value: datetime) -> date:
    """从 SQLite 读回的时间戳不带时区；既有代码按 UTC 存取，这里照同一口径补回。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).date()
    return value.astimezone(UTC).date()


def _daily_trend(assets: list[MaterialAsset], days: int, today: date) -> list[HomeTrendPointResponse]:
    """按素材接收日（UTC）分桶，缺失的日子补 0——图上有一天没数不等于那天没有素材。"""
    received = [_utc_date(asset.created_at) for asset in assets]
    return [
        HomeTrendPointResponse(date=day, count=sum(1 for value in received if value == day))
        for day in _day_span(days, today)
    ]


def _device_status_counts(
    database_path,
) -> tuple[dict[str, int], int | None, int | None, int | None, list[str]]:
    """整机匿名的制作作业与素材接收状态计数。

    两张表都是全库聚合、刻意不带 ``workspace_id``——首页要的是"这台机器现在
    有多少东西在跑"的匿名数量。只读计数，不取路径、不取归属、不碰任务。

    记录数和队列长度不是一回事：``counts`` 含已成功/失败的历史，
    ``running``/``queued`` 才是此刻在跑和排队的真实数量。表读不到时 ``counts``
    留空、后三项留 None——全 0 的计数会被页面读成"现在很空闲"，那是假的。
    """
    unreadable = ["job_runs / material_ingestion_files 读不到，运行与排队数量未知"]
    try:
        conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    except (sqlite3.Error, ValueError):
        return {}, None, None, None, unreadable
    try:
        job_rows = conn.execute("SELECT status, COUNT(*) AS total FROM job_runs GROUP BY status").fetchall()
        intake_rows = conn.execute(
            "SELECT state, COUNT(*) AS total FROM material_ingestion_files GROUP BY state"
        ).fetchall()
    except sqlite3.Error:
        return {}, None, None, None, unreadable
    finally:
        conn.close()

    counts = {f"job.{status}": 0 for status in JOB_STATUSES}
    counts.update({f"intake.{state}": 0 for state in INTAKE_STATES})

    unknown: list[str] = []
    for prefix, rows, known in (("job", job_rows, JOB_STATUSES), ("intake", intake_rows, INTAKE_STATES)):
        for value, total in rows:
            name = str(value)
            if name in known:
                counts[f"{prefix}.{name}"] = int(total)
            else:
                unknown.append(f"未登记的{prefix}状态 {name}，未计入运行与排队")
    records_total = sum(counts.values())
    running = counts["job.running"] + counts["intake.running"]
    queued = sum(counts[f"job.{status}"] for status in PENDING_JOB_STATUSES) + counts["intake.observing"]
    return counts, records_total, running, queued, unknown


def _storage(
    repository: ProductionRepository,
    workspace_id: str,
    as_of,
) -> tuple[HomeStorageResponse, list[HomeUnknownFact]]:
    """真实存储事实：绑定/目录状态，加上已解析绑定目录的一次容量读取。

    容量只在目录解析出来时读取，且只取 ``psutil.disk_usage`` 的元数据——
    不递归、不读文件内容。目录断开、未绑定或读取失败时容量留空并说明原因，
    不用素材文件大小或任何估算顶替。
    """
    state = repository.material_source_files.material_ingestion_directory_state(workspace_id)
    directory_state = str(state["status"])
    directory = state["directory"]
    unknown: list[HomeUnknownFact] = []
    capacity: HomeStorageCapacityResponse | None = None
    if directory is None:
        unknown.append(
            HomeUnknownFact(
                key="storage.capacity",
                reason="素材目录绑定在其他工作区或不可解析，本次读不到容量。",
            )
        )
    else:
        try:
            usage = psutil.disk_usage(str(directory))
        except (OSError, psutil.Error, ValueError):
            unknown.append(
                HomeUnknownFact(
                    key="storage.capacity",
                    reason="已绑定目录当前读不到容量（断开、未挂载或不可读）。",
                )
            )
        else:
            capacity = HomeStorageCapacityResponse(
                total_bytes=int(usage.total),
                used_bytes=int(usage.used),
                free_bytes=int(usage.free),
            )
    return (
        HomeStorageResponse(
            directory_state=directory_state,
            bound_to_current_workspace=bool(state["bound_to_current_workspace"]),
            bound_to_another_workspace=bool(state["bound_to_another_workspace"]),
            configured_path_changed=bool(state["configured_path_changed"]),
            capacity=capacity,
            meta=HomeFactMeta(
                scope="host",
                source="production_repo.material_ingestion_directory_state + psutil.disk_usage(已绑定目录)",
                as_of=as_of,
            ),
        ),
        unknown,
    )


def _entries(
    tasks: list[CreationTask],
    as_of,
    source: str,
    limit: int,
) -> HomeEntriesResponse:
    """当前身份可读的真实条目。

    ``tasks`` 已经过既有读过滤，这里只做展示取舍：队列只放还没做完的任务
    （created/running/waiting_for_user），已完成和已失败的进不了队列，
    否则"队列长度"就变成了历史任务条数。
    """
    ordered = sorted(tasks, key=lambda task: task.updated_at, reverse=True)
    queue = [
        HomeQueueEntryResponse(
            task_id=task.id,
            title=task.name,
            stage=task.stage,
            status=task.status,
            updated_at=task.updated_at,
        )
        for task in ordered
        if task.status in QUEUE_TASK_STATUSES
    ][:limit]
    todos = [
        HomeTodoEntryResponse(
            task_id=task.id,
            title=task.name,
            stage=task.stage,
            reason=task.stage_reason or task.status,
            repair_action=task.stage_repair_action,
        )
        for task in ordered
        if task.stage_reason and task.stage_status in {"failed", "waiting_for_user", "running"}
    ][:limit]
    works = [
        HomeWorkEntryResponse(
            task_id=task.id,
            title=task.name,
            updated_at=task.updated_at,
        )
        for task in ordered
        if task.output_ready
    ][:limit]
    return HomeEntriesResponse(
        queue=queue,
        todos=todos,
        works=works,
        meta=HomeFactMeta(scope="caller_visible", source=source, as_of=as_of),
    )


def aggregate_home(
    *,
    workspace_id: str,
    repository: ProductionRepository,
    source_repository: SourceRepository,
    days: int = 7,
    viewer_user_id: str | None = None,
    local_diagnostic: bool = False,
    include_private: bool = True,
    entry_limit: int | None = None,
) -> HomeAggregationResponse:
    """按当前身份汇总整机首页。

    ``viewer_user_id`` / ``local_diagnostic`` / ``include_private`` 直接来自既有的
    ``_content_reader`` 与 ``_content_include_private``，路由层照旧解析后传入；
    认证态拿不到可信身份时，知识读取照旧失败，不退化成只看共享。

    ``days`` 只影响日趋势的跨度，不改变其他统计的口径。
    """
    if days < 1:
        raise ValueError("days 至少为 1")
    limit = ENTRY_LIMIT if entry_limit is None else max(0, entry_limit)
    as_of = utc_now()
    today = as_of.date()
    scope = _caller_scope(viewer_user_id, local_diagnostic)
    unknown: list[HomeUnknownFact] = [
        HomeUnknownFact(
            key="materials.by_industry",
            reason="素材没有行业字段；现有分类词表是场景/物体/动作/内容/主体/镜头，不是行业。",
        ),
    ]

    assets = repository.list_assets(
        workspace_id,
        include_private=include_private,
        viewer_user_id=viewer_user_id,
        local_diagnostic=local_diagnostic,
    )
    tasks = repository.task_records.list_creation_tasks(
        workspace_id,
        include_private=include_private,
        viewer_user_id=viewer_user_id,
        local_diagnostic=local_diagnostic,
    )
    knowledge_items = source_repository.list_knowledge_items_readable_by(
        workspace_id,
        user_id=viewer_user_id,
        local_diagnostic=local_diagnostic,
    )
    voices = repository.list_versioned_voice_profiles(workspace_id)

    material_meta = HomeFactMeta(
        scope=scope,
        source="production_repo.list_assets（既有可见性判据）",
        as_of=as_of,
    )
    counts = [
        HomeCountResponse(key="visible_assets", label="可见素材", value=len(assets), meta=material_meta),
        HomeCountResponse(
            key="analysis_ready",
            label="分析可用",
            value=sum(1 for asset in assets if asset.analysis_status == "ready"),
            meta=material_meta,
        ),
        HomeCountResponse(
            key="analysis_pending",
            label="待分析",
            value=sum(1 for asset in assets if asset.analysis_status == "pending"),
            meta=material_meta,
        ),
        HomeCountResponse(
            key="knowledge_sources",
            label="知识来源",
            value=len(knowledge_items),
            meta=HomeFactMeta(
                scope=scope,
                source="source_repository.list_knowledge_items_readable_by",
                as_of=as_of,
            ),
        ),
        HomeCountResponse(
            key="voices",
            label="声音",
            value=len(voices),
            meta=HomeFactMeta(
                scope="workspace",
                source="production_repo.list_versioned_voice_profiles（工作区级计数，不含声纹名称；该表无成员私有维度）",
                as_of=as_of,
            ),
        ),
        HomeCountResponse(
            key="tasks",
            label="任务",
            value=len(tasks),
            meta=HomeFactMeta(
                scope=scope,
                source="production_repo.list_creation_tasks",
                as_of=as_of,
            ),
        ),
        HomeCountResponse(
            key="works",
            label="作品记录",
            value=sum(1 for task in tasks if task.output_ready),
            meta=HomeFactMeta(
                scope=scope,
                source="production_repo.list_creation_tasks.output_ready",
                as_of=as_of,
            ),
        ),
    ]

    trend = HomeTrendResponse(
        days=days,
        points=_daily_trend(assets, days, today),
        meta=HomeFactMeta(
            scope=scope,
            source="当前身份可读素材的 created_at（UTC 日）——是本人可见素材的接收趋势，不是整机匿名趋势",
            as_of=as_of,
        ),
    )

    device_counts, records_total, running, queued, device_unknown = _device_status_counts(
        repository.database_path
    )
    device_queue = HomeDeviceQueueResponse(
        counts=device_counts,
        records_total=records_total,
        running=running,
        queued=queued,
        meta=HomeFactMeta(
            scope="device_anonymous",
            source=(
                "job_runs（制作执行与合成渲染）与 material_ingestion_files（素材接收）全库 "
                "GROUP BY status/state，无 workspace 过滤，仅计数；"
                "running/queued 扣除已成功、失败与已取消的历史记录"
            ),
            as_of=as_of,
        ),
    )
    unknown.append(
        HomeUnknownFact(
            key="device_queue.analysis",
            reason="分析没有独立的运行或排队载体：素材只有 analysis_status（pending/blocked/failed/ready），"
            "没有「正在分析」状态，所以分析在跑与排队如实未知。",
        )
    )
    unknown.extend(HomeUnknownFact(key="device_queue", reason=reason) for reason in device_unknown)

    storage, storage_unknown = _storage(repository, workspace_id, as_of)
    unknown.extend(storage_unknown)

    return HomeAggregationResponse(
        days=days,
        counts=counts,
        trend=trend,
        device_queue=device_queue,
        storage=storage,
        entries=_entries(tasks, as_of, "production_repo.list_creation_tasks", limit),
        unknown=unknown,
    )
