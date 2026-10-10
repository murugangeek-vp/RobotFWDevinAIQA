"""CI smoke test for the Oracle adapters against a real Oracle XE.

Provisions a table owner + a SELECT-only user via a privileged connection
(loader-style side path, never the verification adapter), then exercises
OracleSource / OracleTarget end-to-end — same coverage as the mysql smoke:
watermark predicates, streaming chunks, ALL_TAB_COLUMNS introspection,
single-SELECT query guard, server-side write denial.

Usage:
    python scripts/oracle_adapter_smoke.py

Env: ORACLE_HOST/PORT/SERVICE, ORACLE_SYS_PASSWORD, ORACLE_RO_USER,
ORACLE_RO_PASSWORD. Retries connect for container startup.
"""

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from libs.adapters.oracle import OracleSource, OracleTarget  # noqa: E402

HOST = os.environ.get("ORACLE_HOST") or "localhost"
PORT = int(os.environ.get("ORACLE_PORT") or 1521)
SERVICE = os.environ.get("ORACLE_SERVICE") or "FREEPDB1"
SYS_PASSWORD = os.environ.get("ORACLE_SYS_PASSWORD") or "sys_ci_pwd"
APP_USER = "RECON_APP"
APP_PASSWORD = "app_ci_pwd"
RO_USER = os.environ.get("ORACLE_RO_USER") or "recon_ro"
RO_PASSWORD = os.environ.get("ORACLE_RO_PASSWORD") or "ro_ci_pwd"


def _connect(user, password):
    import oracledb

    return oracledb.connect(
        user=user,
        password=password,
        dsn=oracledb.makedsn(HOST, PORT, service_name=SERVICE),
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
    sys_conn = _retry(lambda: _connect("system", SYS_PASSWORD))
    cur = sys_conn.cursor()
    for stmt in (
        f"CREATE USER {APP_USER} IDENTIFIED BY {APP_PASSWORD} "
        "DEFAULT TABLESPACE USERS QUOTA UNLIMITED ON USERS",
        f"GRANT CREATE SESSION, CREATE TABLE TO {APP_USER}",
        f"CREATE USER {RO_USER} IDENTIFIED BY {RO_PASSWORD}",
        f"GRANT CREATE SESSION TO {RO_USER}",
    ):
        try:
            cur.execute(stmt)
        except Exception as exc:  # noqa: BLE001 — user already exists (ORA-01920)
            if "01920" not in str(exc):
                raise
    cur.close()
    sys_conn.close()

    app = _connect(APP_USER, APP_PASSWORD)
    cur = app.cursor()
    cur.execute(
        """
        BEGIN EXECUTE IMMEDIATE 'DROP TABLE recon_smoke';
        EXCEPTION WHEN OTHERS THEN NULL; END;
        """
    )
    cur.execute(
        """
        CREATE TABLE recon_smoke (
            id NUMBER(19,0) PRIMARY KEY,
            balance NUMBER(12,2) NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    cur.executemany(
        "INSERT INTO recon_smoke (id, balance, updated_at) VALUES (:1, :2, :3)",
        [
            (1, 10.00, datetime(2026, 1, 1)),
            (2, 20.50, datetime(2026, 1, 2)),
            (3, 30.00, datetime(2026, 1, 3)),
        ],
    )
    app.commit()
    cur.execute(f"GRANT SELECT ON {APP_USER}.recon_smoke TO {RO_USER}")
    cur.close()
    app.close()


def main():
    provision()

    src = OracleSource(
        host=HOST,
        port=PORT,
        database=SERVICE,
        user=RO_USER,
        password=RO_PASSWORD,
        schema=APP_USER,
        table="recon_smoke",
        columns=["id", "balance", "updated_at"],
        snapshot_column="updated_at",
        snapshot_value=datetime(2026, 1, 2, 12, 0, 0),
    )
    assert src.row_count() == 2, src.row_count()
    assert src.post_snapshot_count() == 1, src.post_snapshot_count()
    assert sum(len(f) for f in src.read_chunks(2)) == 2
    assert src.schema() == ["ID", "BALANCE", "UPDATED_AT"], src.schema()
    src.close()

    tgt = OracleTarget(
        host=HOST,
        port=PORT,
        database=SERVICE,
        user=RO_USER,
        password=RO_PASSWORD,
        schema=APP_USER,
    )
    assert tgt.row_count("recon_smoke") == 3
    live = tgt.schema("recon_smoke")
    assert [c[0] for c in live] == ["ID", "BALANCE", "UPDATED_AT"], live
    assert tgt.primary_key("recon_smoke") == ["ID"]
    assert len(tgt.read_table("recon_smoke")) == 3
    assert sum(len(f) for f in tgt.read_chunks("recon_smoke", size=2)) == 3
    assert tgt.query("SELECT count(*) c FROM RECON_APP.recon_smoke").iloc[0]["C"] == 3
    try:
        tgt.query("DELETE FROM recon_smoke")
    except ValueError:
        pass
    else:
        raise AssertionError("query() accepted a mutation statement")

    # Least privilege: server refuses writes on the RO user.
    try:
        conn = _connect(RO_USER, RO_PASSWORD)
        conn.cursor().execute(f"INSERT INTO {APP_USER}.recon_smoke VALUES (9, 9, SYSDATE)")
    except Exception as exc:  # noqa: BLE001 — any driver error proves denial
        assert "insufficient" in str(exc).lower() or "privileg" in str(exc).lower(), exc
    else:
        raise AssertionError("recon_ro was able to INSERT — grants too broad")

    tgt.close()
    print("oracle adapter smoke: PASS")


if __name__ == "__main__":
    main()
