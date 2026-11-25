"""维修库归卷验收：同号共用、冲突整卷拒绝、撤回只清独有记录、并发与读取语义。"""
import threading

import pytest

from src.domain import library

VOLUME_A = {
    "organs": [
        {"organ_id": "ORG-17", "label": "橡山礼堂管风琴"},
        {"organ_id": "ORG-22", "label": "澄木教学厅管风琴"},
    ],
    "visits": [
        {"visit_id": "W-1131", "organ_id": "ORG-17", "service_day": "2026-11-10"},
        {"visit_id": "W-1138", "organ_id": "ORG-22", "service_day": "2026-11-15"},
    ],
    "readings": [
        {"reading_id": "D-2", "visit_id": "W-1131", "detail": "a"},
        {"reading_id": "D-3", "visit_id": "W-1131", "detail": "b"},
        {"reading_id": "D-4", "visit_id": "W-1138", "detail": "c"},
    ],
}
VOLUME_B = {
    "organs": [
        {"organ_id": "ORG-17", "label": "橡山礼堂管风琴"},
        {"organ_id": "ORG-31", "label": "青松排练厅管风琴"},
    ],
    "visits": [
        {"visit_id": "W-1126", "organ_id": "ORG-17", "service_day": "2026-11-02"},
        {"visit_id": "W-1142", "organ_id": "ORG-31", "service_day": "2026-11-18"},
    ],
    "readings": [
        {"reading_id": "D-1", "visit_id": "W-1126", "detail": "x"},
        {"reading_id": "D-5", "visit_id": "W-1142", "detail": "y"},
    ],
}


def counts(records):
    return {relation: len(records[relation]) for relation in library.RELATION_ORDER}


def test_admit_creates_volume_and_records(db):
    outcome = library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    assert outcome["status"] == "admitted"
    assert outcome["created"] == {"organs": 2, "visits": 2, "readings": 3}
    assert outcome["shared"] == {"organs": 0, "visits": 0, "readings": 0}
    catalogue = library.catalog(db)
    assert catalogue["counts"] == {"volumes": 1, "organs": 2, "visits": 2, "readings": 3}
    detail = library.volume(db, outcome["volume"]["id"])
    assert detail["records"] == {
        "organs": VOLUME_A["organs"],
        "visits": VOLUME_A["visits"],
        "readings": VOLUME_A["readings"],
    }


def test_same_number_same_content_is_shared(db):
    library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    outcome = library.admit(db, code="VOL-B", title="乙卷", records=VOLUME_B, actor="operator")
    assert outcome["status"] == "admitted"
    # ORG-17 在两卷中同号同内容，只共用一份。
    assert outcome["shared"] == {"organs": 1, "visits": 0, "readings": 0}
    assert outcome["created"] == {"organs": 1, "visits": 2, "readings": 2}
    catalogue = library.catalog(db)
    assert catalogue["counts"] == {"volumes": 2, "organs": 3, "visits": 4, "readings": 5}
    assert [organ["organ_id"] for organ in catalogue["organs"]].count("ORG-17") == 1


def test_conflicting_record_rejects_whole_volume(db):
    library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    before = library.catalog(db)
    conflicting = {
        **VOLUME_B,
        "organs": [{"organ_id": "ORG-17", "label": "改名的琴体"}, VOLUME_B["organs"][1]],
    }
    outcome = library.admit(db, code="VOL-B", title="乙卷", records=conflicting, actor="operator")
    assert outcome["status"] == "rejected"
    assert outcome["volume"] is None
    assert [(c["relation"], c["record_id"]) for c in outcome["conflicts"]] == [("organs", "ORG-17")]
    assert outcome["conflicts"][0]["existing"]["label"] == "橡山礼堂管风琴"
    assert outcome["conflicts"][0]["incoming"]["label"] == "改名的琴体"
    # 整卷不入库：不新增卷行，也不写入未被冲突命中的新记录。
    after = library.catalog(db)
    assert after == before


def test_each_relation_conflict_is_detected(db):
    library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    for relation, mutate in (
        ("visits", lambda payload: payload["visits"][0].update(service_day="2026-11-11")),
        ("readings", lambda payload: payload["readings"][0].update(detail="changed")),
    ):
        import copy
        payload = copy.deepcopy(VOLUME_A)
        mutate(payload)
        outcome = library.admit(db, code=f"VOL-{relation}", title="t", records=payload, actor="operator")
        assert outcome["status"] == "rejected"
        assert {conflict["relation"] for conflict in outcome["conflicts"]} == {relation}


def test_duplicate_volume_code_is_idempotent(db):
    first = library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    again = library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    assert again["status"] == "unchanged"
    assert again["volume"]["id"] == first["volume"]["id"]
    assert library.catalog(db)["counts"]["volumes"] == 1


def test_reused_code_with_different_content_rejects(db):
    library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    outcome = library.admit(db, code="VOL-A", title="另一卷", records=VOLUME_B, actor="operator")
    assert outcome["status"] == "rejected"
    assert outcome["conflicts"][0]["relation"] == "volume"


def test_withdraw_removes_only_exclusive_records(db):
    first = library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    library.admit(db, code="VOL-B", title="乙卷", records=VOLUME_B, actor="operator")
    outcome = library.withdraw(db, first["volume"]["id"])
    # ORG-17 仍被乙卷引用，只能清走甲卷独有的记录。
    assert outcome["removed"] == {"organs": 1, "visits": 2, "readings": 3}
    assert outcome["retained"] == {"organs": 1, "visits": 0, "readings": 0}
    catalogue = library.catalog(db)
    assert catalogue["counts"] == {"volumes": 1, "organs": 2, "visits": 2, "readings": 2}
    assert [organ["organ_id"] for organ in catalogue["organs"]] == ["ORG-17", "ORG-31"]
    assert [volume["code"] for volume in catalogue["volumes"]] == ["VOL-B"]


def test_withdraw_retains_parent_of_shared_reading(db):
    # 甲卷包含工单 W-1131 与其父琴体 ORG-17，乙卷只引用 W-1131 及其测记。
    first = library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="operator")
    only_visit = {
        "organs": [VOLUME_A["organs"][0]],
        "visits": [VOLUME_A["visits"][0]],
        "readings": [VOLUME_A["readings"][0]],
    }
    library.admit(db, code="VOL-C", title="丙卷", records=only_visit, actor="operator")
    outcome = library.withdraw(db, first["volume"]["id"])
    # W-1131 记录仍被丙卷的测记间接引用，ORG-17 也仍被 W-1131 需要。
    assert outcome["retained"]["organs"] == 1
    assert outcome["retained"]["visits"] == 1
    assert outcome["retained"]["readings"] == 1
    assert library.catalog(db)["counts"] == {"volumes": 1, "organs": 1, "visits": 1, "readings": 1}


def test_withdraw_missing_volume_returns_none(db):
    assert library.withdraw(db, 12345) is None


def test_overlapping_volumes_serialise(clean_library):
    """两个卷共享同一记录并发入库：都成功且该记录只存一份。"""
    barrier = threading.Barrier(2)
    outcomes = {}

    def worker(name, code):
        barrier.wait()
        with clean_library.begin() as conn:
            outcomes[name] = library.admit(conn, code=code, title=code, records=VOLUME_A, actor="op")

    threads = [threading.Thread(target=worker, args=(name, code)) for name, code in (("A", "VOL-A"), ("B", "VOL-B"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert {outcome["status"] for outcome in outcomes.values()} == {"admitted"}
    with clean_library.connect() as conn:
        catalogue = library.catalog(conn)
    assert catalogue["counts"] == {"volumes": 2, "organs": 2, "visits": 2, "readings": 3}


def test_non_overlapping_volumes_proceed_concurrently(clean_library):
    """互不重叠的卷可以同时办理；两条入库都成功。"""
    barrier = threading.Barrier(2)
    results = []

    def worker(records, code):
        barrier.wait()
        with clean_library.begin() as conn:
            results.append(library.admit(conn, code=code, title=code, records=records, actor="op")["status"])

    threads = [
        threading.Thread(target=worker, args=(VOLUME_A, "VOL-A")),
        threading.Thread(target=worker, args=(VOLUME_B, "VOL-B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == ["admitted", "admitted"]
    with clean_library.connect() as conn:
        assert library.catalog(conn)["counts"]["volumes"] == 2


def test_reader_never_sees_partial_volume(db):
    """读取在写入未提交时看不到该卷，提交后看到整卷。"""
    started = threading.Event()
    release = threading.Event()

    def writer():
        with db.engine.begin() as conn:
            library.admit(conn, code="VOL-A", title="甲卷", records=VOLUME_A, actor="op")
            started.set()
            release.wait(10)

    thread = threading.Thread(target=writer)
    thread.start()
    assert started.wait(10)
    assert library.catalog(db)["counts"] == {"volumes": 0, "organs": 0, "visits": 0, "readings": 0}
    release.set()
    thread.join()
    assert library.catalog(db)["counts"] == {"volumes": 1, "organs": 2, "visits": 2, "readings": 3}


def test_validate_rejects_dangling_children(db):
    with pytest.raises(ValueError):
        library.validate_records({
            "organs": VOLUME_A["organs"],
            "visits": VOLUME_A["visits"],
            "readings": [{"reading_id": "D-X", "visit_id": "W-UNKNOWN", "detail": "z"}],
        })
    with pytest.raises(ValueError):
        library.validate_records({
            "organs": VOLUME_A["organs"][:1],
            "visits": [VOLUME_A["visits"][1]],
            "readings": [],
        })


def test_catalogue_is_consistent_after_withdraw(db):
    first = library.admit(db, code="VOL-A", title="甲卷", records=VOLUME_A, actor="op")
    library.withdraw(db, first["volume"]["id"])
    catalogue = library.catalog(db)
    assert catalogue["counts"] == {"volumes": 0, "organs": 0, "visits": 0, "readings": 0}
    assert catalogue["volumes"] == [] and catalogue["organs"] == []
