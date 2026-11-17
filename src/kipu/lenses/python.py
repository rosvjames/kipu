"""Python lens: modules, their imports, and the data they read and write.

What is exact here: which modules and functions exist, and which project modules a file imports.
What is inferred: data access, recognised by patterns (Spark readers/writers, pandas I/O, SQL passed
to execute()/sql()), and resolved through string constants, f-strings and one level of wrapper
functions (e.g. `def read_gold(table): ... option("dbtable", table)` called as `read_gold("FACT_SALES")`).
Anything we cannot resolve is kept as an `unresolved` edge instead of being dropped.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from kipu import ids, sqlextract
from kipu.files import read_text, rel, short_hash, walk
from kipu.graph import EXACT, INFERRED, UNRESOLVED, Evidence, Node
from kipu.lenses import Context

LENS = "python"
_SQL_HINT = re.compile(r"\b(select|insert|update|delete|merge|copy\s+into|create)\b", re.I)
_SECRETISH = re.compile(r"(secret|\.pem$|\.p8$|\.key$|\.env|password|token)", re.I)
_READERS = {"read_csv", "read_parquet", "read_json", "read_excel", "read_feather", "read_table", "read_orc"}
_WRITERS = {"to_csv", "to_parquet", "to_json", "to_excel", "to_feather", "to_orc"}
_SQL_CALLS = {"execute", "executemany", "sql", "read_sql", "read_sql_query", "query"}
_SPARK_FILE_FMT = {"csv", "parquet", "json", "orc", "text", "delta"}
_SESSION_NAMES = {"spark", "session", "sparkSession", "spark_session", "sqlContext", "snowpark", "snowpark_session"}
_SLOT = "\x00"  # where a wrapper's parameter goes inside a template string


@dataclass
class Access:
    op: str  # reads | writes
    kind: str  # table | file | sql
    value: object  # ("lit", str) | ("param", name) | ("partial", str) | None
    schema: tuple | None
    line: int
    func: str | None
    detail: str


@dataclass
class ModuleInfo:
    rel: str
    node_id: str
    tree: ast.Module
    constants: dict[str, str] = field(default_factory=dict)
    functions: dict[str, ast.FunctionDef] = field(default_factory=dict)
    imported_names: dict[str, tuple[str, str]] = field(default_factory=dict)  # local -> (module rel, name)
    wrappers: dict[str, list[tuple[int, str, Access]]] = field(default_factory=dict)  # func -> [(arg idx, param, acc)]


def scan(ctx: Context) -> None:
    g, root = ctx.graph, ctx.root
    mods: dict[str, ModuleInfo] = {}
    basenames: dict[str, list[str]] = {}
    for p in walk(root, {".py"}):
        r = rel(root, p)
        text = read_text(p)
        nid = f"module:{r}"
        parent_dir = str(Path(r).parent)
        parent = f"dir:{parent_dir}" if parent_dir != "." else None
        meta: dict = {}
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            g.add_node(Node(nid, "module", p.name, parent=parent, file=r, line=1, lens=LENS, defined=True,
                            hash=short_hash(text), meta={"parse_error": str(e)}))
            continue
        doc = ast.get_docstring(tree)
        if doc:
            meta["description"] = doc.strip().splitlines()[0]
        funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        meta["functions"] = [{"name": f.name, "line": f.lineno} for f in funcs]
        if classes:
            meta["classes"] = [{"name": c.name, "line": c.lineno} for c in classes]
        if any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in tree.body):
            meta["entrypoint"] = True
        g.add_node(Node(nid, "module", p.name, parent=parent, file=r, line=1, lens=LENS, defined=True,
                        hash=short_hash(text), meta=meta))
        info = ModuleInfo(r, nid, tree)
        info.functions = {f.name: f for f in funcs}
        for n in tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        info.constants[t.id] = n.value.value
        mods[r] = info
        basenames.setdefault(p.name, []).append(nid)
    for name, nids in basenames.items():
        if len(nids) == 1:
            ctx.python_modules[name] = nids[0]

    for info in mods.values():
        _imports(ctx, info, mods)
    for info in mods.values():
        _find_accesses(ctx, info)
    for info in mods.values():
        _wrapper_calls(ctx, info, mods)


# ---------------------------------------------------------------- imports
def _resolve_module(dotted: str, level: int, importer: str, mods: dict) -> str | None:
    if not dotted and not level:
        return None
    here = Path(importer).parent
    parts = dotted.split(".") if dotted else []
    bases = []
    if level:
        base = here
        for _ in range(level - 1):
            base = base.parent
        bases.append(base)
    else:
        bases += [here, Path("."), Path("src")]
    for b in bases:
        cand = b.joinpath(*parts) if parts else b
        for c in (cand.with_suffix(".py"), cand / "__init__.py"):
            key = c.as_posix().removeprefix("./")
            if key in mods:
                return key
    return None


def _imports(ctx: Context, info: ModuleInfo, mods: dict) -> None:
    pkg = ctx.root.name  # scanning a package folder directly: `from <pkg> import x` means ./x.py

    def resolve(dotted: str, level: int):
        hit = _resolve_module(dotted, level, info.rel, mods)
        if hit is None and not level and dotted == pkg and "__init__.py" in mods:
            return "__init__.py"
        if hit is None and not level and dotted.split(".")[0] == pkg and dotted != pkg:
            hit = _resolve_module(".".join(dotted.split(".")[1:]), 0, "__root__.py", mods)
        return hit

    for n in ast.walk(info.tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                tgt = resolve(a.name, 0)
                if tgt:
                    ctx.graph.add_edge(info.node_id, mods[tgt].node_id, "imports",
                                       Evidence(EXACT, LENS, info.rel, n.lineno, f"import {a.name}"))
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            pkg_mod = resolve(base, n.level) if base or n.level else None
            for a in n.names:
                # `from pkg import submodule` points at the submodule, not at pkg/__init__.py.
                sub = resolve(f"{base}.{a.name}" if base else a.name, n.level)
                tgt = sub or pkg_mod
                if not tgt:
                    continue
                ctx.graph.add_edge(info.node_id, mods[tgt].node_id, "imports",
                                   Evidence(EXACT, LENS, info.rel, n.lineno, f"from {'.' * n.level}{base} import {a.name}"))
                if not sub:
                    info.imported_names[a.asname or a.name] = (tgt, a.name)


# ---------------------------------------------------------------- value resolution
def _resolve(expr, info: ModuleInfo, params: list[str]):
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return ("lit", expr.value)
    if isinstance(expr, ast.Name):
        if expr.id in params:
            return ("param", expr.id, _SLOT)
        if expr.id in info.constants:
            return ("lit", info.constants[expr.id])
        return None
    if isinstance(expr, ast.JoinedStr):
        out, complete, param = [], True, None
        for part in expr.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                r = _resolve(part.value, info, params)
                if r and r[0] == "lit":
                    out.append(r[1])
                elif r and r[0] == "param" and r[2] == _SLOT and param is None:
                    param = r[1]
                    out.append(_SLOT)  # f"select * from {table}" inside a wrapper: filled in at call sites
                else:
                    out.append("{…}")
                    complete = False
        if param and complete:
            return ("param", param, "".join(out))
        text = "".join(out).replace(_SLOT, "{…}")
        return ("lit" if complete and not param else "partial", text)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        a, b = _resolve(expr.left, info, params), _resolve(expr.right, info, params)
        if a and b and a[0] == b[0] == "lit":
            return ("lit", a[1] + b[1])
    return None


def _chain(call: ast.Call) -> list[tuple[str, ast.Call | None]]:
    """spark.read.format(x).option(k, v).load() -> [('spark',None),('read',None),('format',c),('option',c),('load',c)]"""
    items = []
    node = call
    while True:
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                items.append((f.attr, node))
                node = f.value
            elif isinstance(f, ast.Name):
                items.append((f.id, node))
                break
            else:
                break
        elif isinstance(node, ast.Attribute):
            items.append((node.attr, None))
            node = node.value
        elif isinstance(node, ast.Name):
            items.append((node.id, None))
            break
        else:
            break
    return list(reversed(items))


_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _enclosing_functions(tree: ast.Module) -> dict[int, ast.FunctionDef]:
    """id(ast node) -> innermost function that contains it."""
    out: dict[int, ast.FunctionDef] = {}

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            if fn is not None:
                out[id(child)] = fn
            visit(child, child if isinstance(child, _FUNCS) else fn)

    visit(tree, None)
    return out


def _params(fn) -> list[str]:
    if fn is None:
        return []
    a = fn.args
    return [x.arg for x in a.posonlyargs + a.args + a.kwonlyargs]


# ---------------------------------------------------------------- access detection
def _find_accesses(ctx: Context, info: ModuleInfo) -> None:
    enclosing = _enclosing_functions(info.tree)
    for node in ast.walk(info.tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        fn = enclosing.get(id(node))
        params = _params(fn)
        for acc in _accesses_for_call(node, info, params):
            acc.func = fn.name if fn else None
            if acc.value and acc.value[0] == "param" and fn is not None:
                idx = params.index(acc.value[1])
                info.wrappers.setdefault(fn.name, []).append((idx, acc.value[1], acc))
                continue
            _emit(ctx, info, acc)


def _accesses_for_call(call: ast.Call, info: ModuleInfo, params: list[str]) -> list[Access]:
    attr = call.func.attr
    chain = _chain(call)
    names = [n for n, _ in chain]
    out: list[Access] = []
    line = call.lineno

    # Spark-style builder chains: only analyse at the terminal call.
    if attr in ("load", "save", "saveAsTable", "insertInto", "table", *_SPARK_FILE_FMT) and (
        "read" in names or "write" in names or "readStream" in names or "writeStream" in names
        or (attr == "table" and names[0] in _SESSION_NAMES)
    ):
        op = "writes" if ("write" in names or "writeStream" in names) else "reads"
        table = query = path = None
        schema = None
        for name, c in chain:
            if c is None:
                continue
            if name == "option" and len(c.args) >= 2 and isinstance(c.args[0], ast.Constant):
                key = str(c.args[0].value).lower()
                val = _resolve(c.args[1], info, params)
                if key in ("dbtable", "table"):
                    table = val
                elif key == "query":
                    query = val
                elif key in ("sfschema", "schema"):
                    schema = ("exact", val[1]) if val and val[0] == "lit" else schema
                elif key == "path":
                    path = val
            elif name == "options":
                for kw in c.keywords:
                    if kw.arg is None and isinstance(kw.value, ast.Call) and kw.value.args:
                        v = _resolve(kw.value.args[0], info, params)
                        if v and v[0] == "lit":
                            fname = ast.unparse(kw.value.func)
                            schema = ("inferred", v[1], f"**{fname}({v[1]!r})")
                    elif kw.arg and kw.arg.lower() in ("sfschema", "schema"):
                        v = _resolve(kw.value, info, params)
                        if v and v[0] == "lit":
                            schema = ("exact", v[1])
        if attr in ("saveAsTable", "insertInto", "table") and call.args:
            table = _resolve(call.args[0], info, params)
        if attr in _SPARK_FILE_FMT and call.args:
            path = _resolve(call.args[0], info, params)
        if attr in ("load", "save") and call.args and not table:
            path = _resolve(call.args[0], info, params)
        src = ".".join(names[:2])
        if table:
            out.append(Access(op, "table", table, schema, line, None, f"{src}… option('dbtable')"))
        if query:
            out.append(Access("reads", "sql", query, schema, line, None, f"{src}… option('query')"))
        if path and not table:
            out.append(Access(op, "file", path, None, line, None, f"{src}… {attr}()"))
        if not (table or query or path):
            out.append(Access(op, "table", None, schema, line, None, f"{src}… {attr}() sin destino literal"))
        return out

    # Model artifacts: joblib.dump(model, path) / joblib.load(path).
    if names and names[0] == "joblib" and attr in ("dump", "load"):
        idx = 1 if attr == "dump" else 0
        if len(call.args) > idx:
            op = "writes" if attr == "dump" else "reads"
            return [Access(op, "file", _resolve(call.args[idx], info, params), None, line, None, f"joblib.{attr}()")]

    # pandas / polars I/O.
    if attr in _READERS and call.args:
        return [Access("reads", "file", _resolve(call.args[0], info, params), None, line, None, f"{attr}()")]
    if attr in _WRITERS and call.args:
        return [Access("writes", "file", _resolve(call.args[0], info, params), None, line, None, f".{attr}()")]
    if attr == "to_sql" and call.args:
        return [Access("writes", "table", _resolve(call.args[0], info, params), None, line, None, ".to_sql()")]
    if attr == "read_sql_table" and call.args:
        return [Access("reads", "table", _resolve(call.args[0], info, params), None, line, None, "read_sql_table()")]

    # SQL strings handed to a driver / session.
    if attr in _SQL_CALLS and call.args:
        val = _resolve(call.args[0], info, params)
        if val and val[0] in ("lit", "partial") and _SQL_HINT.search(val[1]):
            return [Access("sql", "sql", val, None, line, None, f".{attr}(SQL)")]
        if val and val[0] == "param":
            return [Access("sql", "sql", val, None, line, None, f".{attr}(SQL)")]
    return out


# ---------------------------------------------------------------- emitting edges
def _emit(ctx: Context, info: ModuleInfo, acc: Access, via: str | None = None, value=None) -> None:
    g = ctx.graph
    value = value if value is not None else acc.value
    where = f" en {acc.func}()" if acc.func else ""
    detail = acc.detail + where + (f" · vía {via}" if via else "")
    me = info.node_id
    schema_name, schema_conf = None, EXACT
    if acc.schema:
        schema_name = acc.schema[1]
        if acc.schema[0] == "inferred":
            schema_conf = INFERRED
            detail += f" · schema {acc.schema[1]} por {acc.schema[2]}"

    if acc.kind == "sql":
        if not value or value[0] not in ("lit", "partial"):
            _unresolved(ctx, info, acc, detail)
            return
        for a in sqlextract.extract(value[1].replace("{…}", "{{ x }}")):
            tid = _table(ctx, a.name, schema_name, a.is_stage, a.confidence)
            conf = _weakest(a.confidence, schema_conf, INFERRED if via else EXACT, INFERRED)
            ev = Evidence(conf, LENS, info.rel, acc.line, f"{detail} → {a.detail}")
            if a.op == "reads":
                g.add_edge(tid, me, "reads", ev)
            else:
                g.add_edge(me, tid, "writes", ev)
        return

    if not value or value[0] != "lit":
        _unresolved(ctx, info, acc, detail)
        return
    if acc.kind == "table":
        tid = _table(ctx, value[1], schema_name, False, EXACT)
        conf = _weakest(schema_conf, INFERRED if via else EXACT, INFERRED)
    else:  # file
        if _SECRETISH.search(value[1]):
            return
        tid = f"file:{value[1]}"
        g.add_node(Node(tid, "file", value[1].rsplit("/", 1)[-1], lens=LENS, meta={"path": value[1]}))
        conf = INFERRED
    ev = Evidence(conf, LENS, info.rel, acc.line, detail)
    if acc.op == "reads":
        g.add_edge(tid, me, "reads", ev)
    else:
        g.add_edge(me, tid, "writes", ev)


def _weakest(*levels: str) -> str:
    rank = {EXACT: 3, INFERRED: 2, UNRESOLVED: 1}
    return min(levels, key=rank.__getitem__)


def _table(ctx: Context, name: str, schema: str | None, is_stage: bool, conf: str) -> str:
    g = ctx.graph
    if conf == UNRESOLVED:
        nid = f"unresolved:{name}"
        g.add_node(Node(nid, "unknown", name, lens=LENS, meta={"note": "nombre dinámico"}))
        return nid
    if is_stage:
        sid, _ = ids.stage_id(name)
        g.add_node(Node(sid, "stage", "@" + ids.table_label(sid), parent=ids.group_id_for(sid), lens=LENS))
        return sid
    tid, meta = ids.table_id(name, schema=schema)
    g.add_node(Node(tid, "table", ids.table_label(tid), parent=ids.group_id_for(tid), lens=LENS, meta=meta))
    return tid


def _unresolved(ctx: Context, info: ModuleInfo, acc: Access, detail: str) -> None:
    g = ctx.graph
    nid = f"unresolved:{info.rel}:{acc.line}"
    label = {"table": "¿tabla dinámica?", "file": "¿archivo dinámico?"}.get(acc.kind, "¿SQL dinámico?")
    g.add_node(Node(nid, "unknown", label, lens=LENS, file=info.rel, line=acc.line,
                    meta={"note": "no se pudo resolver el nombre de forma estática"}))
    ev = Evidence(UNRESOLVED, LENS, info.rel, acc.line, detail)
    if acc.op == "writes":
        g.add_edge(info.node_id, nid, "writes", ev)
    else:
        g.add_edge(nid, info.node_id, "reads", ev)


def _wrapper_calls(ctx: Context, info: ModuleInfo, mods: dict) -> None:
    """Calls like read_gold("FACT_SALES") to a function that forwards its argument to a data access."""
    enclosing = _enclosing_functions(info.tree)
    for node in ast.walk(info.tree):
        if not isinstance(node, ast.Call):
            continue
        target_mod, fname = None, None
        if isinstance(node.func, ast.Name):
            if node.func.id in info.wrappers:
                target_mod, fname = info, node.func.id
            elif node.func.id in info.imported_names:
                mrel, n = info.imported_names[node.func.id]
                if n in mods[mrel].wrappers:
                    target_mod, fname = mods[mrel], n
        if not target_mod:
            continue
        caller = enclosing.get(id(node))
        params = _params(caller)
        for idx, pname, acc in target_mod.wrappers[fname]:
            arg = node.args[idx] if idx < len(node.args) else next(
                (k.value for k in node.keywords if k.arg == pname), None)
            val = _resolve(arg, info, params) if arg is not None else None
            if val and val[0] == "param":
                continue  # wrapper of a wrapper: out of scope for now
            template = acc.value[2]
            if val and val[0] in ("lit", "partial"):
                val = (val[0], template.replace(_SLOT, val[1]))
            call_acc = Access(acc.op, acc.kind, val, acc.schema, node.lineno,
                              caller.name if caller else None, acc.detail)
            via = f"{fname}({ast.unparse(arg) if arg is not None else '…'})"
            _emit(ctx, info, call_acc, via=via, value=val)
