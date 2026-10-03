"""MIG-05: referential integrity across migrated tables.

A manifest relationship ``{id, child, column, parent, parent_column}`` asserts every
non-null ``child.column`` exists in ``parent.parent_column``. It is checked on the source
extract (so broken source data is reported as a source defect, not a migration defect)
and on the target via TargetAdapter.orphans (anti-join inside the database).
"""

from libs.engine.reconcile import _isna, normalize


def source_orphans(child_df, child_contract, parent_df, parent_contract, rel, samples=5):
    """(orphan_count, sample values) over expected target rows of both tables."""
    cspec = child_contract.column(rel["column"])
    pspec = parent_contract.column(rel["parent_column"])
    parents = {normalize(v, pspec) for v in parent_df[rel["parent_column"]] if not _isna(v)}
    orphans = [
        v for v in child_df[rel["column"]] if not _isna(v) and normalize(v, cspec) not in parents
    ]
    return len(orphans), orphans[:samples]
