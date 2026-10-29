"""Synchronous SQLAlchemy saver for bounded, single-branch LangGraph workflows."""

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import select

from workbench.flow_models import GraphCheckpoint, GraphWrite


class SQLCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, database, run_id, *, execution_guard=None):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self.database, self.run_id = database, run_id
        self.execution_guard = execution_guard

    def require_lease(self, session):
        if self.execution_guard and not self.execution_guard(session):
            from workbench.mcp_gateway import ToolFailure

            raise ToolFailure("worker_lease_lost")

    def scope(self, config):
        values = config["configurable"]
        if values["thread_id"] != self.run_id or values.get("checkpoint_ns", ""):
            raise ValueError("Checkpoint scope mismatch; subgraphs are not enabled")
        return self.run_id, ""

    def encoded(self, value):
        kind, blob = self.serde.dumps_typed(value)
        if len(blob) > 512 * 1024:
            raise ValueError("Checkpoint size limit exceeded")
        return kind, blob

    def config(self, checkpoint_id):
        return {
            "configurable": {
                "thread_id": self.run_id,
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
            }
        }

    def get_tuple(self, config):
        run_id, namespace = self.scope(config)
        statement = select(GraphCheckpoint).where(
            GraphCheckpoint.run_id == run_id, GraphCheckpoint.namespace == namespace
        )
        identifier = config["configurable"].get("checkpoint_id")
        if identifier:
            statement = statement.where(GraphCheckpoint.checkpoint_id == identifier)
        with self.database.sessions() as session:
            row = session.scalar(statement.order_by(GraphCheckpoint.checkpoint_id.desc()).limit(1))
            if not row:
                return None
            writes = session.scalars(
                select(GraphWrite)
                .where(
                    GraphWrite.run_id == run_id,
                    GraphWrite.namespace == namespace,
                    GraphWrite.checkpoint_id == row.checkpoint_id,
                )
                .order_by(GraphWrite.task_id, GraphWrite.position)
            ).all()
            return CheckpointTuple(
                config=self.config(row.checkpoint_id),
                checkpoint=self.serde.loads_typed((row.payload_type, row.payload)),
                metadata=self.serde.loads_typed((row.metadata_type, row.metadata_payload)),
                parent_config=self.config(row.parent_id) if row.parent_id else None,
                pending_writes=[
                    (
                        item.task_id,
                        item.channel,
                        self.serde.loads_typed((item.payload_type, item.payload)),
                    )
                    for item in writes
                ],
            )

    def put(self, config, checkpoint, metadata, new_versions):
        run_id, namespace = self.scope(config)
        kind, blob = self.encoded(checkpoint)
        metadata_kind, metadata_blob = self.encoded(get_checkpoint_metadata(config, metadata))
        with self.database.sessions() as session:
            self.require_lease(session)
            session.merge(
                GraphCheckpoint(
                    run_id=run_id,
                    namespace=namespace,
                    checkpoint_id=checkpoint["id"],
                    parent_id=config["configurable"].get("checkpoint_id"),
                    payload_type=kind,
                    payload=blob,
                    metadata_type=metadata_kind,
                    metadata_payload=metadata_blob,
                )
            )
            session.commit()
        return self.config(checkpoint["id"])

    def put_writes(self, config, writes, task_id, task_path=""):
        run_id, namespace = self.scope(config)
        identifier = config["configurable"]["checkpoint_id"]
        with self.database.sessions() as session:
            self.require_lease(session)
            for number, (channel, value) in enumerate(writes):
                position = WRITES_IDX_MAP.get(channel, number)
                key = (run_id, namespace, identifier, task_id, position)
                existing = session.get(GraphWrite, key)
                if existing and position >= 0:
                    continue
                kind, blob = self.encoded(value)
                session.merge(
                    GraphWrite(
                        run_id=run_id,
                        namespace=namespace,
                        checkpoint_id=identifier,
                        task_id=task_id,
                        position=position,
                        channel=channel,
                        payload_type=kind,
                        payload=blob,
                    )
                )
            session.commit()

    def list(self, config, *, filter=None, before=None, limit=None):
        if config is None:
            config = {"configurable": {"thread_id": self.run_id}}
        self.scope(config)
        with self.database.sessions() as session:
            identifiers = session.scalars(
                select(GraphCheckpoint.checkpoint_id)
                .where(GraphCheckpoint.run_id == self.run_id)
                .order_by(GraphCheckpoint.checkpoint_id.desc())
            ).all()
        count = 0
        for identifier in identifiers:
            if before and identifier >= before["configurable"]["checkpoint_id"]:
                continue
            item = self.get_tuple(self.config(identifier))
            if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                return
            count += 1
            yield item
