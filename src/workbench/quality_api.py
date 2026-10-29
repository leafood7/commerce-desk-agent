from fastapi import Depends, Query
from sqlalchemy import select

from workbench import quality
from workbench.quality_schemas import QualityChange, QualityCreate, QualityLookup
from workbench.return_models import QualityEvent, QualityInspection


def attach_quality_routes(app, session_dependency, actor_dependency, require, key_dependency):
    @app.post("/api/v1/quality/inspections", status_code=201)
    def create(
        body: QualityCreate,
        user=Depends(require("support")),
        session=Depends(session_dependency),
        key=Depends(key_dependency),
    ):
        return quality.view(quality.create(session, user, body, key))

    @app.post("/api/v1/quality/lookup")
    def lookup(
        body: QualityLookup, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        return quality.lookup(session, user, body)

    @app.get("/api/v1/quality/inspections")
    def listing(
        limit: int = Query(50, ge=1, le=100),
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        rows = session.scalars(
            select(QualityInspection)
            .where(QualityInspection.organization_id == user.organization_id)
            .order_by(QualityInspection.created_at.desc(), QualityInspection.id.desc())
            .limit(limit)
        ).all()
        return {"items": [quality.view(row) for row in rows]}

    @app.get("/api/v1/quality/inspections/{identifier}")
    def detail(
        identifier: str, user=Depends(actor_dependency), session=Depends(session_dependency)
    ):
        row = quality.owned(session, user, identifier)
        events = session.scalars(
            select(QualityEvent)
            .where(
                QualityEvent.inspection_id == row.id,
                QualityEvent.organization_id == user.organization_id,
            )
            .order_by(QualityEvent.created_at, QualityEvent.id)
        ).all()
        return {
            **quality.view(row),
            "events": [
                {
                    "action": e.action,
                    "revision": e.revision,
                    "details": e.details,
                    "created_at": e.created_at.isoformat(),
                }
                for e in events
            ],
        }

    @app.post("/api/v1/quality/inspections/{identifier}/transition")
    def transition(
        identifier: str,
        body: QualityChange,
        user=Depends(actor_dependency),
        session=Depends(session_dependency),
    ):
        return quality.view(quality.change(session, user, identifier, body))
