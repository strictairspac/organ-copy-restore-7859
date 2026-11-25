from fastapi import APIRouter, Depends, HTTPException, Response

from src.config.resources import engine
from src.controllers.access import identity, operator
from src.schemas.library import AdmitResult, Catalogue, VolumeDetail, VolumeInput, WithdrawResult
from src.services import library

router = APIRouter(prefix="/library", tags=["Library"])


@router.get("", response_model=Catalogue, dependencies=[Depends(identity)])
def catalogue():
    with engine.connect() as db:
        return library.catalogue(db)


@router.get("/{volume_id}", response_model=VolumeDetail, dependencies=[Depends(identity)])
def volume_detail(volume_id: int):
    with engine.connect() as db:
        return library.get(db, volume_id)


@router.post("", response_model=AdmitResult, status_code=201)
def admit(form: VolumeInput, response: Response, user=Depends(operator)):
    with engine.begin() as db:
        try:
            outcome = library.admit(db, form, user["username"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    # 冲突卷未入库时返回 409，客户端可直接按状态码处理，同时保留完整冲突明细。
    if outcome["status"] == "rejected":
        response.status_code = 409
    return outcome


@router.post("/{volume_id}/withdraw", response_model=WithdrawResult, status_code=200)
def withdraw(volume_id: int, user=Depends(operator)):
    with engine.begin() as db:
        return library.withdraw(db, volume_id, user["username"])
