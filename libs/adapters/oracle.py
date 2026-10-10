"""Oracle source + target adapters (python-oracledb thin mode — no client
install needed).

Verification is read-only: connect with a SELECT-only role; the single-SELECT
guard in query() is a second line of defence. Identifiers are emitted
quoted-UPPER, matching tables created by ordinary unquoted DDL. ``database``
is the Oracle service name; ``schema`` defaults to the connecting user.
"""

from libs.adapters.dbapi import DbapiSource, DbapiTarget
from libs.adapters.registry import register_source, register_target


def _connect(host, port, database, user, password, service_name=None, **_):
    import oracledb

    dsn = oracledb.makedsn(host, int(port or 1521), service_name=service_name or database)
    return oracledb.connect(user=user, password=password, dsn=dsn)


class _OracleConn:
    dialect = "oracle"
    PARAM = ":1"

    def _connect(self, host, port, database, user, password, **kw):
        return _connect(host, port, database, user, password, **kw)

    def _cursor(self):
        cur = self.conn.cursor()
        cur.prefetchrows = 5000
        return cur

    def _default_schema(self, user, database):
        return str(user).upper()


@register_source("oracle")
class OracleSource(_OracleConn, DbapiSource):
    """Oracle migration source (service name via ``database``)."""


@register_target("oracle")
class OracleTarget(_OracleConn, DbapiTarget):
    """Oracle migration target (SELECT-only)."""
