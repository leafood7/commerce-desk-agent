from fastapi import Depends, Query
from sqlalchemy import select

from workbench.profile_models import ProfileHead, ProfileVersion
from workbench.profile_schemas import ActivateProfile, ProfileOptions
from workbench.profiles import activate_version, create_version, current_profile, version_view


def attach_profile_routes(app, settings, session, actor, require):
    @app.get("/api/v1/runtime/config")
    def current(user=Depends(actor), connection=Depends(session)):
        return current_profile(connection, user, settings)

    @app.get("/api/v1/runtime/config/versions")
    def versions(
        limit: int = Query(20, ge=1, le=100),
        user=Depends(require("admin")),
        connection=Depends(session),
    ):
        rows = connection.scalars(
            select(ProfileVersion)
            .where(ProfileVersion.organization_id == user.organization_id)
            .order_by(ProfileVersion.number.desc())
            .limit(limit)
        ).all()
        head = connection.get(ProfileHead, user.organization_id)
        return {
            "items": [version_view(row) for row in rows],
            "revision": head.revision if head else 0,
            "current_id": head.version_id if head else None,
        }

    @app.post("/api/v1/runtime/config/versions", status_code=201)
    def draft(body: ProfileOptions, user=Depends(require("admin")), connection=Depends(session)):
        return create_version(connection, user, body, settings)

    @app.post("/api/v1/runtime/config/versions/{identifier}/publish")
    def publish(
        identifier: str,
        body: ActivateProfile,
        user=Depends(require("admin")),
        connection=Depends(session),
    ):
        return activate_version(connection, user, identifier, body.expected_revision, settings)
