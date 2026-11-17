"""Which tables (and stages) a piece of SQL reads and writes.

Templated SQL (Jinja / Pebble / dbt) is cleaned first: `{% ... %}` blocks are removed and `{{ ... }}`
expressions become a placeholder, keeping line numbers intact. sqlglot does the parsing; if it fails,
a tolerant regex fallback runs and everything it finds is marked inferred.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from kipu.graph import EXACT, INFERRED, UNRESOLVED

PLACEHOLDER = "KIPU_TPL"

_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_BLOCK = re.compile(r"\{%-?.*?-?%\}", re.S)
_EXPR = re.compile(r"\{\{.*?\}\}", re.S)

_SKIP_CREATE_KINDS = {"FILE FORMAT", "SCHEMA", "DATABASE", "WAREHOUSE", "ROLE", "USER", "FUNCTION",
                      "PROCEDURE", "SEQUENCE", "INDEX", "INTEGRATION", "PIPE", "TAG"}


@dataclass
class Access:
    op: str  # "reads" | "writes"
    name: str  # raw qualified name, or '@stage/path'
    line: int  # 1-based line within the given SQL text
    confidence: str
    detail: str

    @property
    def is_stage(self) -> bool:
        return self.name.startswith("@")


def _keep_newlines(repl: str):
    return lambda m: repl + "\n" * m.group(0).count("\n")


def strip_templates(sql: str) -> str:
    sql = _COMMENT.sub(_keep_newlines(""), sql)
    sql = _BLOCK.sub(_keep_newlines(""), sql)
    return _EXPR.sub(_keep_newlines(PLACEHOLDER), sql)


def _split_statements(sql: str) -> list[tuple[str, int]]:
    """Split on top-level semicolons; return (statement_text, start_line)."""
    try:
        tokens = sqlglot.tokenize(sql, read="snowflake")
    except TokenError:
        return [(sql, 1)]
    out, start = [], 0
    for tok in tokens:
        if tok.token_type == sqlglot.tokens.TokenType.SEMICOLON:
            out.append((sql[start:tok.start], start))
            start = tok.end + 1
    out.append((sql[start:], start))
    res = []
    for text, offset in out:
        if text.strip():
            lead = len(text) - len(text.lstrip())
            res.append((text.lstrip(), sql.count("\n", 0, offset + lead) + 1))
    return res


def _table_name(t: exp.Table) -> str:
    parts = [p for p in (t.catalog, t.db, t.name) if p]
    return ".".join(parts)


def _line_in(text: str, name: str, start_line: int) -> int:
    needle = name.lstrip("@").split("/")[0].split(".")[-1].strip('"').lower()
    for i, ln in enumerate(text.splitlines()):
        if needle and needle in ln.lower():
            return start_line + i
    return start_line


def _write_target(stmt: exp.Expression) -> tuple[exp.Table | None, str]:
    if isinstance(stmt, exp.Create):
        kind = (stmt.args.get("kind") or "").upper()
        if kind in _SKIP_CREATE_KINDS:
            return None, "skip"
        target = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
        return (target if isinstance(target, exp.Table) else None), f"CREATE {kind}".strip()
    if isinstance(stmt, (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Copy)):
        target = stmt.this.this if isinstance(stmt.this, exp.Schema) else stmt.this
        label = {exp.Insert: "INSERT", exp.Update: "UPDATE", exp.Delete: "DELETE",
                 exp.Merge: "MERGE", exp.Copy: "COPY INTO"}[type(stmt)]
        return (target if isinstance(target, exp.Table) else None), label
    return None, type(stmt).__name__.upper()


def extract(sql: str, dialect: str = "snowflake") -> list[Access]:
    clean = strip_templates(sql)
    accesses: list[Access] = []
    for text, start_line in _split_statements(clean):
        try:
            stmt = sqlglot.parse_one(text, read=dialect)
        except (ParseError, TokenError, ValueError):
            accesses.extend(_fallback(text, start_line))
            continue
        if stmt is None or isinstance(stmt, (exp.Use, exp.Grant, exp.Command)) and not isinstance(stmt, exp.Copy):
            if isinstance(stmt, exp.Command):
                accesses.extend(_fallback(text, start_line))
            continue
        target, label = _write_target(stmt)
        if label == "skip":
            continue
        ctes = {c.alias_or_name.upper() for c in stmt.find_all(exp.CTE)}
        seen = set()
        if target is not None:
            name = _table_name(target)
            if label == "CREATE STAGE" and not name.startswith("@"):
                name = "@" + name
            seen.add(id(target))
            accesses.append(_mk("writes", name, text, start_line, label))
        if label.startswith("CREATE") and label not in ("CREATE TABLE", "CREATE VIEW", "CREATE"):
            continue  # e.g. CREATE STAGE ... FILE_FORMAT = x: x is not a table being read
        for t in stmt.find_all(exp.Table):
            if id(t) in seen:
                continue
            name = _table_name(t)
            if not name or name.upper() in ctes:
                continue
            accesses.append(_mk("reads", name, text, start_line, label))
    return accesses


def _mk(op: str, name: str, text: str, start_line: int, label: str) -> Access:
    line = _line_in(text, name, start_line)
    conf = EXACT
    if PLACEHOLDER in name.upper():
        stage_part = name.split("/", 1)[0]
        if name.startswith("@") and PLACEHOLDER not in stage_part.upper():
            label += " (ruta dinámica dentro del stage)"  # the stage is literal; only the file path varies
        else:
            conf = UNRESOLVED
        name = re.sub(PLACEHOLDER, "{…}", name, flags=re.I)
    return Access(op, name, line, conf, label)


_FALLBACK = re.compile(
    r"\b(?P<kw>FROM|JOIN|INTO|UPDATE|TABLE)\s+(?P<name>@?[\w$\"]+(?:\.[\w$\"]+){0,2}(?:/[\w./{}…-]*)?)",
    re.I,
)
_NOT_TABLES = {"SELECT", "LATERAL", "TABLE", "VALUES", "IF", "NOT", "EXISTS", "(", "UNNEST"}


def _fallback(text: str, start_line: int) -> list[Access]:
    out = []
    for m in _FALLBACK.finditer(text):
        name = m.group("name")
        if name.upper() in _NOT_TABLES:
            continue
        op = "writes" if m.group("kw").upper() in ("INTO", "UPDATE", "TABLE") else "reads"
        line = start_line + text.count("\n", 0, m.start())
        conf = UNRESOLVED if PLACEHOLDER in name.upper() else INFERRED
        out.append(Access(op, name.replace(PLACEHOLDER, "{…}"), line, conf, f"regex {m.group('kw').upper()}"))
    return out
