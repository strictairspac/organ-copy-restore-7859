from sqlalchemy import BigInteger, Column, Date, ForeignKey, Integer, String, Table
from .base import metadata


# 维修库中的实体表。同号记录在库中只保留一份，由各维护卷共同引用。
library_organs = Table(
    "library_organs", metadata,
    Column("organ_id", String(600), primary_key=True),
    Column("label", String(600), nullable=False),
)

library_visits = Table(
    "library_visits", metadata,
    Column("visit_id", String(600), primary_key=True),
    Column("organ_id", String(600), ForeignKey("library_organs.organ_id"), nullable=False, index=True),
    Column("service_day", Date, nullable=False),
)

library_readings = Table(
    "library_readings", metadata,
    Column("reading_id", String(600), primary_key=True),
    Column("visit_id", String(600), ForeignKey("library_visits.visit_id"), nullable=False, index=True),
    Column("detail", String(600), nullable=False),
)

library_volumes = Table(
    "library_volumes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("title", String(120), nullable=False),
    Column("digest", String(64), nullable=False),
    Column("source_case_id", ForeignKey("cases.id"), nullable=True),
    Column("source_run_id", ForeignKey("runs.id"), nullable=True),
    Column("admitted_by", String(48), nullable=False),
    Column("admitted_at", BigInteger, nullable=False),
)

# 卷与记录的归属关系：同一记录被多个卷共享时会出现多行，撤回时据此判定独有记录。
library_members = Table(
    "library_members", metadata,
    Column("volume_id", ForeignKey("library_volumes.id", ondelete="CASCADE"), primary_key=True),
    Column("relation", String(16), primary_key=True),
    Column("record_id", String(600), primary_key=True, index=True),
)
