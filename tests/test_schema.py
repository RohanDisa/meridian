from meridian.db import REQUIRED_TABLES, connect, init_schema


def test_init_schema_creates_required_tables(db_path):
    conn = connect(db_path)
    init_schema(conn)
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert REQUIRED_TABLES <= names


def test_bom_rows_has_raw_and_parsed_columns(db_path):
    conn = connect(db_path)
    init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(bom_rows)")}
    for expected in (
        "name",
        "name_norm",
        "material_raw",
        "interface_with_raw",
        "cost_raw",
        "cost_dkk",
        "amount_raw",
        "amount_parsed",
        "source_file",
        "source_row",
    ):
        assert expected in cols


def test_corrections_do_not_replace_bom_raw_columns(db_path):
    conn = connect(db_path)
    init_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(corrections)")}
    for expected in (
        "entity_type",
        "entity_id",
        "field",
        "old_value",
        "new_value",
        "status",
    ):
        assert expected in cols
