"""PROD-01: shared adapter conformance checks.

Any adapter resolved through the registry must satisfy these before suites
rely on it. Each function returns a list of violation strings; empty = the
adapter conforms. New adapters (S3, REST, Dataiku, Snowflake, MySQL) run the
same checks — contract/env config picks the type, nothing else changes.
"""

import pandas as pd


def check_source(adapter) -> list:
    """Verify a SourceAdapter: read_batch -> DataFrame, row_count and schema
    consistent with it, close() does not raise."""
    errors = []
    try:
        df = adapter.read_batch()
        if not isinstance(df, pd.DataFrame):
            errors.append(f"read_batch returned {type(df).__name__}, expected DataFrame")
        else:
            n = adapter.row_count()
            if n != len(df):
                errors.append(f"row_count()={n} != len(read_batch())={len(df)}")
            cols = list(adapter.schema())
            if cols != list(df.columns):
                errors.append(f"schema()={cols} != read_batch columns={list(df.columns)}")
    except Exception as e:
        errors.append(f"adapter call raised {e!r}")
    finally:
        try:
            adapter.close()
        except Exception as e:
            errors.append(f"close raised {e!r}")
    return errors


def check_target(adapter, table: str) -> list:
    """Verify a TargetAdapter: read_table -> DataFrame, row_count and schema
    consistent with it, close() does not raise."""
    errors = []
    try:
        df = adapter.read_table(table)
        if not isinstance(df, pd.DataFrame):
            errors.append(f"read_table returned {type(df).__name__}, expected DataFrame")
        else:
            n = adapter.row_count(table)
            if n != len(df):
                errors.append(f"row_count()={n} != len(read_table())={len(df)}")
            names = [s[0] for s in adapter.schema(table)]
            if names != list(df.columns):
                errors.append(f"schema() columns={names} != read_table columns={list(df.columns)}")
    except Exception as e:
        errors.append(f"adapter call raised {e!r}")
    finally:
        try:
            adapter.close()
        except Exception as e:
            errors.append(f"close raised {e!r}")
    return errors
