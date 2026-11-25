"""维修库归卷：同号记录共用、冲突整卷拒绝、撤回只清独有记录。

所有写操作在调用方的事务内完成，并通过按记录主键排序的 PostgreSQL 事务级
咨询锁串行化互相重叠的卷。互不重叠的卷持有不同的锁集合，可以并发办理；
读取依赖 MVCC，单条语句只看到整卷提交后的状态。
"""

from datetime import date
from time import time

from sqlalchemy import delete, func, insert, select, text

from src.models.library import (
    library_members,
    library_organs,
    library_readings,
    library_visits,
    library_volumes,
)
from src.utils.serial import content_digest

RELATION_LABELS = {"organs": "琴体", "visits": "维修工单", "readings": "测记"}
RELATION_COLUMNS = {
    "organs": ("organ_id", "label"),
    "visits": ("visit_id", "organ_id", "service_day"),
    "readings": ("reading_id", "visit_id", "detail"),
}
RELATION_ORDER = ("organs", "visits", "readings")
DELETE_ORDER = ("readings", "visits", "organs")
TABLES = {"organs": library_organs, "visits": library_visits, "readings": library_readings}


def _blank_counts():
    return {relation: 0 for relation in RELATION_ORDER}


def _normalise(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def validate_records(records):
    """校验维修库入卷的三段记录及其外键闭包，返回规范化后的副本。"""
    cleaned = {}
    for relation in RELATION_ORDER:
        columns = RELATION_COLUMNS[relation]
        items = records.get(relation)
        if not isinstance(items, list):
            raise ValueError(f"{RELATION_LABELS[relation]}应为数组")
        seen = set()
        allowed = set(columns)
        normalised = []
        for item in items:
            if not isinstance(item, dict) or set(item) != allowed:
                raise ValueError(f"{RELATION_LABELS[relation]}字段须为 {'、'.join(columns)}")
            for column in columns:
                value = item[column]
                if not isinstance(value, str) or not 1 <= len(value) <= 600:
                    raise ValueError(f"{RELATION_LABELS[relation]}字段须为非空且不超过 600 字的文本")
                if any(char == "\x00" or 0xD800 <= ord(char) <= 0xDFFF for char in value):
                    raise ValueError(f"{RELATION_LABELS[relation]}字段含不可入库的字符")
            key = item[columns[0]]
            if key in seen:
                raise ValueError(f"{RELATION_LABELS[relation]}主键重复：{key}")
            seen.add(key)
            normalised.append({column: item[column] for column in columns})
        cleaned[relation] = normalised

    organs = {item["organ_id"] for item in cleaned["organs"]}
    visits = {item["visit_id"] for item in cleaned["visits"]}
    for visit in cleaned["visits"]:
        if visit["organ_id"] not in organs:
            raise ValueError(f"工单 {visit['visit_id']} 缺少琴体")
        try:
            if date.fromisoformat(visit["service_day"]).isoformat() != visit["service_day"]:
                raise ValueError
        except ValueError as exc:
            raise ValueError("维修日期须为 YYYY-MM-DD") from exc
    for reading in cleaned["readings"]:
        if reading["visit_id"] not in visits:
            raise ValueError(f"测记 {reading['reading_id']} 缺少工单")
    return cleaned


def _incoming(records):
    """把选中闭包整理成按主键排序的记录，保持琴体→工单→测记的外键顺序。"""
    result = {}
    for relation in RELATION_ORDER:
        columns = RELATION_COLUMNS[relation]
        entries = []
        for item in records[relation]:
            entries.append((
                item[columns[0]],
                tuple(_normalise(item[column]) for column in columns),
                {column: _normalise(item[column]) for column in columns},
            ))
        result[relation] = sorted(entries, key=lambda entry: entry[0])
    return result


def _lock(db, entries, code=None):
    """按固定顺序获取事务级咨询锁，避免死锁，并让重叠卷彼此排队。"""
    if db.dialect.name != "postgresql":
        return
    keys = {f"library:{relation}:{key}" for relation, key in entries}
    if code is not None:
        keys.add(f"library:volume:{code}")
    for key in sorted(keys):
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})


def _existing(db, relation, keys):
    if not keys:
        return {}
    table = TABLES[relation]
    columns = RELATION_COLUMNS[relation]
    condition = table.c[columns[0]].in_(keys)
    rows = db.execute(select(table).where(condition)).mappings().all()
    return {
        row[columns[0]]: tuple(_normalise(row[column]) for column in columns)
        for row in rows
    }


def _summary(db, row):
    counts = _blank_counts()
    statement = (
        select(library_members.c.relation, func.count())
        .where(library_members.c.volume_id == row["id"])
        .group_by(library_members.c.relation)
    )
    for relation, count in db.execute(statement):
        if relation in counts:
            counts[relation] = count
    return {
        "id": row["id"],
        "code": row["code"],
        "title": row["title"],
        "digest": row["digest"],
        "source_case_id": row["source_case_id"],
        "source_run_id": row["source_run_id"],
        "admitted_by": row["admitted_by"],
        "admitted_at": row["admitted_at"],
        "counts": counts,
    }


# 读取统一走单条 JSON 查询：PostgreSQL 会给同一条语句一个快照，因此并发入库或
# 撤回在语句执行中途提交时，读到的仍是某个整卷生效后的完整状态。
_VOLUME_FIELDS = """
    'id', v.id, 'code', v.code, 'title', v.title, 'digest', v.digest,
    'source_case_id', v.source_case_id, 'source_run_id', v.source_run_id,
    'admitted_by', v.admitted_by, 'admitted_at', v.admitted_at,
    'counts', json_build_object(
        'organs', (SELECT count(*) FROM library_members m
                   WHERE m.volume_id = v.id AND m.relation = 'organs'),
        'visits', (SELECT count(*) FROM library_members m
                   WHERE m.volume_id = v.id AND m.relation = 'visits'),
        'readings', (SELECT count(*) FROM library_members m
                     WHERE m.volume_id = v.id AND m.relation = 'readings')
    )
"""

_VOLUME_JSON = f"json_build_object({_VOLUME_FIELDS})"

_RECORD_JSON = {
    relation: f"""(SELECT coalesce(json_agg(json_build_object(
        {", ".join(f"'{column}', src.{column}" for column in RELATION_COLUMNS[relation])}
    ) ORDER BY src.{RELATION_COLUMNS[relation][0]}), '[]'::json)
    FROM library_members m JOIN library_{relation} src ON src.{RELATION_COLUMNS[relation][0]} = m.record_id
    WHERE m.volume_id = v.id AND m.relation = '{relation}')"""
    for relation in RELATION_ORDER
}

_CATALOG_JSON = {
    relation: f"""coalesce((SELECT json_agg(json_build_object(
        {", ".join(f"'{column}', src.{column}" for column in RELATION_COLUMNS[relation])}
    ) ORDER BY src.{RELATION_COLUMNS[relation][0]}) FROM library_{relation} src), '[]'::json)"""
    for relation in RELATION_ORDER
}


def _postgres(db):
    return db.dialect.name == "postgresql"


def admit(db, *, code, title, records, actor, source_case_id=None, source_run_id=None):
    """把一卷记录并入维修库。

    同号同内容共用既有记录；任一同号记录内容不同则整卷拒绝并列出冲突，
    不写入任何琴体、工单、测记或卷行。同一卷号重复入库保持幂等。
    """
    incoming = _incoming(records)
    _lock(db, [(relation, key) for relation in RELATION_ORDER for key, _, _ in incoming[relation]], code)

    digest = content_digest({
        relation: [item for _, _, item in incoming[relation]] for relation in RELATION_ORDER
    })
    existing_volume = db.execute(
        select(library_volumes).where(library_volumes.c.code == code)
    ).mappings().first()
    if existing_volume is not None:
        if existing_volume["digest"] == digest:
            return {
                "status": "unchanged",
                "volume": _summary(db, existing_volume),
                "conflicts": [],
                "created": _blank_counts(),
                "shared": _blank_counts(),
            }
        return {
            "status": "rejected",
            "volume": None,
            "conflicts": [{
                "relation": "volume",
                "label": "卷号",
                "record_id": code,
                "existing": {
                    "title": existing_volume["title"],
                    "digest": existing_volume["digest"],
                    "admitted_by": existing_volume["admitted_by"],
                },
                "incoming": {"title": title, "digest": digest},
            }],
            "created": _blank_counts(),
            "shared": _blank_counts(),
        }

    existing = {
        relation: _existing(db, relation, [key for key, _, _ in incoming[relation]])
        for relation in RELATION_ORDER
    }
    conflicts = []
    for relation in RELATION_ORDER:
        for key, values, item in incoming[relation]:
            stored = existing[relation].get(key)
            if stored is not None and stored != values:
                conflicts.append({
                    "relation": relation,
                    "label": RELATION_LABELS[relation],
                    "record_id": key,
                    "existing": dict(zip(RELATION_COLUMNS[relation], stored, strict=True)),
                    "incoming": item,
                })
    if conflicts:
        return {
            "status": "rejected",
            "volume": None,
            "conflicts": conflicts,
            "created": _blank_counts(),
            "shared": _blank_counts(),
        }

    created = _blank_counts()
    shared = _blank_counts()
    new_records = {relation: [] for relation in RELATION_ORDER}
    for relation in RELATION_ORDER:
        for key, values, item in incoming[relation]:
            if key in existing[relation]:
                shared[relation] += 1
            else:
                new_records[relation].append({column: item[column] for column in RELATION_COLUMNS[relation]})
                created[relation] += 1

    volume_id = db.execute(insert(library_volumes).values(
        code=code,
        title=title,
        digest=digest,
        source_case_id=source_case_id,
        source_run_id=source_run_id,
        admitted_by=actor,
        admitted_at=int(time()),
    )).inserted_primary_key[0]

    for relation in RELATION_ORDER:
        if new_records[relation]:
            db.execute(insert(TABLES[relation]).values(new_records[relation]))

    memberships = [
        {"volume_id": volume_id, "relation": relation, "record_id": key}
        for relation in RELATION_ORDER
        for key, _, _ in incoming[relation]
    ]
    if memberships:
        db.execute(insert(library_members).values(memberships))

    row = db.execute(
        select(library_volumes).where(library_volumes.c.id == volume_id)
    ).mappings().one()
    return {
        "status": "admitted",
        "volume": _summary(db, row),
        "conflicts": [],
        "created": created,
        "shared": shared,
    }


def withdraw(db, volume_id):
    """撤回一卷：只删除不再被其他在库卷引用的记录。

    返回 None 表示卷不存在。记录按测记→工单→琴体的顺序删除，并保留仍被
    其他卷引用或被保留子记录引用的父记录。
    """
    probe = db.execute(
        select(library_volumes.c.id, library_volumes.c.code)
        .where(library_volumes.c.id == volume_id)
    ).mappings().first()
    if probe is None:
        return None
    members = db.execute(
        select(library_members.c.relation, library_members.c.record_id)
        .where(library_members.c.volume_id == volume_id)
    ).mappings().all()
    # 先取咨询锁再锁行，与入库保持同一加锁顺序，避免同卷号并发时死锁。
    _lock(db, [(member["relation"], member["record_id"]) for member in members], probe["code"])

    row = db.execute(
        select(library_volumes).where(library_volumes.c.id == volume_id).with_for_update()
    ).mappings().first()
    if row is None:
        return None

    before = _blank_counts()
    for member in members:
        if member["relation"] in before:
            before[member["relation"]] += 1

    db.execute(delete(library_members).where(library_members.c.volume_id == volume_id))

    removed = _blank_counts()
    retained = _blank_counts()
    for relation in DELETE_ORDER:
        keys = list(dict.fromkeys(
            member["record_id"] for member in members if member["relation"] == relation
        ))
        if not keys:
            continue
        still_referenced = set(db.execute(
            select(library_members.c.record_id).where(
                library_members.c.relation == relation,
                library_members.c.record_id.in_(keys),
            )
        ).scalars())
        if relation == "visits":
            still_referenced |= set(db.execute(
                select(library_readings.c.visit_id).where(library_readings.c.visit_id.in_(keys))
            ).scalars())
        elif relation == "organs":
            still_referenced |= set(db.execute(
                select(library_visits.c.organ_id).where(library_visits.c.organ_id.in_(keys))
            ).scalars())
        orphans = [key for key in keys if key not in still_referenced]
        if orphans:
            column = TABLES[relation].c[RELATION_COLUMNS[relation][0]]
            db.execute(delete(TABLES[relation]).where(column.in_(orphans)))
        removed[relation] = len(orphans)
        retained[relation] = len(keys) - len(orphans)

    db.execute(delete(library_volumes).where(library_volumes.c.id == volume_id))
    return {
        "status": "withdrawn",
        "volume": {
            "id": row["id"],
            "code": row["code"],
            "title": row["title"],
            "digest": row["digest"],
            "source_case_id": row["source_case_id"],
            "source_run_id": row["source_run_id"],
            "admitted_by": row["admitted_by"],
            "admitted_at": row["admitted_at"],
            "counts": before,
        },
        "removed": removed,
        "retained": retained,
    }


def volumes(db):
    if _postgres(db):
        rows = db.scalar(text(f"SELECT coalesce(json_agg({_VOLUME_JSON} ORDER BY v.id), '[]'::json) "
                               "FROM library_volumes v"))
        return list(rows)
    rows = db.execute(select(library_volumes).order_by(library_volumes.c.id)).mappings().all()
    return [_summary(db, row) for row in rows]


def volume(db, volume_id):
    if _postgres(db):
        expression = "json_build_object(" + _VOLUME_FIELDS + ", 'records', json_build_object(" + ", ".join(
            f"'{relation}', {_RECORD_JSON[relation]}" for relation in RELATION_ORDER
        ) + "))"
        return db.scalar(text(
            f"SELECT CASE WHEN v.id IS NULL THEN NULL ELSE {expression} END "
            "FROM (SELECT :volume_id AS id) probe LEFT JOIN library_volumes v ON v.id = probe.id"
        ), {"volume_id": volume_id})
    row = db.execute(
        select(library_volumes).where(library_volumes.c.id == volume_id)
    ).mappings().first()
    if row is None:
        return None
    summary = _summary(db, row)
    summary["records"] = _records_by_volume(db, volume_id)
    return summary


def _records_by_volume(db, volume_id):
    members = db.execute(
        select(library_members.c.relation, library_members.c.record_id)
        .where(library_members.c.volume_id == volume_id)
    ).mappings().all()
    by_relation = {relation: [] for relation in RELATION_ORDER}
    for relation in RELATION_ORDER:
        keys = [member["record_id"] for member in members if member["relation"] == relation]
        if not keys:
            continue
        table = TABLES[relation]
        columns = RELATION_COLUMNS[relation]
        rows = db.execute(
            select(table).where(table.c[columns[0]].in_(keys)).order_by(table.c[columns[0]])
        ).mappings().all()
        by_relation[relation] = [
            {column: _normalise(row[column]) for column in columns} for row in rows
        ]
    return by_relation


def catalog(db):
    """在单一快照中读取在库卷及其记录。"""
    if _postgres(db):
        volumes_expression = f"coalesce((SELECT json_agg({_VOLUME_JSON} ORDER BY v.id) FROM library_volumes v), '[]'::json)"
        expression = ("json_build_object("
                      f"'volumes', {volumes_expression}, "
                      f"'organs', {_CATALOG_JSON['organs']}, "
                      f"'visits', {_CATALOG_JSON['visits']}, "
                      f"'readings', {_CATALOG_JSON['readings']})")
        result = db.scalar(text(f"SELECT {expression}"))
        counts = {relation: len(result[relation]) for relation in RELATION_ORDER}
        result["counts"] = {"volumes": len(result["volumes"]), **counts}
        return result
    catalogue = {"volumes": volumes(db)}
    for relation in RELATION_ORDER:
        table = TABLES[relation]
        column = table.c[RELATION_COLUMNS[relation][0]]
        rows = db.execute(select(table).order_by(column)).mappings().all()
        catalogue[relation] = [
            {name: _normalise(row[name]) for name in RELATION_COLUMNS[relation]} for row in rows
        ]
    catalogue["counts"] = {
        "volumes": len(catalogue["volumes"]),
        **{relation: len(catalogue[relation]) for relation in RELATION_ORDER},
    }
    return catalogue
