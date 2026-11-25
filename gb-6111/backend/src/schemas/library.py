from pydantic import BaseModel, Field, field_validator, model_validator

from src.domain import library


class VolumeInput(BaseModel):
    code: str | None = Field(default=None, max_length=48)
    title: str | None = Field(default=None, max_length=120)
    run_id: int | None = Field(default=None, ge=1)
    organs: list[dict] = Field(default_factory=list)
    visits: list[dict] = Field(default_factory=list)
    readings: list[dict] = Field(default_factory=list)

    @field_validator("code", "title")
    @classmethod
    def check_text(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("卷号和名称不能为空")
        return value.strip()

    @field_validator("organs", "visits", "readings")
    @classmethod
    def check_records(cls, value):
        if len(value) > 4000:
            raise ValueError("单段记录不能超过 4000 行")
        return value

    @model_validator(mode="after")
    def check_source(self):
        if self.run_id is None and not (self.organs or self.visits or self.readings):
            raise ValueError("请提供已通过核验的执行批次，或直接给出卷内记录")
        return self

    def records(self):
        return library.validate_records({
            "organs": self.organs, "visits": self.visits, "readings": self.readings,
        })


class ConflictEntry(BaseModel):
    relation: str
    label: str
    record_id: str
    existing: dict
    incoming: dict


class VolumeCounts(BaseModel):
    organs: int
    visits: int
    readings: int


class VolumeSummary(BaseModel):
    id: int
    code: str
    title: str
    digest: str
    source_case_id: int | None
    source_run_id: int | None
    admitted_by: str
    admitted_at: int
    counts: VolumeCounts


class AdmitResult(BaseModel):
    status: str
    volume: VolumeSummary | None
    conflicts: list[ConflictEntry]
    created: VolumeCounts
    shared: VolumeCounts


class WithdrawResult(BaseModel):
    status: str
    volume: VolumeSummary
    removed: VolumeCounts
    retained: VolumeCounts


class VolumeDetail(VolumeSummary):
    records: dict


class Catalogue(BaseModel):
    volumes: list[VolumeSummary]
    organs: list[dict]
    visits: list[dict]
    readings: list[dict]
    counts: dict[str, int]
