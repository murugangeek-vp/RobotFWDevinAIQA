"""PROD-05: Snowflake target adapter.

Env `target:` block with `type: snowflake`:
    account / warehouse / database / schema / role — Snowflake coordinates
    (role should be a SELECT-only role for verification users)
    Credentials come from RECON_RO_* or key-pair environment references.

Only conventional, case-insensitive identifiers and TIMESTAMP_NTZ contracts
are supported. Verification uses bounded reads, an explicit primary role,
and no secondary roles. Arbitrary SQL and loading are disabled. The account
administrator must grant SELECT-only access and validate inherited grants;
local guard tests do not prove Snowflake RBAC enforcement.
"""

import os
from contextlib import closing
from typing import Any

import pandas as pd

from libs.adapters.base import TargetAdapter
from libs.adapters.registry import register_target
from libs.engine.schema import ident


@register_target("snowflake")
class SnowflakeTarget(TargetAdapter):
    """Read-only verification adapter for Snowflake (SELECT-only role)."""

    dialect = "snowflake"

    def __init__(
        self,
        account: str,
        warehouse: str,
        database: str,
        user: str,
        password: str = "",
        schema: str = "PUBLIC",
        role: "str | None" = None,
        authenticator: str = "snowflake",
        private_key_file_env: str = "SNOWFLAKE_PRIVATE_KEY_FILE",
        private_key_password_env: str = "SNOWFLAKE_PRIVATE_KEY_PASSWORD",
        login_timeout: int = 30,
        network_timeout: int = 60,
        statement_timeout: int = 120,
        max_rows: int = 100000,
    ):
        for name, value in {"account": account, "user": user}.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Snowflake requires {name}")
        if not isinstance(role, str):
            raise ValueError("Snowflake requires an explicit verification role")
        for value in (database, schema, warehouse, role):
            ident(value, "snowflake")
        limits = (login_timeout, network_timeout, statement_timeout, max_rows)
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in limits):
            raise ValueError("Snowflake timeouts and max_rows must be positive integers")
        self.database = database.upper()
        self.schema_name = schema.upper()
        self.max_rows = max_rows
        self._column_details: dict = {}
        conn_args: dict[str, Any] = dict(
            account=account,
            warehouse=warehouse.upper(),
            database=self.database,
            schema=self.schema_name,
            user=user,
            role=role.upper(),
            authenticator=authenticator,
            login_timeout=login_timeout,
            network_timeout=network_timeout,
            socket_timeout=network_timeout,
            ocsp_fail_open=False,
            session_parameters={
                "QUERY_TAG": "recon-verification",
                "STATEMENT_TIMEOUT_IN_SECONDS": statement_timeout,
                "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": statement_timeout,
                "TIMEZONE": "UTC",
                "CLIENT_SESSION_KEEP_ALIVE": False,
            },
        )
        if authenticator == "snowflake":
            if not password:
                raise ValueError("Snowflake password authentication requires RECON_RO_PASSWORD")
            conn_args["password"] = password
        elif authenticator == "SNOWFLAKE_JWT":
            key_file = os.environ.get(private_key_file_env)
            if not key_file:
                raise ValueError(
                    f"Snowflake key-pair authentication requires {private_key_file_env}"
                )
            conn_args["private_key_file"] = key_file
            key_password = os.environ.get(private_key_password_env)
            if key_password:
                conn_args["private_key_file_pwd"] = key_password
        else:
            raise ValueError("Supported Snowflake authenticators: snowflake, SNOWFLAKE_JWT")
        import snowflake.connector

        self.conn = snowflake.connector.connect(**conn_args)
        try:
            with closing(self.conn.cursor()) as cur:
                cur.execute("USE SECONDARY ROLES NONE")
                cur.execute("SELECT CURRENT_ROLE()")
                current_role = cur.fetchone()
                if not current_role or current_role[0] != role.upper():
                    raise PermissionError("Snowflake session role does not match configured role")
        except Exception:
            self.conn.close()
            raise

    def _table(self, table: str) -> str:
        return ".".join(ident(v, "snowflake") for v in (self.database, self.schema_name, table))

    @staticmethod
    def _column_name(name: str) -> str:
        ident(name, "snowflake")
        if name != name.upper():
            raise ValueError("Quoted case-sensitive Snowflake columns are not supported")
        return name.lower()

    def read_table(self, table: str, columns: "list | None" = None):
        cols = ", ".join(ident(c, "snowflake") for c in columns) if columns else "*"
        query = f"SELECT {cols} FROM {self._table(table)} LIMIT {self.max_rows + 1}"
        with closing(self.conn.cursor()) as cur:
            cur.execute(query)
            names = [self._column_name(d[0]) for d in cur.description]
            if len(names) != len(set(names)):
                raise ValueError("Duplicate Snowflake result column names")
            rows = cur.fetchmany(self.max_rows + 1)
            if len(rows) > self.max_rows:
                raise ValueError(
                    "Snowflake result exceeds max_rows; reconciliation was not performed"
                )
        return pd.DataFrame(rows, columns=names, dtype=object)

    def row_count(self, table: str) -> int:
        query = f"SELECT count(*) FROM {self._table(table)}"
        with closing(self.conn.cursor()) as cur:
            cur.execute(query)
            return int(cur.fetchone()[0])

    def schema(self, table: str) -> list:
        self._table(table)
        with closing(self.conn.cursor()) as cur:
            cur.execute(
                f"SELECT column_name, data_type, is_nullable, numeric_precision, "
                f"numeric_scale, character_maximum_length "
                f"FROM {ident(self.database, 'snowflake')}.INFORMATION_SCHEMA.COLUMNS "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (self.schema_name, table.upper()),
            )
            rows = cur.fetchall()
        self._column_details = {self._column_name(r[0]): r[3:] for r in rows}
        return [(self._column_name(r[0]), r[1].lower(), r[2] == "YES") for r in rows]

    def type_errors(self, contract) -> list:
        errors = []
        for col in contract.columns:
            details = self._column_details.get(col["name"])
            if details is None:
                continue
            precision, scale, length = details
            if col["type"] == "integer" and scale != 0:
                errors.append(
                    f"column '{col['name']}' integer requires numeric_scale=0, got {scale}"
                )
            if col["type"] == "decimal" and scale != col.get("scale", 2):
                errors.append(f"column '{col['name']}' decimal scale mismatch: got {scale}")
            if col.get("max_length") is not None and length != col["max_length"]:
                errors.append(f"column '{col['name']}' maximum length mismatch: got {length}")
            if col.get("precision") is not None and precision != col["precision"]:
                errors.append(f"column '{col['name']}' numeric precision mismatch: got {precision}")
        return errors

    def primary_key(self, table: str) -> list:
        query = f"SHOW PRIMARY KEYS IN TABLE {self._table(table)}"
        with closing(self.conn.cursor()) as cur:
            cur.execute(query)
            names = [d[0].lower() for d in cur.description]
            rows = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
        return [
            self._column_name(r["column_name"])
            for r in sorted(rows, key=lambda r: int(r["key_sequence"]))
        ]

    def _qualified(self, table: str) -> str:
        return self._table(table)

    def _fetch(self, sql: str, max_rows: int) -> list:
        # Only reached via TargetAdapter.aggregate/orphans (generated SQL).
        with closing(self.conn.cursor()) as cur:
            cur.execute(sql)
            return cur.fetchmany(max_rows)

    def query(self, sql: str, params=None):
        raise PermissionError(
            "Snowflake arbitrary SQL is disabled; use verification adapter methods"
        )

    def is_closed(self) -> bool:
        return self.conn.is_closed()

    def close(self):
        if not self.is_closed():
            self.conn.close()
