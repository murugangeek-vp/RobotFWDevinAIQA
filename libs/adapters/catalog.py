def inspect_schema(conn, schema_name, dialect="postgres"):
    """Read catalog metadata only, limited to the configured schema.

    postgres/mysql/mssql read information_schema.columns; oracle reads
    ALL_TAB_COLUMNS (grant-filtered, OWNER holds the schema). Parameter
    style follows the dialect's DBAPI paramstyle."""
    cur = conn.cursor()
    names = [
        "table",
        "column",
        "type",
        "nullable",
        "max_length",
        "precision",
        "scale",
        "default",
    ]
    try:
        if dialect == "oracle":
            # data_default is a LONG column — not selectable alongside others.
            cur.execute(
                "SELECT table_name, column_name, data_type, nullable, "
                "char_length, data_precision, data_scale, NULL "
                "FROM all_tab_columns WHERE owner = :1 "
                "ORDER BY table_name, column_id",
                (schema_name.upper(),),
            )
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
        ph = "?" if dialect == "mssql" else "%s"
        cur.execute(
            "SELECT table_name, column_name, data_type, is_nullable, "
            "character_maximum_length, numeric_precision, numeric_scale, column_default "
            "FROM information_schema.columns WHERE table_schema = " + ph + " "
            "ORDER BY table_name, ordinal_position",
            (schema_name,),
        )
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        cur.close()
