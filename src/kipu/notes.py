"""Plain-language explanations written by a coding agent, tied to the code they explain.

The rule: the code decides the facts, the agent only writes the words.

- `todo()` hands the agent the facts kipu extracted (what a node is, what it connects to, its code).
- `save()` stores one short explanation per node, stamped with the node's content hash, and refuses
  ids that are not in the graph (an agent cannot add things to the map by describing them).
- `attach()` puts the notes on the graph; a note whose code changed since it was written is marked stale.

Notes live in the output folder (`notes.json`), next to graph.json. They are never facts: the viewer
always shows them marked as written by AI.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from kipu.files import is_secret

NOTES_FILE = "notes.json"
MAX_WHAT, MAX_WHY, MAX_SUMMARY = 170, 420, 420
VERB = {"reads": "lee", "writes": "escribe en", "feeds": "alimenta a", "passes": "entrega su resultado a",
        "runs": "ejecuta", "triggers": "dispara", "imports": "importa"}


def load(out: Path) -> dict:
    path = out / NOTES_FILE
    if not path.exists():
        return {"version": 1, "summary": None, "nodes": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"version": 1, "summary": None, "nodes": {}}
    data.setdefault("nodes", {})
    data.setdefault("summary", None)
    return data


def structure_hash(graph: dict) -> str:
    ids = sorted(n["id"] for n in graph["nodes"]) + sorted(e["id"] for e in graph["edges"] if e["kind"] != "next")
    return hashlib.sha1("\n".join(ids).encode()).hexdigest()[:12]


def attach(graph: dict, out: Path) -> None:
    """Annotate graph nodes with their note and whether it is stale."""
    notes = load(out)
    fresh = stale = 0
    explainable = 0
    for n in graph["nodes"]:
        explainable += 1
        note = notes["nodes"].get(n["id"])
        if not note:
            continue
        is_stale = note.get("hash") != n.get("hash")
        n["note"] = {"what": note["what"], **({"why": note["why"]} if note.get("why") else {}), "stale": is_stale}
        stale += is_stale
        fresh += not is_stale
    s = notes.get("summary")
    if s:
        graph["summary"] = {"text": s["text"], "stale": s.get("hash") != structure_hash(graph)}
    graph["notes"] = {"fresh": fresh, "stale": stale, "missing": explainable - fresh - stale}


def save(graph: dict, out: Path, incoming: dict) -> dict:
    """Merge {"summary": str, "nodes": {id: {"what": str, "why": str}}} into notes.json. Returns a report."""
    notes = load(out)
    by_id = {n["id"]: n for n in graph["nodes"]}
    saved, errors = [], []
    for nid, val in (incoming.get("nodes") or {}).items():
        if isinstance(val, str):
            val = {"what": val}
        what, why = (val.get("what") or "").strip(), (val.get("why") or "").strip()
        if nid not in by_id:
            errors.append(f"{nid}: no existe en el mapa (solo se pueden explicar nodos que kipu encontró)")
        elif not what:
            errors.append(f"{nid}: falta 'what'")
        elif len(what) > MAX_WHAT:
            errors.append(f"{nid}: 'what' tiene {len(what)} caracteres (máximo {MAX_WHAT}); una frase corta")
        elif len(why) > MAX_WHY:
            errors.append(f"{nid}: 'why' tiene {len(why)} caracteres (máximo {MAX_WHY})")
        else:
            notes["nodes"][nid] = {"what": what, **({"why": why} if why else {}), "hash": by_id[nid].get("hash")}
            saved.append(nid)
    summary = incoming.get("summary")
    if isinstance(summary, str) and summary.strip():
        if len(summary.strip()) > MAX_SUMMARY:
            errors.append(f"summary: {len(summary.strip())} caracteres (máximo {MAX_SUMMARY})")
        else:
            notes["summary"] = {"text": summary.strip(), "hash": structure_hash(graph)}
            saved.append("summary")
    for nid in [i for i in notes["nodes"] if i not in by_id]:      # explanations of things that no longer exist
        del notes["nodes"][nid]
    out.mkdir(parents=True, exist_ok=True)
    (out / NOTES_FILE).write_text(json.dumps(notes, ensure_ascii=False, indent=1))
    return {"saved": saved, "errors": errors}


def _snippet(root: Path, node: dict, max_lines: int) -> str:
    file, line = node.get("file"), node.get("line") or 1
    if not file or is_secret(Path(file).name) or file.endswith(".csv"):
        return ""
    try:
        lines = (root / file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = line - 1
    while start > 0 and lines[start - 1].strip().startswith(("#", "--")):       # include the comment above
        start -= 1
    chunk = lines[start:start + max_lines]
    more = len(lines) - (start + max_lines)
    return "\n".join(chunk) + (f"\n… ({more} líneas más)" if more > 0 else "")


def todo(graph: dict, root: Path, include_all: bool = False, code: bool = True, limit: int = 60,
         max_lines: int = 24) -> str:
    """Markdown brief for the agent: the facts of every node that needs an explanation."""
    labels = {n["id"]: n["label"] for n in graph["nodes"]}
    pending = [n for n in graph["nodes"] if include_all or not n.get("note") or n["note"]["stale"]]
    out = [f"# kipu · explicaciones pendientes de {graph['project']}", ""]
    s = graph.get("summary")
    if include_all or not s or s["stale"]:
        out += ["## summary", "Falta el resumen del proyecto: 1 o 2 frases sobre qué hace de punta a punta."
                + (f" Anterior (desactualizado): {s['text']}" if s else ""), ""]
    out.append(f"{len(pending)} nodos sin explicación vigente" + (f" (se muestran {limit})" if len(pending) > limit else "") + ".")
    out.append("")
    for n in pending[:limit]:
        m = n.get("meta") or {}
        out.append(f"## {n['id']}")
        where = f" · {n['file']}:{n.get('line', 1)}" if n.get("file") else ""
        out.append(f"- qué es: {n['kind']} `{n['label']}`{where}")
        if n.get("parent"):
            out.append(f"- dentro de: {labels.get(n['parent'], n['parent'])}")
        if n.get("note"):
            out.append(f"- explicación anterior (el código cambió): {n['note']['what']}")
        for key in ("description", "comment"):
            if m.get(key):
                out.append(f"- {key} en el código: {m[key]}")
        for e in graph["edges"]:
            if e["kind"] == "next":
                continue
            if e["source"] == n["id"]:
                out.append(f"- {VERB.get(e['kind'], e['kind'])} `{labels.get(e['target'], e['target'])}` [{e['confidence']}]")
            elif e["target"] == n["id"]:
                out.append(f"- `{labels.get(e['source'], e['source'])}` {VERB.get(e['kind'], e['kind'])} esto [{e['confidence']}]")
        snippet = _snippet(root, n, max_lines) if code else ""
        if snippet:
            out += ["```", snippet, "```"]
        out.append("")
    return "\n".join(out)
