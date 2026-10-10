"""MySQL source + target adapters (pymysql driver).

Verification is read-only: connect with a SELECT-only role — MySQL has no
session-level read-only equivalent to postgres' set_session, so least
privilege is enforced by the account's grants plus the single-SELECT guard
in query(). ``database`` doubles as the schema on MySQL.
"""

from libs.adapters.dbapi import DbapiSource, DbapiTarget
from libs.adapters.registry import register_source, register_target


def _connect(host, port, database, user, password, charset="utf8mb4", connect_timeout=15, **_):
    import pymysql

    return pymysql.connect(
        host=host,
        port=int(port or 3306),
        user=user,
        password=password,
        database=database,
        charset=charset,
        connect_timeout=int(connect_timeout),
        autocommit=True,
    )


class _MysqlConn:
    dialect = "mysql"
    PARAM = "%s"

    def _connect(self, host, port, database, user, password, **kw):
        return _connect(host, port, database, user, password, **kw)

    def _cursor(self):
        # SSCursor is unbuffered (server-side) — million-row sources stay
        # flat in memory like the postgres named cursor.
        import pymysql

        return self.conn.cursor(pymysql.cursors.SSCursor)

    def _default_schema(self, user, database):
        return database


@register_source("mysql")
class MysqlSource(_MysqlConn, DbapiSource):
    """MySQL migration source; ``schema`` defaults to ``database``."""


@register_target("mysql")
class MysqlTarget(_MysqlConn, DbapiTarget):
    """MySQL migration target (SELECT-only)."""
