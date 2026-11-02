"""Publication impact previews and durable, source-bound maintenance worklists."""

from difflib import SequenceMatcher

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import func, select

from workbench.domain import audit, fingerprint
from workbench.knowledge_models import (
    Document,
    DocumentVersion,
    KnowledgeChunk,
    KnowledgeUpdate,
    KnowledgeUpdateTask,
    PolicyExtraction,
)
from workbench.knowledge_revisions import publication_lock, reviewable_blocks
from workbench.knowledge_schemas import ReasonedInput, VersionOptions
from workbench.models import Run, identifier, now
from workbench.policy_audience import PolicyAudience
from workbench.return_models import ReturnPolicy

OPEN = ("pending", "prepared")


class MaintenanceAction(ReasonedInput):
    expected_revision: int = Field(ge=1)
    replacement_id: str | None = Field(default=None, min_length=1, max_length=80)


class FaqPrepare(MaintenanceAction):
    content: str = Field(min_length=1, max_length=80000)
    audience: PolicyAudience | None = None
    binding_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def source_bindings(session, organization_id, references):
    bindings = []
    for ref in references:
        version = session.get(DocumentVersion, ref)
        if not version or version.organization_id != organization_id:
            raise HTTPException(409, "FAQ来源不可用，请重新选择有效政策")
        document = session.get(Document, version.document_id)
        current = (
            session.get(DocumentVersion, document.active_version_id)
            if document.active_version_id
            else None
        )
        if (
            not current
            or current.status != "ready"
            or (current.parse_metadata or {}).get("knowledge_kind") == "faq"
        ):
            raise HTTPException(409, "来源已撤回或尚未就绪，当前不能准备替代FAQ")
        today = now().date()
        if (current.effective_from and current.effective_from > today) or (
            current.effective_until and current.effective_until < today
        ):
            raise HTTPException(409, "来源尚未生效或已到期，请先处理政策版本")
        bindings.append(
            {
                "document_id": document.id,
                "version_id": current.id,
                "annotation_id": (current.parse_metadata or {}).get("policy_extraction_id"),
            }
        )
    return list({b["document_id"]: b for b in bindings}.values())


def affected(session, actor, document_id):
    versions = session.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.organization_id == actor.organization_id,
        )
    ).all()
    ids = {v.id for v in versions}
    faqs = []
    active = session.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, Document.active_version_id == DocumentVersion.id)
        .where(
            Document.organization_id == actor.organization_id,
            DocumentVersion.organization_id == actor.organization_id,
            DocumentVersion.parse_metadata["knowledge_kind"].as_string() == "faq",
        )
    ).all()
    for doc, version in active:
        if ids & set((version.parse_metadata or {}).get("faq_sources", [])):
            faqs.append(
                {
                    "kind": "faq",
                    "id": version.id,
                    "document_id": doc.id,
                    "title": doc.title,
                    "version": version.number,
                }
            )
    return versions, faqs


def preview(session, actor, version_id, candidate_id=None):
    from workbench.knowledge_service import scoped_document, scoped_version

    version = scoped_version(session, actor, version_id)
    document = scoped_document(session, actor, version.document_id)
    previous = (
        session.get(DocumentVersion, document.active_version_id)
        if document.active_version_id
        else None
    )
    if candidate_id:
        candidate = session.get(PolicyExtraction, candidate_id)
        if (
            not candidate
            or candidate.organization_id != actor.organization_id
            or candidate.version_id != version.id
        ):
            raise HTTPException(404, "候选不属于该政策版本")
    versions, faqs = affected(session, actor, document.id)
    annotations = []
    for row in session.scalars(
        select(PolicyExtraction).where(
            PolicyExtraction.organization_id == actor.organization_id,
            PolicyExtraction.version_id.in_([v.id for v in versions]),
            PolicyExtraction.status.in_(["draft", "pending", "approved", "published"]),
        )
    ):
        if row.id == candidate_id:
            continue
        if previous and (
            (
                row.status == "published"
                and row.id == (previous.parse_metadata or {}).get("policy_extraction_id")
            )
            or (
                row.status in ("draft", "pending", "approved") and row.base_active_id == previous.id
            )
        ):
            annotations.append({"id": row.id, "status": row.status, "version_id": row.version_id})
    rules = session.scalars(
        select(ReturnPolicy).where(
            ReturnPolicy.organization_id == actor.organization_id,
            ReturnPolicy.version_id == previous.id if previous else ReturnPolicy.id == "",
        )
    ).all()
    old_rows = (
        session.scalars(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.version_id == previous.id)
            .order_by(KnowledgeChunk.position)
            .limit(201)
        ).all()
        if previous
        else []
    )
    new_rows = session.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.version_id == version.id)
        .order_by(KnowledgeChunk.position)
        .limit(201)
    ).all()
    old = [r.content for r in old_rows[:200]]
    new = [r.content for r in new_rows[:200]]
    changes = []
    for tag, a, b, c, d in SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        changes.append({"kind": tag, "before": old[a:b], "after": new[c:d]})
    bound_runs = (
        session.scalar(
            select(func.count())
            .select_from(Run)
            .where(
                Run.organization_id == actor.organization_id,
                Run.status.in_(
                    [
                        "awaiting_input",
                        "awaiting_confirmation",
                        "awaiting_approval",
                        "interrupted",
                        "queued",
                        "running",
                    ]
                ),
                Run.input["return_policy_id"].as_string().in_([r.id for r in rules]),
            )
        )
        if rules
        else 0
    )
    return {
        "document_id": document.id,
        "title": document.title,
        "previous_version_id": document.active_version_id,
        "target_version_id": version.id,
        "same_source_version": document.active_version_id == version.id,
        "faqs": faqs,
        "policy_candidates": annotations,
        "return_rules": [
            {
                "id": r.id,
                "skus": r.rules.get("skus", []),
                "basis": r.rules.get("return_basis", "no_reason"),
            }
            for r in rules
        ],
        "bound_open_runs": bound_runs,
        "run_count_scope": "only_runs_explicitly_bound_to_listed_return_rules",
        "changes": changes[:60],
        "diff_truncated": len(old_rows) > 200 or len(new_rows) > 200 or len(changes) > 60,
        "annotation_update_only": bool(candidate_id and previous and previous.id == version.id),
        "automatic_business_migration": False,
    }


def record_update(
    session, actor, document, previous, current, action, *, old_annotation=None, new_annotation=None
):
    """Inside the existing organization publication transaction; never commit here."""
    version = session.get(DocumentVersion, current or previous) if (current or previous) else None
    if not version or (version.parse_metadata or {}).get("knowledge_kind") == "faq":
        return
    annotation_changed = (
        previous == current and old_annotation != new_annotation and new_annotation is not None
    )
    if previous == current and not annotation_changed:
        return
    versions, faqs = affected(session, actor, document.id)
    impact_previous = previous
    if impact_previous is None:
        withdrawal = session.scalar(
            select(KnowledgeUpdate)
            .where(
                KnowledgeUpdate.organization_id == actor.organization_id,
                KnowledgeUpdate.document_id == document.id,
                KnowledgeUpdate.action == "withdraw",
            )
            .order_by(KnowledgeUpdate.created_at.desc(), KnowledgeUpdate.id.desc())
            .limit(1)
        )
        impact_previous = withdrawal.previous_version_id if withdrawal else None
    impact_version = session.get(DocumentVersion, impact_previous) if impact_previous else None
    impact_pointer = (
        (impact_version.parse_metadata or {}).get("policy_extraction_id")
        if impact_version
        else None
    )
    root = KnowledgeUpdate(
        id=identifier(),
        organization_id=actor.organization_id,
        document_id=document.id,
        previous_version_id=previous,
        current_version_id=current,
        action=action,
        created_by=actor.id,
        details={
            "old_annotation_id": old_annotation,
            "new_annotation_id": new_annotation,
            "business_bindings_migrated": False,
        },
    )
    session.add(root)
    session.flush()
    replacement_annotation = (
        session.get(PolicyExtraction, new_annotation) if new_annotation else None
    )
    if (
        replacement_annotation is None
        or replacement_annotation.organization_id != actor.organization_id
        or replacement_annotation.version_id != current
        or replacement_annotation.status != "published"
    ):
        new_annotation = None
    specs = [("faq", r["id"], None) for r in faqs]
    if annotation_changed:
        for faq in session.scalars(
            select(DocumentVersion)
            .where(
                DocumentVersion.organization_id == actor.organization_id,
                DocumentVersion.parse_metadata["knowledge_kind"].as_string() == "faq",
            )
            .with_for_update()
        ):
            metadata = dict(faq.parse_metadata or {})
            if previous in metadata.get("faq_sources", []) and not metadata.get(
                "source_invalidated"
            ):
                metadata["source_invalidated"] = {
                    "document_id": document.id,
                    "previous_version_id": previous,
                    "current_version_id": current,
                    "annotation_changed": True,
                    "at": now().isoformat(),
                }
                faq.parse_metadata = metadata
    if previous != current:
        for candidate in session.scalars(
            select(PolicyExtraction).where(
                PolicyExtraction.organization_id == actor.organization_id,
                PolicyExtraction.version_id.in_([v.id for v in versions]),
                PolicyExtraction.status.in_(["draft", "pending", "approved", "published"]),
            )
        ):
            relevant = (
                candidate.status in ("draft", "pending", "approved")
                and candidate.base_active_id != current
            ) or (
                candidate.status == "published"
                and candidate.id == impact_pointer
                and candidate.version_id == impact_previous
                and candidate.version_id != current
            )
            if relevant:
                specs.append(
                    (
                        "extraction",
                        candidate.id,
                        current if candidate.status == "published" else candidate.version_id,
                    )
                )
        rules = session.scalars(
            select(ReturnPolicy).where(
                ReturnPolicy.organization_id == actor.organization_id,
                ReturnPolicy.version_id == impact_previous,
            )
        ).all()
        specs.extend(("return_rule", r.id, current) for r in rules)
    for kind, resource_id, target in dict.fromkeys(specs):
        for old in session.scalars(
            select(KnowledgeUpdateTask)
            .where(
                KnowledgeUpdateTask.organization_id == actor.organization_id,
                KnowledgeUpdateTask.resource_kind == kind,
                KnowledgeUpdateTask.resource_id == resource_id,
                KnowledgeUpdateTask.status.in_(OPEN),
            )
            .with_for_update()
        ):
            old.status = "superseded"
            old.revision += 1
            old.updated_at = now()
        task = KnowledgeUpdateTask(
            organization_id=actor.organization_id,
            update_id=root.id,
            resource_kind=kind,
            resource_id=resource_id,
            target_version_id=target,
            updated_by=actor.id,
        )
        if kind == "extraction" and new_annotation and current and target == current:
            task.status = "completed"
            task.replacement_id = new_annotation
            task.note = "新版本已有独立审核并发布的整理"
        session.add(task)
    audit(
        session,
        actor,
        "knowledge.update_recorded",
        root.id,
        action_type=action,
        affected_resources=len(specs),
    )


def scoped_task(session, actor, task_id):
    task = session.scalar(
        select(KnowledgeUpdateTask)
        .where(
            KnowledgeUpdateTask.id == task_id,
            KnowledgeUpdateTask.organization_id == actor.organization_id,
        )
        .with_for_update()
    )
    if not task:
        raise HTTPException(404, "知识维护待办不存在")
    return task


def task_view(session, task):
    root = session.get(KnowledgeUpdate, task.update_id)
    document = session.get(Document, root.document_id)
    title = ""
    version = None
    if task.resource_kind == "faq":
        version = session.get(DocumentVersion, task.resource_id)
        if version:
            title = session.get(Document, version.document_id).title
    elif task.resource_kind == "extraction":
        candidate = session.get(PolicyExtraction, task.resource_id)
        version = session.get(DocumentVersion, candidate.version_id) if candidate else None
        title = "政策审核整理"
    else:
        rule = session.get(ReturnPolicy, task.resource_id)
        title = "退货规则 · " + (" / ".join(rule.rules.get("skus", [])) if rule else "")
    return {
        "id": task.id,
        "update_id": task.update_id,
        "resource_kind": task.resource_kind,
        "resource_id": task.resource_id,
        "title": title,
        "status": task.status,
        "revision": task.revision,
        "target_version_id": task.target_version_id,
        "replacement_id": task.replacement_id,
        "note": task.note,
        "source_title": document.title,
        "current_source_version_id": document.active_version_id,
        "updated_at": task.updated_at,
        "source_version_id": version.id if version else None,
    }


def editor(session, actor, task_id, settings):
    task = scoped_task(session, actor, task_id)
    if task.resource_kind != "faq":
        raise HTTPException(422, "该待办不是FAQ更新")
    version = session.get(DocumentVersion, task.resource_id)
    blocks, _ = reviewable_blocks(session, version, settings)
    bindings = source_bindings(
        session, actor.organization_id, (version.parse_metadata or {}).get("faq_sources", [])
    )
    content = "\n\n".join(b.text for b in blocks)
    if len(content) > 80000:
        raise HTTPException(413, "FAQ过长，请在知识库中人工分段维护")
    from workbench.knowledge_service import version_view

    prepared = (
        session.get(DocumentVersion, task.replacement_id)
        if task.status == "prepared" and task.replacement_id
        else None
    )
    original_content = content
    if prepared:
        prepared_blocks, _ = reviewable_blocks(session, prepared, settings)
        content = "\n\n".join(b.text for b in prepared_blocks)
    source_details = []
    for binding in bindings:
        current = session.get(DocumentVersion, binding["version_id"])
        doc = session.get(Document, current.document_id)
        source_details.append(
            {
                "id": current.id,
                "title": doc.title,
                "number": current.number,
                "audience": (current.parse_metadata or {}).get("audience", {}),
            }
        )
    return {
        "task": task_view(session, task),
        "original_content": original_content,
        "prepared_version": version_view(
            session, session.get(Document, prepared.document_id), prepared
        )
        if prepared
        else None,
        "source_details": source_details,
        "content": content,
        "source_bindings": bindings,
        "binding_hash": fingerprint(bindings),
        "audience": ((prepared or version).parse_metadata or {}).get("audience", {}),
        "warning": "这是原FAQ正文，尚未按新政策修订；请对照当前来源修改后提交独立审核。",
    }


def check_revision(task, body):
    if task.revision != body.expected_revision or task.status not in OPEN:
        raise HTTPException(409, "待办已变化，请刷新后重新操作")


def prepare_faq(session, actor, task_id, body, settings):
    from workbench.knowledge_service import create_version, stored_file, version_view

    publication_lock(session, actor)
    task = scoped_task(session, actor, task_id)
    check_revision(task, body)
    if task.resource_kind != "faq":
        raise HTTPException(422, "该待办不能生成FAQ")
    original = session.get(DocumentVersion, task.resource_id)
    bindings = source_bindings(
        session, actor.organization_id, (original.parse_metadata or {}).get("faq_sources", [])
    )
    if fingerprint(bindings) != body.binding_hash:
        raise HTTPException(409, "政策来源已变化，请刷新编辑器后重新核对")
    if not body.content.strip():
        raise HTTPException(422, "FAQ正文不能为空")
    if task.status == "prepared":
        existing = session.get(DocumentVersion, task.replacement_id)
        if (
            existing
            and (existing.parse_metadata or {}).get("faq_update_review", {}).get("status")
            != "rejected"
        ):
            raise HTTPException(409, "已生成待审核FAQ，请先完成审核或驳回后重建")
    review = {
        "task_id": task.id,
        "status": "pending",
        "proposed_by": actor.id,
        "source_bindings": bindings,
        "reason": body.reason,
        "task_revision": task.revision,
    }
    options = VersionOptions(
        policy_scope=original.policy_scope,
        chunk_strategy="parent_child",
        chunk_chars=original.chunk_chars,
        chunk_overlap=original.chunk_overlap,
        knowledge_kind="faq",
        faq_sources=[b["version_id"] for b in bindings],
        audience=body.audience
        if body.audience is not None
        else (original.parse_metadata or {}).get("audience") or None,
    )
    document, version, created = create_version(
        session,
        actor,
        settings,
        "faq-update.md",
        body.content.encode(),
        options,
        document_id=original.document_id,
        faq_review=review,
        commit=False,
    )
    task.status = "prepared"
    task.replacement_id = version.id
    task.note = body.reason
    task.revision += 1
    task.updated_by = actor.id
    task.updated_at = now()
    audit(session, actor, "knowledge.faq_update_prepared", task.id, version_id=version.id)
    try:
        session.commit()
    except Exception:
        session.rollback()
        if created:
            stored_file(settings, version.storage_key).unlink(missing_ok=True)
        raise
    return {"task": task_view(session, task), "version": version_view(session, document, version)}


def prepare_extraction(session, actor, task_id, body, settings):
    from workbench.policy_extraction import create

    publication_lock(session, actor)
    task = scoped_task(session, actor, task_id)
    check_revision(task, body)
    if task.resource_kind != "extraction":
        raise HTTPException(422, "该待办不是政策整理更新")
    if not task.target_version_id:
        raise HTTPException(409, "来源已撤回，请先发布可用政策")
    target = task.target_version_id
    # create owns a publication transaction; no task mutation is pending here.
    result = create(session, actor, target, settings)
    publication_lock(session, actor)
    task = scoped_task(session, actor, task_id)
    check_revision(task, body)
    task.status = "prepared"
    task.replacement_id = result["id"]
    task.revision += 1
    task.note = body.reason
    task.updated_by = actor.id
    task.updated_at = now()
    audit(session, actor, "knowledge.extraction_prepared", task.id, candidate_id=result["id"])
    session.commit()
    return {"task": task_view(session, task), "candidate": result}


def faq_publication_ready(session, actor, version):
    metadata = version.parse_metadata or {}
    review = metadata.get("faq_update_review")
    if not review:
        return
    task = scoped_task(session, actor, review["task_id"])
    if (
        review.get("status") != "approved"
        or task.status not in ("prepared", "completed")
        or task.replacement_id != version.id
    ):
        raise HTTPException(409, "联动FAQ需要独立审核，且待办仍然有效才能发布")


def finish_published(session, actor, version, candidate=None):
    marker = (version.parse_metadata or {}).get("faq_update_review")
    if marker:
        task = scoped_task(session, actor, marker["task_id"])
        if task.status == "prepared" and task.replacement_id == version.id:
            task.status = "completed"
            task.revision += 1
            task.updated_at = now()
            task.updated_by = actor.id
            audit(
                session, actor, "knowledge.followup_completed", task.id, replacement_id=version.id
            )
    if candidate:
        for task in session.scalars(
            select(KnowledgeUpdateTask)
            .where(
                KnowledgeUpdateTask.organization_id == actor.organization_id,
                KnowledgeUpdateTask.resource_kind == "extraction",
                KnowledgeUpdateTask.replacement_id == candidate.id,
                KnowledgeUpdateTask.status == "prepared",
            )
            .with_for_update()
        ):
            task.status = "completed"
            task.revision += 1
            task.updated_at = now()
            task.updated_by = actor.id
            audit(
                session, actor, "knowledge.followup_completed", task.id, replacement_id=candidate.id
            )


def review_faq(session, actor, version_id, body):
    from workbench.faq_sources import check_faq
    from workbench.knowledge_service import scoped_document, scoped_version, version_view

    publication_lock(session, actor)
    version = scoped_version(session, actor, version_id)
    document = scoped_document(session, actor, version.document_id, lock=True)
    metadata = dict(version.parse_metadata or {})
    review = dict(metadata.get("faq_update_review") or {})
    if not review or review.get("proposed_by") == actor.id:
        raise HTTPException(403, "需要独立审批员核对FAQ")
    if (
        version.status != "ready"
        or review.get("status") != "pending"
        or version.config_hash != body.config_hash
    ):
        raise HTTPException(409, "FAQ尚未就绪或审核状态已变化")
    task = scoped_task(session, actor, review["task_id"])
    if task.status != "prepared" or task.replacement_id != version.id:
        raise HTTPException(409, "该FAQ更新待办已失效")
    check_faq(session, actor.organization_id, metadata)
    review.update(
        status="approved" if body.action == "approve" else "rejected",
        reviewed_by=actor.id,
        reviewed_at=now().isoformat(),
        review_note=body.reason,
    )
    metadata["faq_update_review"] = review
    version.parse_metadata = metadata
    audit(
        session,
        actor,
        "knowledge.faq_update_reviewed",
        version.id,
        decision=body.action,
        reason=body.reason,
    )
    session.commit()
    return version_view(session, document, version)


def resolve_rule(session, actor, task_id, body):
    from workbench.returns import policy_evidence

    publication_lock(session, actor)
    task = scoped_task(session, actor, task_id)
    check_revision(task, body)
    if task.resource_kind != "return_rule" or not body.replacement_id:
        raise HTTPException(422, "请选择已重新配置的退货规则")
    rule = session.get(ReturnPolicy, body.replacement_id)
    root = session.get(KnowledgeUpdate, task.update_id)
    doc = session.get(Document, root.document_id)
    if (
        not rule
        or rule.organization_id != actor.organization_id
        or rule.version_id != doc.active_version_id
    ):
        raise HTTPException(409, "替代规则必须来自当前有效政策")
    original = session.get(ReturnPolicy, task.resource_id)
    if (
        original is None
        or original.organization_id != actor.organization_id
        or not set(original.rules.get("skus", [])) <= set(rule.rules.get("skus", []))
        or original.rules.get("return_basis", "no_reason")
        != rule.rules.get("return_basis", "no_reason")
    ):
        raise HTTPException(
            409, "替代规则必须覆盖原商品且属于同一退货分支；不再适用时请明确记录不再维护"
        )
    policy_evidence(session, actor, rule, lock=True)
    task.status = "completed"
    task.replacement_id = rule.id
    task.revision += 1
    task.note = body.reason
    task.updated_by = actor.id
    task.updated_at = now()
    audit(
        session,
        actor,
        "knowledge.rule_replacement_reviewed",
        task.id,
        replacement_id=rule.id,
        reason=body.reason,
    )
    session.commit()
    return task_view(session, task)


def dismiss(session, actor, task_id, body):
    publication_lock(session, actor)
    task = scoped_task(session, actor, task_id)
    check_revision(task, body)
    task.status = "dismissed"
    task.note = body.reason
    task.revision += 1
    task.updated_by = actor.id
    task.updated_at = now()
    audit(session, actor, "knowledge.followup_dismissed", task.id, reason=body.reason)
    session.commit()
    return task_view(session, task)
