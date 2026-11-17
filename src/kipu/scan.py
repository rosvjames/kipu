"""Run every lens over a project, resolve identities, and compare against the session baseline."""
from __future__ import annotations

import json
import subprocess
import heapq
from datetime import datetime, timezone
from pathlib import Path

from kipu import __version__, ids, notes
from kipu.graph import CONTROL_KINDS, DATA_KINDS, Evidence, Graph, Node
from kipu.lenses import Context, run_all

GRAPH_FILE, BASELINE_FILE = "graph.json", "baseline.json"


def default_out(root: Path) -> Path:
    return root / ".kipu"


def build_graph(root: Path) -> dict:
    root = root.resolve()
    g = Graph()
    ctx = Context(root=root, graph=g)
    run_all(ctx)
    _resolve_identities(g)
    _fold_read_backs(g)
    _ensure_groups_and_endpoints(g)
    data = g.to_dict()
    _mark_uncommitted(root, data)
    data.update({
        "kipu": __version__,
        "project": root.name,
        "root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report": ctx.report,
    })
    return data


def _fold_read_backs(g: Graph) -> None:
    """A step that writes a table and also reads it back (row counts, verification) is not a new flow.

    The read is kept as evidence on the write edge instead of a backwards arrow that creates a cycle.
    """
    for (s, t, k) in list(g.edges):
        if k != "reads" or (t, s, "writes") not in g.edges:
            continue
        read = g.edges.pop((s, t, k))
        write = g.edges[(t, s, "writes")]
        for ev in read.evidence:
            write.evidence.append(Evidence(ev.confidence, ev.lens, ev.file, ev.line,
                                           f"también lo vuelve a leer: {ev.detail}".strip()))


def _resolve_identities(g: Graph) -> None:
    """table:NAME (schema unknown) -> table:SCHEMA.NAME when exactly one candidate exists."""
    for prefix in ("table:", "stage:"):
        qualified: dict[str, list[str]] = {}
        for nid in g.nodes:
            body = nid[len(prefix):] if nid.startswith(prefix) else None
            if body and "." in body:
                qualified.setdefault(body.split(".")[-1], []).append(nid)
        for nid in [n for n in g.nodes if n.startswith(prefix) and "." not in n[len(prefix):]]:
            cands = qualified.get(nid[len(prefix):], [])
            if len(cands) == 1:
                g.redirect(nid, cands[0], f"schema resuelto: único {cands[0].split(':', 1)[1]} en el proyecto")
            elif len(cands) > 1:
                g.nodes[nid].meta["ambiguous"] = [c.split(":", 1)[1] for c in cands]


def _ensure_groups_and_endpoints(g: Graph) -> None:
    for node in list(g.nodes.values()):
        p = node.parent
        if p and p not in g.nodes:
            kind, name = p.split(":", 1)
            label = name if kind == "schema" else name.rstrip("/") + "/"
            g.add_node(Node(p, kind, label, lens="kipu"))
    for (s, t, _k) in list(g.edges):
        for nid in (s, t):
            if nid not in g.nodes:
                kind = nid.split(":", 1)[0]
                g.add_node(Node(nid, kind, nid.split(":", 1)[1].split(".")[-1], lens="kipu",
                                parent=ids.group_id_for(nid) if kind in ("table", "stage") else None,
                                meta={"note": "referenciado pero no definido en el proyecto"}))
    for node in list(g.nodes.values()):
        if node.parent and node.parent not in g.nodes:
            node.parent = None


def _mark_uncommitted(root: Path, data: dict) -> None:
    try:
        out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return
    if out.returncode != 0:
        return
    top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, timeout=5).stdout.strip()
    prefix = root.resolve().relative_to(Path(top).resolve()).as_posix() if top else ""
    dirty = set()
    for line in out.stdout.splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        if prefix and prefix != ".":
            if not path.startswith(prefix + "/"):
                continue
            path = path[len(prefix) + 1:]
        dirty.add(path)
    for n in data["nodes"]:
        if n.get("file") in dirty:
            n.setdefault("meta", {})["uncommitted"] = True


def compare(current: dict, baseline: dict | None) -> None:
    """Annotate nodes/edges with change = added | modified, and list removed ones."""
    if not baseline:
        current["changes"] = {"baseline_at": None, "added": 0, "modified": 0, "removed": []}
        return
    bnodes = {n["id"]: n for n in baseline.get("nodes", [])}
    bedges = {e["id"] for e in baseline.get("edges", [])}
    added = modified = 0
    for n in current["nodes"]:
        old = bnodes.get(n["id"])
        if old is None:
            if n["kind"] not in ("schema", "dir"):
                n["change"] = "added"
                added += 1
        elif n.get("hash") and old.get("hash") and n["hash"] != old["hash"]:
            n["change"] = "modified"
            modified += 1
    cur_ids = {n["id"] for n in current["nodes"]}
    for e in current["edges"]:
        if e["id"] not in bedges and baseline.get("nodes"):
            e["change"] = "added"
    removed = [{"id": i, "label": n["label"], "kind": n["kind"]} for i, n in bnodes.items()
               if i not in cur_ids and n["kind"] not in ("schema", "dir")]
    current["changes"] = {"baseline_at": baseline.get("generated_at"), "added": added,
                          "modified": modified, "removed": removed}


def run(root: Path, out: Path | None = None, reset_baseline: bool = False) -> dict:
    root = root.resolve()
    out = (out or default_out(root)).resolve()
    out.mkdir(parents=True, exist_ok=True)
    ignore = out / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# kipu output: never commit\n*\n")
    data = build_graph(root)
    bpath = out / BASELINE_FILE
    if reset_baseline or not bpath.exists():
        bpath.write_text(json.dumps(data, ensure_ascii=False))
    baseline = json.loads(bpath.read_text())
    compare(data, baseline)
    notes.attach(data, out)
    (out / GRAPH_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


def trace(data: dict, src_query: str, dst_query: str, kinds: set[str] | None = None) -> list[dict] | None:
    """Shortest flow path between two nodes matched by id/label substring (case-insensitive)."""
    kinds = kinds or (DATA_KINDS | CONTROL_KINDS)
    nodes = {n["id"]: n for n in data["nodes"]}

    def match(q):
        q = q.lower()
        exact = [i for i, n in nodes.items() if n["label"].lower() == q or i.lower() == q]
        return exact or [i for i, n in nodes.items() if q in i.lower() or q in n["label"].lower()]

    adj: dict[str, list[dict]] = {}
    for e in data["edges"]:
        if e["kind"] in kinds:
            adj.setdefault(e["source"], []).append(e)
    starts, goals = match(src_query), set(match(dst_query))
    # Dijkstra: prefer the path the data takes; task order ("next") only bridges gaps.
    cost = {"next": 3.0}
    dist: dict[str, float] = {s: 0.0 for s in starts}
    prev: dict[str, dict | None] = {s: None for s in starts}
    heap = [(0.0, i, s) for i, s in enumerate(starts)]
    heapq.heapify(heap)
    tie = len(heap)
    while heap:
        d, _, cur = heapq.heappop(heap)
        if d > dist.get(cur, float("inf")):
            continue
        if cur in goals:
            path, node = [], cur
            while prev[node] is not None:
                path.append(prev[node])
                node = prev[node]["source"]
            return list(reversed(path))
        for e in adj.get(cur, []):
            nd = d + cost.get(e["kind"], 1.0)
            if nd < dist.get(e["target"], float("inf")):
                dist[e["target"]], prev[e["target"]] = nd, e
                tie += 1
                heapq.heappush(heap, (nd, tie, e["target"]))
    return None
