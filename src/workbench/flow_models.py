"""Private LangGraph persistence; never serialized through public run/history APIs."""

from sqlalchemy import JSON, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.models import Base

BINARY = LargeBinary().with_variant(LONGBLOB(), "mysql")


class GraphCheckpoint(Base):
    __tablename__ = "wb_graph_checkpoints"
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_runs.id"), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(120), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(40))
    payload_type: Mapped[str] = mapped_column(String(20))
    payload: Mapped[bytes] = mapped_column(BINARY)
    metadata_type: Mapped[str] = mapped_column(String(20))
    metadata_payload: Mapped[bytes] = mapped_column(BINARY)


class GraphWrite(Base):
    __tablename__ = "wb_graph_writes"
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_runs.id"), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(120), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(160))
    payload_type: Mapped[str] = mapped_column(String(20))
    payload: Mapped[bytes] = mapped_column(BINARY)


class FlowAction(Base):
    __tablename__ = "wb_flow_actions"
    run_id: Mapped[str] = mapped_column(ForeignKey("wb_runs.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    body: Mapped[dict] = mapped_column(JSON)
