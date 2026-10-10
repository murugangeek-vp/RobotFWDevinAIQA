import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from libs.engine.reconcile import canonical_key, normalize


def ordered_chunks(chunks, contract, size=5000, directory=None):
    """External canonical sort: bounded RAM, O(rows) private temporary disk.

    Use encrypted local storage for real data. The caller must close this
    generator on failure; temporary storage is deleted on exit.
    """
    if size < 1:
        raise ValueError("batch size must be positive")
    names = [c["name"] for c in contract.columns]
    with ExitStack() as stack:
        tmp = stack.enter_context(TemporaryDirectory(prefix="recon-", dir=directory))
        db = stack.enter_context(closing(sqlite3.connect(str(Path(tmp) / "rows.sqlite"))))
        if hasattr(chunks, "close"):
            stack.callback(chunks.close)
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA temp_store=FILE")
        db.execute("CREATE TABLE rows (key TEXT NOT NULL, payload TEXT NOT NULL)")
        for chunk in chunks:

            def records(frame=chunk):
                for values in frame.itertuples(index=False, name=None):
                    row = dict(zip(frame.columns, values, strict=True))
                    normalized = {c["name"]: normalize(row[c["name"]], c) for c in contract.columns}
                    yield canonical_key(row, contract), json.dumps(normalized, default=str)

            db.executemany("INSERT INTO rows VALUES (?, ?)", records())
            db.commit()
        db.execute("CREATE INDEX row_key ON rows(key)")
        # Cursor registered last so it unwinds first: an open statement keeps
        # the sqlite file locked on Windows and breaks tempdir cleanup.
        cur = stack.enter_context(
            closing(db.execute("SELECT payload FROM rows ORDER BY key"))
        )
        while rows := cur.fetchmany(size):
            yield pd.DataFrame([json.loads(row[0]) for row in rows], columns=names, dtype=object)
