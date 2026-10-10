"""MSSQL (SQL Server) source + target adapters (pyodbc driver).

Requires an ODBC driver on the host — ``driver`` defaults to "ODBC Driver 18
for SQL Server" and can be overridden in the env yaml. Verification is
read-only: connect with a SELECT-only role plus the single-SELECT guard in
query(). ``schema`` defaults to dbo.
"""

from libs.adapters.dbapi import DbapiSource, DbapiTarget
from libs.adapters.registry import register_source, register_target


def _connect(
    host,
    port,
    database,
    user,
    password,
    driver="ODBC Driver 18 for SQL Server",
    encrypt=None,
    trust_cert=None,
    timeout=15,
    **_,
):
    import pyodbc

    kw = {}
    if encrypt:
        kw["Encrypt"] = encrypt
    if trust_cert:
        kw["TrustServerCertificate"] = trust_cert
    # keyword args let pyodbc build the connection string — the password is
    # never interpolated into a string ourselves.
    return pyodbc.connect(
        driver=f"{{{driver}}}",
        server=f"{host},{int(port or 1433)}",
        database=database,
        uid=user,
        pwd=password,
        timeout=int(timeout),
        autocommit=True,
        **kw,
    )


class _MssqlConn:
    dialect = "mssql"
    PARAM = "?"

    def _connect(self, host, port, database, user, password, **kw):
        return _connect(host, port, database, user, password, **kw)

    def _default_schema(self, user, database):
        return "dbo"


@register_source("mssql")
class MssqlSource(_MssqlConn, DbapiSource):
    """SQL Server migration source."""


@register_target("mssql")
class MssqlTarget(_MssqlConn, DbapiTarget):
    """SQL Server migration target (SELECT-only)."""
