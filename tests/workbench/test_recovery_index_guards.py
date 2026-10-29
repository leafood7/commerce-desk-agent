"""Recovery writes fail closed before any indexing against ambiguous targets."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import rebuild_recovery_index as recovery
from workbench.config import WorkbenchSettings

TARGET = "commercedesk_verify_012345abcdef"


def settings(database=TARGET, chroma="http://restore-chroma:8000"):
    return WorkbenchSettings(
        database_url=f"mysql+pymysql://recovery:test@mysql:3306/{database}",
        chroma_url=chroma,
    )


@pytest.mark.parametrize(
    "expected,configured,chroma",
    [
        ("commercedesk", "commercedesk", "http://restore-chroma:8000"),
        (TARGET, "commercedesk", "http://restore-chroma:8000"),
        (TARGET, TARGET, "http://chroma:8000"),
        ("commercedesk_verify_bad;sql", TARGET, "http://restore-chroma:8000"),
    ],
)
def test_wrong_target_rejected_before_connection(expected, configured, chroma, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Wrong target must be rejected before database/index connection")

    monkeypatch.setattr(recovery, "Database", forbidden)
    monkeypatch.setattr(recovery, "ChromaIndex", forbidden)
    with pytest.raises(ValueError):
        recovery.rebuild(settings(configured, chroma), expected)


def test_nonempty_index_is_preserved_and_database_closed(monkeypatch):
    closed = []
    database = SimpleNamespace(close=lambda: closed.append(True))
    index = SimpleNamespace(client=SimpleNamespace(list_collections=lambda: ["prior-index"]))
    monkeypatch.setattr(recovery, "Database", lambda s: database)
    monkeypatch.setattr(recovery, "ChromaIndex", lambda s: index)
    with pytest.raises(ValueError, match="not empty"):
        recovery.rebuild(settings(), TARGET)
    assert closed == [True]


def test_matching_isolated_target_is_accepted():
    recovery.require_recovery(settings(), TARGET)


def test_orchestrator_rejects_outside_directory_before_creating_it(tmp_path):
    target = tmp_path / "must-not-create"
    script = Path(recovery.__file__).with_name("verify_full_recovery.py")
    result = subprocess.run(
        [sys.executable, str(script), "--directory", str(target)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "must be a child of project work/recovery" in result.stderr
    assert not target.exists()
