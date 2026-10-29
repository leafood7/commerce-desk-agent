"""Host-side acceptance backup drill. Never restore over an existing database.

Private SQL/files stay under ignored work/backups; manifests contain hashes and counts.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "commercedesk"


def identifier(value):
    if not re.fullmatch(r"[a-zA-Z0-9_]+", value):
        raise ValueError("Unsafe SQL identifier")
    return "`" + value + "`"


class AcceptanceDatabase:
    def __init__(self):
        environment = ROOT / "work/docker/acceptance.env"
        if not environment.is_file():
            raise RuntimeError("Private acceptance environment is missing")
        self.compose = [
            r"D:\Docker\app\resources\bin\docker.exe",
            "--host",
            "npipe:////./pipe/dockerDesktopLinuxEngine",
            "compose",
            "--project-name",
            "commercedesk-acceptance",
            "--env-file",
            str(environment),
            "-f",
            str(ROOT / "compose.yaml"),
            "-f",
            str(ROOT / "compose.acceptance.yaml"),
        ]
        self.env = {**os.environ, "DOCKER_CONFIG": r"D:\Docker\config"}

    def run(self, command, data=None):
        result = subprocess.run(
            [*self.compose, *command],
            input=data,
            capture_output=True,
            timeout=180,
            env=self.env,
            cwd=ROOT,
            check=False,
        )
        if result.returncode:
            # SQL and private environment must never be printed in a failure.
            raise RuntimeError("Container backup utility failed; exit=" + str(result.returncode))
        return result.stdout

    def sql(self, query, database=None):
        command = 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root --batch --skip-column-names --default-character-set=utf8mb4'
        if database:
            identifier(database)
            command += " " + database
        return self.run(["exec", "-T", "mysql", "sh", "-c", command], query.encode("utf-8")).decode(
            "utf-8"
        )

    def snapshot(self, database):
        safe = identifier(database)
        tables = self.sql(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA='"
            + database
            + "' AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME;"
        ).splitlines()
        counts = {}
        checksums = {}
        for table in tables:
            name = safe + "." + identifier(table)
            counts[table] = int(self.sql("SELECT COUNT(*) FROM " + name + ";").strip())
            checksum = self.sql("CHECKSUM TABLE " + name + " EXTENDED;").strip().split("\t")[-1]
            if checksum == "NULL":
                raise RuntimeError("Logical checksum unavailable")
            checksums[table] = checksum
        return {"counts": counts, "checksums": checksums}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-new-database",
        action="store_true",
        help="Create a unique empty verification database, never replace or drop one",
    )
    args = parser.parse_args()
    client = AcceptanceDatabase()
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    target = ROOT / "work/backups" / suffix
    target.mkdir(parents=True, exist_ok=False)
    before = client.snapshot(SOURCE)
    dump = client.run(
        [
            "exec",
            "-T",
            "mysql",
            "sh",
            "-c",
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump --user=root --single-transaction '
            "--quick --hex-blob --skip-add-drop-table --skip-add-locks --no-tablespaces "
            "--set-gtid-purged=OFF --default-character-set=utf8mb4 " + SOURCE,
        ]
    )
    (target / "database.sql").write_bytes(dump)
    client.run(["cp", "api:/app/data/workbench/files", str(target / "documents")])
    after = client.snapshot(SOURCE)
    if before != after:
        raise RuntimeError("Source changed during the drill; backup retained but not accepted")
    rows = client.sql(
        "SELECT storage_key,content_hash FROM wb_document_versions;", SOURCE
    ).splitlines()
    verified_files = 0
    for row in rows:
        key, expected = row.split("\t")
        path = (target / "documents" / key).resolve()
        if not path.is_relative_to((target / "documents").resolve()):
            raise RuntimeError("Document key escapes the backup directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Document content hash differs from database")
        verified_files += 1
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "source": SOURCE,
        "sql_sha256": hashlib.sha256(dump).hexdigest(),
        "sql_bytes": len(dump),
        "logical_snapshot": before,
        "document_versions_verified": verified_files,
        "source_stable_before_after": True,
        "restore_verified": False,
        "limits": [
            "Local acceptance data only; no offsite or encrypted backup.",
            "Vector store and model weights excluded; fresh vector rebuild not validated.",
            "This drill does not cut over the application or resume a restored workflow.",
        ],
    }
    if args.verify_new_database:
        restored = "commercedesk_verify_" + uuid4().hex[:12]
        safe = identifier(restored)
        # CREATE without IF NOT EXISTS fails closed on any collision. No DROP or overwrite.
        client.sql("CREATE DATABASE " + safe + " CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;")
        if (
            hashlib.sha256((target / "database.sql").read_bytes()).hexdigest()
            != manifest["sql_sha256"]
        ):
            raise RuntimeError("Backup hash changed before import")
        command = (
            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --user=root --default-character-set=utf8mb4 '
            + restored
        )
        client.run(["exec", "-T", "mysql", "sh", "-c", command], dump)
        restored_snapshot = client.snapshot(restored)
        if restored_snapshot != before:
            raise RuntimeError("Restored logical data differs; verification database retained")
        manifest.update(
            restore_verified=True,
            verification_database=restored,
            restored_table_count=len(restored_snapshot["counts"]),
        )
        restored_files = client.sql(
            "SELECT storage_key,content_hash FROM wb_document_versions;", restored
        ).splitlines()
        if sorted(restored_files) != sorted(rows):
            raise RuntimeError("Restored document references differ")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"backup_path": str(target), **manifest}, indent=2))


if __name__ == "__main__":
    main()
