"""Safe profile publication and immutable run bindings. Secret settings stay out of SQL snapshots."""

from fastapi import HTTPException
from sqlalchemy import func, select

from workbench.domain import audit, fingerprint
from workbench.models import now
from workbench.profile_models import ProfileHead, ProfileVersion, RunProfile
from workbench.profile_schemas import ProfileOptions
from workbench.quotas import acquire_admission

PROMPT_FIELDS = {"prompt_sha256", "return_prompt_sha256"}


def return_prompt_hash():
    from workbench.return_decisions import prompt_hash as decision_prompt_hash

    return decision_prompt_hash()


def profile_options(values):
    return ProfileOptions.model_validate(
        {k: v for k, v in values.items() if k not in PROMPT_FIELDS}
    )


def prompt_hash():
    from workbench.intent_router import router_messages

    return fingerprint(router_messages(""))


def binding_hash(settings):
    return fingerprint({"url": settings.agent_router_url, "model": settings.agent_router_model})


def factory_values(settings):
    return {
        **ProfileOptions(
            model_alias="server_default" if settings.agent_router_mode == "model" else "rules",
            max_output_tokens=settings.agent_router_max_output_tokens,
        ).model_dump(),
        "prompt_sha256": prompt_hash(),
        "return_prompt_sha256": return_prompt_hash(),
    }


def check_options(options, settings):
    if options.model_alias == "server_default" and settings.agent_router_mode != "model":
        raise HTTPException(422, "Server model is not enabled; configuration cannot enable it")
    if options.max_output_tokens > settings.agent_router_max_output_tokens:
        raise HTTPException(422, "Output cap exceeds server limit")
    if options.return_decisions == "model" and (
        options.model_alias != "server_default" or settings.return_decision_mode != "model"
    ):
        raise HTTPException(422, "Return model decisions are not enabled by the server")


def version_view(row):
    return {
        "id": row.id,
        "number": row.number,
        "values": row.values,
        "config_hash": row.config_hash,
        "created_at": row.created_at,
        "published_at": row.published_at,
    }


def current_profile(session, actor, settings):
    head = session.get(ProfileHead, actor.organization_id)
    row = session.get(ProfileVersion, head.version_id) if head and head.version_id else None
    if row and row.organization_id != actor.organization_id:
        raise HTTPException(409, "Profile scope mismatch")
    values = row.values if row else factory_values(settings)
    return {
        "revision": head.revision if head else 0,
        "current": version_view(row)
        if row
        else {
            "id": None,
            "number": 0,
            "values": values,
            "config_hash": fingerprint(values),
            "source": "factory",
        },
        "allowed_model_aliases": ["rules"]
        + (["server_default"] if settings.agent_router_mode == "model" else []),
        "max_output_cap": settings.agent_router_max_output_tokens,
        "return_model_available": settings.return_decision_mode == "model"
        and settings.agent_router_mode == "model",
    }


def create_version(session, actor, options, settings):
    check_options(options, settings)
    acquire_admission(session)
    head = session.get(ProfileHead, actor.organization_id)
    if not head:
        session.add(ProfileHead(organization_id=actor.organization_id, revision=0))
    number = (
        session.scalar(
            select(func.max(ProfileVersion.number)).where(
                ProfileVersion.organization_id == actor.organization_id
            )
        )
        or 0
    )
    values = {
        **options.model_dump(),
        "prompt_sha256": prompt_hash(),
        "return_prompt_sha256": return_prompt_hash(),
    }
    row = ProfileVersion(
        organization_id=actor.organization_id,
        number=number + 1,
        created_by=actor.id,
        values=values,
        config_hash=fingerprint(values),
    )
    session.add(row)
    session.flush()
    audit(session, actor, "profile.created", row.id, number=row.number, config_hash=row.config_hash)
    session.commit()
    return version_view(row)


def activate_version(session, actor, identifier, expected, settings):
    acquire_admission(session)
    head = session.get(ProfileHead, actor.organization_id)
    revision = head.revision if head else 0
    if expected != revision:
        raise HTTPException(409, "Profile revision changed; reload before publishing")
    row = session.scalar(
        select(ProfileVersion).where(
            ProfileVersion.id == identifier, ProfileVersion.organization_id == actor.organization_id
        )
    )
    if not row:
        raise HTTPException(404, "Profile not found")
    options = profile_options(row.values)
    check_options(options, settings)
    if (
        fingerprint(row.values) != row.config_hash
        or row.values.get("prompt_sha256") != prompt_hash()
        or (
            options.return_decisions == "model"
            and row.values.get("return_prompt_sha256") != return_prompt_hash()
        )
    ):
        raise HTTPException(409, "Template changed; create a fresh profile version")
    if not head:
        head = ProfileHead(organization_id=actor.organization_id, revision=0)
        session.add(head)
    head.version_id, head.revision = row.id, revision + 1
    if row.published_at is None:
        row.published_at = now()
    audit(
        session,
        actor,
        "profile.published",
        row.id,
        revision=head.revision,
        config_hash=row.config_hash,
    )
    session.commit()
    return current_profile(session, actor, settings)


def snapshot_run(session, run, actor, settings):
    # Called within the same admission/run creation transaction.
    current = current_profile(session, actor, settings)
    values = dict(current["current"]["values"])
    profile_options(values)
    if fingerprint(values) != current["current"]["config_hash"]:
        raise HTTPException(409, "Profile integrity mismatch")
    row = RunProfile(
        run_id=run.id,
        version_id=current["current"]["id"],
        values=values,
        config_hash=fingerprint(values),
        model_binding_hash=binding_hash(settings),
    )
    session.add(row)
    return row


def effective_settings(database, run_id):
    base = database.settings
    with database.sessions() as session:
        row = session.get(RunProfile, run_id)
        if not row:
            return (
                base.model_copy(
                    update={"agent_router_mode": "rules", "return_decision_mode": "rules"}
                ),
                {"top_k": 3, "tone": "plain"},
                "legacy_unrecorded",
            )
        values = dict(row.values)
        options = profile_options(values)
        if fingerprint(values) != row.config_hash:
            raise ValueError("Run profile integrity mismatch")
        reason = "snapshot"
        mode = "rules"
        return_mode = "rules"
        if options.model_alias == "server_default":
            if base.agent_router_mode != "model":
                reason = "server_model_disabled"
            elif row.model_binding_hash != binding_hash(base):
                reason = "server_model_binding_changed"
            elif values.get("prompt_sha256") != prompt_hash():
                reason = "server_template_changed"
            elif options.max_output_tokens > base.agent_router_max_output_tokens:
                reason = "server_output_cap_reduced"
            else:
                mode = "model"
        if options.return_decisions == "model" and mode == "model":
            if base.return_decision_mode != "model":
                reason = "return_model_disabled"
            elif values.get("return_prompt_sha256") != return_prompt_hash():
                reason = "return_template_changed"
            else:
                return_mode = "model"
        return (
            base.model_copy(
                update={
                    "agent_router_mode": mode,
                    "return_decision_mode": return_mode,
                    "agent_router_max_output_tokens": min(
                        options.max_output_tokens, base.agent_router_max_output_tokens
                    ),
                }
            ),
            values,
            reason,
        )


def run_profile_view(session, run):
    row = session.get(RunProfile, run.id)
    if not row:
        return {
            "source": "legacy_unrecorded",
            "version_id": None,
            "values": None,
            "config_hash": None,
        }
    version = session.get(ProfileVersion, row.version_id) if row.version_id else None
    return {
        "source": "version" if row.version_id else "factory",
        "version_number": version.number if version else 0,
        "version_id": row.version_id,
        "values": row.values,
        "config_hash": row.config_hash,
        "created_at": row.created_at,
    }


def render_answer(answer, values):
    return ("您好，" + answer) if values.get("tone") == "friendly" else answer
