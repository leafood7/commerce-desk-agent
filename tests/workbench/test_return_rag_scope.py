"""A version filter narrows access; it never authorizes a private/withdrawn source."""

import pytest
from helpers import MemoryIndex
from sqlalchemy import select
from test_returns import return_case  # noqa: F401

from workbench.knowledge_models import Document, DocumentVersion
from workbench.knowledge_schemas import SearchInput
from workbench.models import User
from workbench.retrieval import KnowledgeRetriever


def test_return_version_filter_honors_org_publication_and_expiry(return_case):  # noqa: F811
    client, app, headers, body, policy = return_case
    retriever = KnowledgeRetriever(app.state.database, app.state.settings, MemoryIndex())
    with app.state.database.sessions() as session:
        version = session.get(DocumentVersion, policy["version_id"])
        org = version.organization_id
        foreign = session.scalar(select(User).where(User.organization_id != org)).organization_id
    request = SearchInput(
        query=policy["window_clause"]["quote"], version_id=policy["version_id"], strategy="hybrid"
    )
    assert retriever.search(org, request)["matches"]
    assert retriever.search(foreign, request)["matches"] == []
    with app.state.database.sessions() as session:
        version = session.get(DocumentVersion, policy["version_id"])
        document = session.get(Document, version.document_id)
        document.active_version_id = None
        session.commit()
    assert retriever.search(org, request)["matches"] == []
    from datetime import timedelta

    from workbench.models import now

    with app.state.database.sessions() as session:
        version = session.get(DocumentVersion, policy["version_id"])
        session.get(Document, version.document_id).active_version_id = version.id
        version.effective_until = now().date() - timedelta(days=1)
        session.commit()
    assert retriever.search(org, request)["matches"] == []


def test_invalid_rag_case_fails_before_creating_index(tmp_path):
    import subprocess
    import sys

    target = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/evaluate_return_rag.py",
            "run",
            "--case-id",
            "INVALID",
            "--output",
            str(target),
        ],
        capture_output=True,
        timeout=20,
    )
    assert result.returncode != 0 and b"Invalid or duplicate case IDs" in result.stderr
    assert not target.exists()


def test_rag_frozen_metrics_do_not_treat_citations_as_answer_correctness():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "return_eval", Path("scripts/evaluate_return_rag.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _, data = module.load_dataset(module.DEFAULT_DATA)
    assert len(data["cases"]) == 24
    with pytest.raises(ValueError):
        module.load_dataset(module.DEFAULT_DATA, ["return-dev-001", "return-dev-001"])
    case = {
        "required_text_groups": [["required exception"]],
        "allowed_document_ids": ["correct"],
        "answerable": True,
    }
    metric = module.metrics(case, [{"content": "irrelevant clause", "version_id": "correct"}])
    assert metric["source_hit"] is True and metric["necessary_clause_coverage"] == 0
