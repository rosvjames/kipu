"""Canonical identity for warehouse objects.

The same table shows up as `M5.BRONZE.SALES_RAW` in raw SQL, `source('bronze', 'sales_raw')` in dbt,
and `read_gold("FACT_SALES")` + a schema option in Spark. Every lens must map them to one id:

    table:SCHEMA.NAME   (schema known)
    table:NAME          (schema unknown; resolved later if exactly one SCHEMA.NAME matches)

The database is dropped from the id (kept in meta): it usually comes from config or env vars,
and two references to the same table often disagree on whether they spell it out.
Names are upper-cased (Snowflake-style case-insensitive identifiers).
"""
from __future__ import annotations

import re

_QUOTES = "\"`[]'"


def norm(part: str | None) -> str | None:
    if part is None:
        return None
    p = part.strip().strip(_QUOTES).strip()
    return p.upper() or None


def split_qualified(name: str) -> tuple[str | None, str | None, str]:
    """'db.schema.table' -> (db, schema, table). Missing parts are None."""
    parts = [p for p in name.strip().split(".") if p]
    parts = [norm(p) for p in parts]
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    return None, None, parts[0] if parts else ""


def table_id(name: str, schema: str | None = None, database: str | None = None) -> tuple[str, dict]:
    db, sch, tbl = split_qualified(name)
    sch = sch or norm(schema)
    db = db or norm(database)
    tid = f"table:{sch}.{tbl}" if sch else f"table:{tbl}"
    meta = {"database": db} if db else {}
    return tid, meta


def table_label(tid: str) -> str:
    return tid.split(":", 1)[1].split(".")[-1]


def schema_of(tid: str) -> str | None:
    body = tid.split(":", 1)[1]
    return body.split(".")[0] if "." in body else None


_STAGE = re.compile(r"^@~?([\w$.\"]*)(/.*)?$")


def stage_id(ref: str) -> tuple[str, str]:
    """'@M5.BRONZE.RAW_STAGE/m5/x.csv.gz' -> ('stage:BRONZE.RAW_STAGE', '/m5/x.csv.gz')."""
    m = _STAGE.match(ref.strip())
    if not m:
        return f"stage:{ref.strip('@').upper()}", ""
    _db, sch, name = split_qualified(m.group(1) or "~")
    return (f"stage:{sch}.{name}" if sch else f"stage:{name}"), (m.group(2) or "")


def group_id_for(tid: str) -> str | None:
    sch = schema_of(tid)
    return f"schema:{sch}" if sch else None
