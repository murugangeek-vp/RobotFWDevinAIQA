def inspect_schema(conn, schema_name):
    """Read catalog metadata only, limited to the configured schema."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT table_name, column_name, data_type, is_nullable, "
            "character_maximum_length, numeric_precision, numeric_scale, column_default "
            "FROM information_schema.columns WHERE table_schema = %s "
            "ORDER BY table_name, ordinal_position",
            (schema_name,),
        )
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
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        cur.close()
