"""维修库接口验收：登录角色、归卷结果与撤回。"""
import os

import pytest
from fastapi.testclient import TestClient

from src.main import app

VOLUME = {
    "code": "API-VOL-1",
    "title": "接口验收卷",
    "organs": [{"organ_id": "ORG-API", "label": "接口琴体"}],
    "visits": [{"visit_id": "W-API", "organ_id": "ORG-API", "service_day": "2026-11-20"}],
    "readings": [{"reading_id": "D-API", "visit_id": "W-API", "detail": "接口测记"}],
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def token(client, username):
    response = client.post("/auth/login", json={"username": username, "password": os.environ["DEMO_PASSWORD"]})
    if response.status_code != 200:
        pytest.skip(f"演示账号不可用：{response.text}")
    return response.json()["token"]


def test_operator_can_admit_and_withdraw(client, clean_library):
    headers = {"Authorization": f"Bearer {token(client, 'operator')}"}
    admitted = client.post("/library", json=VOLUME, headers=headers)
    assert admitted.status_code == 201
    body = admitted.json()
    assert body["status"] == "admitted"
    volume_id = body["volume"]["id"]

    catalogue = client.get("/library", headers=headers).json()
    assert catalogue["counts"]["volumes"] >= 1
    assert volume_id in [volume["id"] for volume in catalogue["volumes"]]

    withdrawn = client.post(f"/library/{volume_id}/withdraw", headers=headers)
    assert withdrawn.status_code == 200
    assert withdrawn.json()["removed"] == {"organs": 1, "visits": 1, "readings": 1}


def test_conflict_response_lists_records(client, clean_library):
    headers = {"Authorization": f"Bearer {token(client, 'operator')}"}
    assert client.post("/library", json=VOLUME, headers=headers).status_code == 201
    conflicting = {**VOLUME, "code": "API-VOL-2", "title": "冲突卷",
                   "readings": [{"reading_id": "D-API", "visit_id": "W-API", "detail": "changed"}]}
    response = client.post("/library", json=conflicting, headers=headers)
    assert response.status_code == 409
    body = response.json()
    assert body["status"] == "rejected"
    assert body["conflicts"][0]["record_id"] == "D-API"


def test_viewer_cannot_admit(client, clean_library):
    headers = {"Authorization": f"Bearer {token(client, 'viewer')}"}
    response = client.post("/library", json=VOLUME, headers=headers)
    assert response.status_code == 403
    assert client.get("/library", headers=headers).status_code == 200


def test_invalid_records_rejected(client, clean_library):
    headers = {"Authorization": f"Bearer {token(client, 'operator')}"}
    bad = {**VOLUME, "readings": [{"reading_id": "D-X", "visit_id": "W-MISSING", "detail": "x"}]}
    response = client.post("/library", json=bad, headers=headers)
    assert response.status_code == 422
