from fastapi import HTTPException
from sqlalchemy import select

from src.constants.job_state import JobState
from src.domain import library as domain
from src.domain.relations import selected_records
from src.models.cases import cases
from src.models.runs import runs
from .audit import record


def list_volumes(db):
    return domain.volumes(db)


def catalogue(db):
    return domain.catalog(db)


def get(db, volume_id):
    entry = domain.volume(db, volume_id)
    if entry is None:
        raise HTTPException(404, "维修库卷不存在")
    return entry


def verified_records(db, run_id):
    """从已完成的复原批次还原选中闭包，作为入库的唯一来源。"""
    run = db.execute(
        select(runs, cases.c.code.label("case_code"), cases.c.title.label("case_title"), cases.c.payload)
        .join(cases, cases.c.id == runs.c.case_id)
        .where(runs.c.id == run_id)
    ).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    if run["status"] != JobState.DONE:
        raise HTTPException(409, "只有通过核验并完成复原的批次才能入库")
    if not (run["result"] or {}).get("metrics", {}).get("还原工单"):
        raise HTTPException(409, "该批次没有可入库的复原结果")
    payload = run["payload"]
    if not isinstance(payload, dict) or not isinstance(payload.get("requested"), list):
        raise HTTPException(409, "该批次缺少可复原的卷内记录")
    selected = payload["requested"][:run["parameter"]]
    return selected_records(payload, selected), run


def admit(db, data, actor):
    run = None
    if data.run_id is not None:
        records, run = verified_records(db, data.run_id)
        if data.organs or data.visits or data.readings:
            if data.records() != records:
                raise HTTPException(409, "卷内记录与核验批次的选中闭包不一致")
    else:
        records = data.records()
    code = data.code or (run["case_code"] if run else None)
    title = data.title or (f"{run['case_title']} · 复原卷" if run else None)
    if not code or not title:
        raise HTTPException(422, "请提供卷号和名称")
    outcome = domain.admit(
        db,
        code=code,
        title=title,
        records=records,
        actor=actor,
        source_case_id=run["case_id"] if run else None,
        source_run_id=run["id"] if run else None,
    )
    if outcome["status"] == "admitted":
        record(db, "admit_volume", actor, "volume", outcome["volume"]["id"])
    elif outcome["status"] == "rejected":
        record(db, "admit_conflict", actor, "run" if run else "volume", run["id"] if run else 0)
    return outcome


def withdraw(db, volume_id, actor):
    outcome = domain.withdraw(db, volume_id)
    if outcome is None:
        raise HTTPException(404, "维修库卷不存在")
    record(db, "withdraw_volume", actor, "volume", volume_id)
    return outcome
