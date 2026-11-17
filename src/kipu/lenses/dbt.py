"""dbt lens: models, sources and seeds as tables, wired by ref() / source().

Identity (which SCHEMA a model lands in) comes from target/manifest.json when it is fresher than
the project files; otherwise it is inferred from dbt_project.yml (+schema), profiles.yml (only the
`schema`/`database` fields are read) and whether a custom generate_schema_name macro exists.
The ref()/source() edges themselves are always exact: dbt declares them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from kipu import ids, yamlx
from kipu.files import IGNORE_DIRS, leading_comment, read_text, rel, short_hash
from kipu.graph import EXACT, INFERRED, Evidence, Node
from kipu.lenses import Context

LENS = "dbt"
_REF = re.compile(r"\{\{\s*ref\(\s*['\"]([\w.]+)['\"]\s*(?:,\s*['\"]([\w.]+)['\"]\s*)?(?:,[^)]*)?\)\s*\}\}")
_SOURCE = re.compile(r"\{\{\s*source\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)\s*\}\}")
_CONFIG = re.compile(r"\{\{\s*config\((.*?)\)\s*\}\}", re.S)
_KV = re.compile(r"(\w+)\s*=\s*['\"]([^'\"]*)['\"]")


def find_projects(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("dbt_project.yml")):
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        out.append(p.parent)
    return out


def scan(ctx: Context) -> None:
    for proj in find_projects(ctx.root):
        _scan_project(ctx, proj)


def _profile_schema(proj: Path, profile: str | None) -> tuple[str | None, str | None]:
    """Read ONLY schema/database of the default target from a profiles.yml next to the project."""
    path = proj / "profiles.yml"
    if not path.exists() or not profile:
        return None, None
    try:
        data = yamlx.load_plain(read_text(path)) or {}
    except Exception:
        return None, None
    prof = data.get(profile) or {}
    out = (prof.get("outputs") or {}).get(prof.get("target") or "", {}) or {}
    schema, db = out.get("schema"), out.get("database")
    schema = None if not isinstance(schema, str) or "{{" in schema else schema
    db = None if not isinstance(db, str) or "{{" in db else db
    return schema, db


def _folder_configs(cfg: dict, project_name: str) -> dict[tuple[str, ...], dict]:
    """dbt_project.yml models: {project: {folder: {+schema: x}}} -> {('folder',): {'schema': x}}."""
    out: dict[tuple[str, ...], dict] = {}

    def walk(node, path):
        if not isinstance(node, dict):
            return
        own = {k.lstrip("+"): v for k, v in node.items() if isinstance(k, str) and k.startswith("+")}
        if own:
            out[path] = own
        for k, v in node.items():
            if isinstance(k, str) and not k.startswith("+") and isinstance(v, dict) and not k.startswith("__"):
                walk(v, path + (k,))

    walk((cfg.get("models") or {}).get(project_name) or {}, ())
    walk({k: v for k, v in ((cfg.get("seeds") or {}).get(project_name) or {}).items()}, ("__seeds__",))
    return out


def _resolve_cfg(folder_cfgs, parts: tuple[str, ...]) -> dict:
    merged: dict = {}
    for i in range(len(parts) + 1):
        merged.update(folder_cfgs.get(parts[:i], {}))
    return merged


def _schema_for(custom: str | None, target_schema: str | None, custom_macro: bool) -> tuple[str | None, str]:
    if not custom:
        return target_schema, "schema del perfil (profiles.yml)"
    if custom_macro:
        return custom, "macro generate_schema_name propio: usa +schema tal cual"
    if target_schema:
        return f"{target_schema}_{custom}", "regla por defecto de dbt: <schema del perfil>_<+schema>"
    return custom, "+schema (sin schema de perfil conocido)"


def _scan_project(ctx: Context, proj: Path) -> None:
    g, root = ctx.graph, ctx.root
    cfg_text = read_text(proj / "dbt_project.yml")
    cfg = yamlx.load_plain(cfg_text) or {}
    name = cfg.get("name", proj.name)
    pid = f"dbt:{name}"
    ctx.dbt_projects.append(pid)
    model_dirs = [proj / d for d in cfg.get("model-paths", ["models"])]
    seed_dirs = [proj / d for d in cfg.get("seed-paths", ["seeds"])]
    macro_dirs = [proj / d for d in cfg.get("macro-paths", ["macros"])]
    target_schema, target_db = _profile_schema(proj, cfg.get("profile"))
    custom_macro = any(
        "generate_schema_name" in read_text(p) for d in macro_dirs if d.exists() for p in d.rglob("*.sql")
    )
    folder_cfgs = _folder_configs(cfg, name)

    files = [p for d in model_dirs + seed_dirs + macro_dirs if d.exists() for p in d.rglob("*") if p.is_file()]
    files.append(proj / "dbt_project.yml")
    newest = max((p.stat().st_mtime for p in files), default=0)
    manifest_path = proj / "target" / "manifest.json"
    manifest = None
    if manifest_path.exists() and manifest_path.stat().st_mtime >= newest:
        try:
            manifest = json.loads(read_text(manifest_path))
        except Exception:
            manifest = None
    identity_source = "manifest.json (fresco)" if manifest else (
        "manifest.json desactualizado: se infiere de la configuración" if manifest_path.exists()
        else "sin manifest.json: se infiere de la configuración")

    g.add_node(Node(pid, "dbt_project", f"dbt · {name}", file=rel(root, proj / "dbt_project.yml"), line=1,
                    lens=LENS, defined=True, hash=short_hash(cfg_text),
                    meta={"identity": identity_source, "target_schema": target_schema}))

    # ---- sources ----
    sources: dict[tuple[str, str], str] = {}  # (source, table) -> table id
    model_docs: dict[str, str] = {}
    for d in model_dirs:
        if not d.exists():
            continue
        for yml in sorted(list(d.rglob("*.yml")) + list(d.rglob("*.yaml"))):
            try:
                doc = yamlx.load(read_text(yml)) or {}
            except Exception:
                continue
            for m in doc.get("models") or []:
                if isinstance(m, dict) and m.get("description"):
                    model_docs[m.get("name")] = m["description"]
            for src in doc.get("sources") or []:
                sname = src.get("name")
                for t in src.get("tables") or []:
                    ident = t.get("identifier") or t.get("name")
                    schema = src.get("schema") or sname
                    tid, tmeta = ids.table_id(ident, schema=schema, database=src.get("database") or target_db)
                    sources[(sname, t.get("name"))] = tid
                    g.add_node(Node(tid, "table", ids.table_label(tid), parent=ids.group_id_for(tid),
                                    file=rel(root, yml), line=yamlx.line_of(t), lens=LENS,
                                    meta={**tmeta, "dbt_source": f"{sname}.{t.get('name')}",
                                          **({"description": t["description"].strip()} if t.get("description") else {})}))

    if manifest:
        for key, s in (manifest.get("sources") or {}).items():
            tid, _ = ids.table_id(s.get("identifier") or s["name"], schema=s.get("schema"))
            sources[(s["source_name"], s["name"])] = tid

    # ---- models and seeds ----
    model_ids: dict[str, str] = {}
    pending: list[tuple[str, Path, str, str]] = []  # (model name, path, sql, tid)
    for d in model_dirs:
        if not d.exists():
            continue
        for sql_path in sorted(d.rglob("*.sql")):
            sql = read_text(sql_path)
            mname = sql_path.stem
            inline = dict(_KV.findall(_CONFIG.search(sql).group(1))) if _CONFIG.search(sql) else {}
            folder = sql_path.relative_to(d).parts[:-1]
            conf = {**_resolve_cfg(folder_cfgs, folder), **inline}
            ident = inline.get("alias") or mname
            mnode = _manifest_node(manifest, name, mname)
            if mnode:
                tid, tmeta = ids.table_id(mnode.get("alias") or ident, schema=mnode.get("schema"),
                                          database=mnode.get("database"))
                why, conf_level = "manifest.json", EXACT
                materialized = (mnode.get("config") or {}).get("materialized") or conf.get("materialized")
            else:
                schema, why = _schema_for(conf.get("schema"), target_schema, custom_macro)
                tid, tmeta = ids.table_id(ident, schema=schema, database=target_db)
                conf_level = INFERRED
                materialized = conf.get("materialized", "view")
            model_ids[mname] = tid
            meta = {**tmeta, "dbt_model": mname, "dbt_project": pid, "materialized": materialized,
                    "layer": "/".join(folder) or None,
                    "identity": why, "identity_confidence": conf_level}
            if model_docs.get(mname):
                meta["description"] = model_docs[mname].strip()
            note = leading_comment(_CONFIG.sub("", sql))
            if note:
                meta["comment"] = note
            g.add_node(Node(tid, "table", mname, parent=ids.group_id_for(tid), file=rel(root, sql_path), line=1,
                            lens=LENS, defined=True, hash=short_hash(sql), meta=meta))
            pending.append((mname, sql_path, sql, tid))

    for sd in seed_dirs:
        if not sd.exists():
            continue
        for csv in sorted(sd.rglob("*.csv")):
            conf = _resolve_cfg(folder_cfgs, ("__seeds__",) + csv.relative_to(sd).parts[:-1])
            snode = _manifest_node(manifest, name, csv.stem, "seed")
            if snode:
                tid, tmeta = ids.table_id(snode.get("alias") or csv.stem, schema=snode.get("schema"),
                                          database=snode.get("database"))
                why = "manifest.json"
            else:
                schema, why = _schema_for(conf.get("schema"), target_schema, custom_macro)
                tid, tmeta = ids.table_id(csv.stem, schema=schema, database=target_db)
            model_ids[csv.stem] = tid
            g.add_node(Node(tid, "table", csv.stem, parent=ids.group_id_for(tid), file=rel(root, csv), line=1,
                            lens=LENS, defined=True,
                            meta={**tmeta, "dbt_seed": csv.stem, "dbt_project": pid, "identity": why}))

    # ---- edges: ref() and source() ----
    for mname, sql_path, sql, tid in pending:
        for m in _REF.finditer(sql):
            parent = m.group(2) or m.group(1)
            line = sql.count("\n", 0, m.start()) + 1
            ev = Evidence(EXACT, LENS, rel(root, sql_path), line, f"ref('{parent}')")
            if parent in model_ids:
                g.add_edge(model_ids[parent], tid, "feeds", ev)
            else:
                ghost = f"table:{parent.upper()}"
                g.add_node(Node(ghost, "table", parent, lens=LENS, meta={"note": "ref() a un modelo no encontrado"}))
                g.add_edge(ghost, tid, "feeds", Evidence(INFERRED, LENS, ev.file, ev.line, ev.detail))
        for m in _SOURCE.finditer(sql):
            line = sql.count("\n", 0, m.start()) + 1
            src = sources.get((m.group(1), m.group(2)))
            ev_detail = f"source('{m.group(1)}', '{m.group(2)}')"
            if src:
                g.add_edge(src, tid, "feeds", Evidence(EXACT, LENS, rel(root, sql_path), line, ev_detail))
            else:
                ghost, _ = ids.table_id(m.group(2), schema=m.group(1))
                g.add_node(Node(ghost, "table", ids.table_label(ghost), parent=ids.group_id_for(ghost), lens=LENS))
                g.add_edge(ghost, tid, "feeds", Evidence(INFERRED, LENS, rel(root, sql_path), line,
                                                         ev_detail + " (no declarado en sources)"))

    ctx.report[pid] = {"models": len(pending), "sources": len(sources), "identity": identity_source}


def _manifest_node(manifest, project: str, name: str, resource: str = "model"):
    if not manifest:
        return None
    return (manifest.get("nodes") or {}).get(f"{resource}.{project}.{name}")
