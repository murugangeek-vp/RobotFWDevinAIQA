"""CI smoke test for the MySQL adapters against a real mysqld.

Provisions a SELECT-only user + a tiny synthetic table via the root
connection (loader-style side path, never the verification adapter), then
exercises MysqlSource / MysqlTarget end-to-end:

- row_count / schema / read_chunks / post_snapshot_count
- target schema + primary_key introspection
- query() SELECT-only guard
- least privilege: the RO user's INSERT is refused by the server

Usage (CI or local dockerised mysqld):
    python scripts/mysql_adapter_smoke.py

Env: MYSQL_HOST/PORT/DATABASE, MYSQL_ROOT_USER/MYSQL_ROOT_PASSWORD,
MYSQL_RO_USER/MYSQL_RO_PASSWORD.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from libs.adapters.mysql import MysqlSource, MysqlTarget  # noqa: E402

HOST = os.environ.get("MYSQL_HOST") or "localhost"
PORT = int(os.environ.get("MYSQL_PORT") or 3306)
DATABASE = os.environ.get("MYSQL_DATABASE") or "recon_src"
ROOT_USER = os.environ.get("MYSQL_ROOT_USER") or "root"
ROOT_PASSWORD = os.environ.get("MYSQL_ROOT_PASSWORD") or "root_ci"
RO_USER = os.environ.get("MYSQL_RO_USER") or "recon_ro"
RO_PASSWORD = os.environ.get("MYSQL_RO_PASSWORD") or "ro_ci"


def _root():
    import pymysql

    return pymysql.connect(
        host=HOST,
        port=PORT,
        user=ROOT_USER,
        password=ROOT_PASSWORD,
        database=DATABASE,
        autocommit=True,
    )


def provision():
    conn = _root()
    cur = conn.cursor()
    cur.execute(f"CREATE USER IF NOT EXISTS '{RO_USER}'@'%' IDENTIFIED BY '{RO_PASSWORD}'")
    cur.execute(f"GRANT SELECT ON {DATABASE}.* TO '{RO_USER}'@'%'")
    cur.execute("FLUSH PRIVILEGES")
    cur.execute("DROP TABLE IF EXISTS recon_smoke")
    cur.execute(
        """
        CREATE TABLE recon_smoke (
            id INT PRIMARY KEY,
            balance DECIMAL(12,2) NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    cur.executemany(
        "INSERT INTO recon_smoke (id, balance, updated_at) VALUES (%s, %s, %s)",
        [
            (1, "10.00", "2026-01-01 00:00:00"),
            (2, "20.50", "2026-01-02 00:00:00"),
            (3, "30.00", "2026-01-03 00:00:00"),
        ],
    )
    cur.close()
    conn.close()


def main():
    provision()

    src = MysqlSource(
        host=HOST,
        port=PORT,
        database=DATABASE,
        user=RO_USER,
        password=RO_PASSWORD,
        table="recon_smoke",
        columns=["id", "balance", "updated_at"],
        snapshot_column="updated_at",
        snapshot_value="2026-01-02 12:00:00",
    )
    assert src.row_count() == 2, src.row_count()
    assert src.post_snapshot_count() == 1, src.post_snapshot_count()
    frames = list(src.read_chunks(2))
    assert sum(len(f) for f in frames) == 2
    assert src.schema() == ["id", "balance", "updated_at"], src.schema()
    src.close()

    tgt = MysqlTarget(
        host=HOST,
        port=PORT,
        database=DATABASE,
        user=RO_USER,
        password=RO_PASSWORD,
        schema=DATABASE,
    )
    assert tgt.row_count("recon_smoke") == 3
    live = tgt.schema("recon_smoke")
    assert [c[0] for c in live] == ["id", "balance", "updated_at"], live
    assert tgt.primary_key("recon_smoke") == ["id"]
    assert len(tgt.read_table("recon_smoke")) == 3
    assert sum(len(f) for f in tgt.read_chunks("recon_smoke", size=2)) == 3
    assert tgt.query("SELECT count(*) c FROM recon_smoke").iloc[0]["c"] == 3
    try:
        tgt.query("DELETE FROM recon_smoke")
    except ValueError:
        pass
    else:
        raise AssertionError("query() accepted a mutation statement")

    # Least privilege: server refuses writes on the RO user.
    try:
        import pymysql

        conn = pymysql.connect(
            host=HOST,
            port=PORT,
            user=RO_USER,
            password=RO_PASSWORD,
            database=DATABASE,
            autocommit=True,
        )
        conn.cursor().execute("INSERT INTO recon_smoke VALUES (9, 9, '2026-01-09')")
    except Exception as exc:  # noqa: BLE001 — any driver error proves denial
        assert "denied" in str(exc).lower() or "insert" in str(exc).lower(), exc
    else:
        raise AssertionError("recon_ro was able to INSERT — grants too broad")

    tgt.close()
    print("mysql adapter smoke: PASS")


if __name__ == "__main__":
    main()
