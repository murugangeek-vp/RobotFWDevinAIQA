"""MIG-08: tamper-evident migration evidence.

One JSON report per run: run id, operator, git commit, SHA-256 of the manifest and
every contract, S3 object lineage, and every check per table/relationship. A sidecar
``.sha256`` lets reviewers verify the report was not edited after the run. Values in
the report are already masked by the caller; this module never sees raw PII.

Overall status is PASS only when every enabled table has every required check recorded
as PASS (SKIPPED allowed only for an explicitly disabled full compare). Missing checks
yield INCOMPLETE — a partial run can never look like a clean one.
"""

import getpass
import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

REQUIRED_TABLE_CHECKS = (
    "source_lineage",
    "source_header",
    "source_data_quality",
    "target_schema",
    "row_count",
    "control_totals",
    "records",
)
REPORT_NAME = "migration_report.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git(repo_root: Path, *args) -> "str | None":
    try:
        out = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=10, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


class MigrationRun:
    def __init__(self, manifest, environment: str, target_type: str, masking: str, repo_root):
        self.manifest = manifest
        self.repo_root = Path(repo_root)
        self.data = {
            "report_version": 1,
            "run_id": str(uuid.uuid4()),
            "migration": manifest.name,
            "manifest_version": manifest.version,
            "data_classification": manifest.classification,
            "environment": environment,
            "target_type": target_type,
            "masking": masking,
            "operator": getpass.getuser(),
            "git_commit": _git(self.repo_root, "rev-parse", "HEAD"),
            "git_dirty": bool(_git(self.repo_root, "status", "--porcelain")),
            "started_at": _utc_now(),
            "finished_at": None,
            "file_hashes": manifest.file_hashes(),
            "tables": {
                name: {
                    "tier": manifest.tables[name].tier,
                    "contract_version": manifest.tables[name].contract.version,
                    "full_compare": manifest.tables[name].full_compare,
                    "source_lineage": [],
                    "source_rows": None,
                    "target_rows": None,
                    "checks": {},
                }
                for name in manifest.enabled_tables()
            },
            "relationships": {rel["id"]: None for rel in manifest.relationships},
            "overall_status": "INCOMPLETE",
        }

    @property
    def run_id(self) -> str:
        return self.data["run_id"]

    def table(self, name: str) -> dict:
        return self.data["tables"][name]

    def record(self, table: str, check: str, status: str, detail=None) -> None:
        if check not in REQUIRED_TABLE_CHECKS:
            raise ValueError(f"unknown check {check!r}")
        self.table(table)["checks"][check] = {"status": status, "detail": detail, "at": _utc_now()}

    def record_relationship(self, rel_id: str, status: str, detail=None) -> None:
        if rel_id not in self.data["relationships"]:
            raise ValueError(f"unknown relationship {rel_id!r}")
        self.data["relationships"][rel_id] = {"status": status, "detail": detail, "at": _utc_now()}

    def table_status(self, name: str) -> str:
        t = self.table(name)
        statuses = []
        for check in REQUIRED_TABLE_CHECKS:
            entry = t["checks"].get(check)
            if entry is None:
                return "INCOMPLETE"
            ok_skip = check == "records" and not t["full_compare"]
            statuses.append("PASS" if entry["status"] == "SKIPPED" and ok_skip else entry["status"])
        return "PASS" if all(s == "PASS" for s in statuses) else "FAIL"

    def overall_status(self) -> str:
        statuses = [self.table_status(n) for n in self.data["tables"]]
        statuses += [
            "INCOMPLETE" if r is None else r["status"] for r in self.data["relationships"].values()
        ]
        if any(s == "FAIL" for s in statuses):
            return "FAIL"
        if any(s != "PASS" for s in statuses):
            return "INCOMPLETE"
        return "PASS"

    def write(self, out_dir) -> Path:
        for name, t in self.data["tables"].items():
            t["status"] = self.table_status(name)
        self.data["finished_at"] = _utc_now()
        self.data["overall_status"] = self.overall_status()
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / REPORT_NAME
        body = json.dumps(self.data, indent=2, sort_keys=True, default=str).encode("utf-8")
        path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        (out / f"{REPORT_NAME}.sha256").write_text(f"{digest}  {REPORT_NAME}\n", encoding="utf-8")
        return path


def verify_report(path) -> bool:
    """True when the report matches its .sha256 sidecar."""
    path = Path(path)
    expected = (path.parent / f"{path.name}.sha256").read_text(encoding="utf-8").split()[0]
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected
