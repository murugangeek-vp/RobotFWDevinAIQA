"""CI smoke test for the MSSQL adapters against a real SQL Server.

Provisions a SELECT-only login + a tiny synthetic table via the sa
connection (loader-style side path, never the verification adapter), then
exercises MssqlSource / MssqlTarget end-to-end — same coverage as the
mysql/oracle smokes.

Requires an ODBC driver on the host (CI installs msodbcsql18).

Usage:
    python scripts/mssql_adapter_smoke.py

Env: MSSQL_HOST/PORT/DATABASE, MSSQL_SA_PASSWORD, MSSQL_RO_USER,
MSSQL_RO_PASSWORD, MSSQL_DRIVER.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from libs.adapters.mssql import MssqlSource, MssqlTarget  # noqa: E402

HOST = os.environ.get("MSSQL_HOST") or "localhost"
PORT = int(os.environ.get("MSSQL_PORT") or 1433)
DATABASE = os.environ.get("MSSQL_DATABASE") or "recon_src"
SA_PASSWORD = os.environ.get("MSSQL_SA_PASSWORD") or "Str0ng!Passw0rd"
RO_USER = os.environ.get("MSSQL_RO_USER") or "recon_ro"
RO_PASSWORD = os.environ.get("MSSQL_RO_PASSWORD") or "ro_ci_pwd"
DRIVER = os.environ.get("MSSQL_DRIVER") or "ODBC Driver 18 for SQL Server"


def _connect(user, password, database=DATABASE):
    import pyodbc

    return pyodbc.connect(
        driver=f"{{{DRIVER}}}",
        server=f"{HOST},{PORT}",
        database=database,
        uid=user,
        pwd=password,
        TrustServerCertificate="yes",
        autocommit=True,
        timeout=10,
    )


def _retry(fn, attempts=30):
    for i in range(attempts):
        try:
            return fn()
        except Exception:
            if i == attempts - 1:
                raise
            time.sleep(4)


def provision():
    master = _retry(lambda: _connect("sa", SA_PASSWORD, database="master"))
    cur = master.cursor()
    cur.execute(f"IF DB_ID('{DATABASE}') IS NULL CREATE DATABASE [{DATABASE}]")
    try:
        # CHECK_POLICY OFF: ephemeral CI container — the smoke verifies
        # grant-based least privilege, not credential strength. CI secrets
        # (and the local default) may not satisfy SQL Server complexity rules.
        cur.execute(f"CREATE LOGIN [{RO_USER}] WITH PASSWORD = '{RO_PASSWORD}', CHECK_POLICY = OFF")
    except Exception as exc:  # noqa: BLE001 — login already exists
        if "already" not in str(exc):
            raise
    cur.close()
    master.close()

    conn = _connect("sa", SA_PASSWORD)
    cur = conn.cursor()
    try:
        cur.execute(f"CREATE USER [{RO_USER}] FOR LOGIN [{RO_USER}]")
    except Exception as exc:  # noqa: BLE001 — user already exists
        if "already" not in str(exc):
            raise
    cur.execute(
        """
        IF OBJECT_ID('dbo.recon_smoke') IS NOT NULL DROP TABLE dbo.recon_smoke
        """
    )
    cur.execute(
        """
        CREATE TABLE dbo.recon_smoke (
            id INT PRIMARY KEY,
            balance DECIMAL(12,2) NOT NULL,
            updated_at DATETIME2 NOT NULL
        )
        """
    )
    cur.executemany(
        "INSERT INTO dbo.recon_smoke (id, balance, updated_at) VALUES (?, ?, ?)",
        [
            (1, "10.00", "2026-01-01 00:00:00"),
            (2, "20.50", "2026-01-02 00:00:00"),
            (3, "30.00", "2026-01-03 00:00:00"),
        ],
    )
    cur.execute(f"GRANT SELECT ON dbo.recon_smoke TO [{RO_USER}]")
    cur.close()
    conn.close()


def main():
    provision()

    src = MssqlSource(
        host=HOST,
        port=PORT,
        database=DATABASE,
        user=RO_USER,
        password=RO_PASSWORD,
        driver=DRIVER,
        trust_cert="yes",
        table="recon_smoke",
        columns=["id", "balance", "updated_at"],
        snapshot_column="updated_at",
        snapshot_value="2026-01-02 12:00:00",
    )
    assert src.row_count() == 2, src.row_count()
    assert src.post_snapshot_count() == 1, src.post_snapshot_count()
    assert sum(len(f) for f in src.read_chunks(2)) == 2
    assert src.schema() == ["id", "balance", "updated_at"], src.schema()
    src.close()

    tgt = MssqlTarget(
        host=HOST,
        port=PORT,
        database=DATABASE,
        user=RO_USER,
        password=RO_PASSWORD,
        schema="dbo",
        driver=DRIVER,
        trust_cert="yes",
    )
    assert tgt.row_count("recon_smoke") == 3
    live = tgt.schema("recon_smoke")
    assert [c[0] for c in live] == ["id", "balance", "updated_at"], live
    assert tgt.primary_key("recon_smoke") == ["id"]
    assert len(tgt.read_table("recon_smoke")) == 3
    assert sum(len(f) for f in tgt.read_chunks("recon_smoke", size=2)) == 3
    assert tgt.query("SELECT count(*) c FROM dbo.recon_smoke").iloc[0]["c"] == 3
    try:
        tgt.query("DELETE FROM recon_smoke")
    except ValueError:
        pass
    else:
        raise AssertionError("query() accepted a mutation statement")

    # Least privilege: server refuses writes on the RO user.
    try:
        conn = _connect(RO_USER, RO_PASSWORD)
        conn.cursor().execute("INSERT INTO dbo.recon_smoke VALUES (9, 9, '2026-01-09')")
    except Exception as exc:  # noqa: BLE001 — any driver error proves denial
        assert "denied" in str(exc).lower() or "permission" in str(exc).lower(), exc
    else:
        raise AssertionError("recon_ro was able to INSERT — grants too broad")

    tgt.close()
    print("mssql adapter smoke: PASS")


if __name__ == "__main__":
    main()
