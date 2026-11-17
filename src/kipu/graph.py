"""Graph model: nodes, edges, and the evidence (provenance) behind every edge.

Every edge carries one or more pieces of evidence. Each piece says where it was found
(file:line), which lens found it, and how sure we are:

- exact:      the code states it explicitly (a dbt ref(), a SQL INSERT INTO a literal table).
- inferred:   derived by a rule that can be wrong (a wrapper function, a shell command, a schema default).
- unresolved: we know something is read or written but not what (a dynamic table name).
"""
from __future__ import annotations

from dataclasses import dataclass, field

EXACT, INFERRED, UNRESOLVED = "exact", "inferred", "unresolved"
_RANK = {EXACT: 3, INFERRED: 2, UNRESOLVED: 1}

# Edge kinds. Direction always follows the flow (data or control), never "depends on".
DATA_KINDS = {"reads", "writes", "feeds"}
CONTROL_KINDS = {"runs", "triggers", "passes", "next"}
CODE_KINDS = {"imports"}


@dataclass
class Node:
    id: str
    kind: str
    label: str
    parent: str | None = None
    file: str | None = None
    line: int | None = None
    lens: str = ""
    defined: bool = False  # True when this lens is where the thing is defined (owns label/file/line)
    meta: dict = field(default_factory=dict)
    hash: str | None = None  # content hash of the defining snippet, for change detection

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "label": self.label, "lens": self.lens}
        for k in ("parent", "file", "line", "hash"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.meta:
            d["meta"] = self.meta
        return d


@dataclass
class Evidence:
    confidence: str
    lens: str
    file: str | None = None
    line: int | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        d = {"confidence": self.confidence, "lens": self.lens}
        if self.file:
            d["file"] = self.file
        if self.line:
            d["line"] = self.line
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class Edge:
    source: str
    target: str
    kind: str
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        return max((e.confidence for e in self.evidence), key=_RANK.__getitem__, default=UNRESOLVED)

    def to_dict(self) -> dict:
        return {
            "id": f"{self.source}|{self.kind}|{self.target}",
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "confidence": self.confidence,
            "evidence": [e.to_dict() for e in self.evidence],
        }


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}

    # -- nodes -------------------------------------------------------------
    def add_node(self, node: Node) -> Node:
        cur = self.nodes.get(node.id)
        if cur is None:
            self.nodes[node.id] = node
            return node
        # Merge: the defining lens owns label/file/line/parent/hash; others only fill gaps.
        if node.defined and not cur.defined:
            cur.label, cur.file, cur.line, cur.lens = node.label, node.file, node.line, node.lens
            cur.defined = True
            if node.hash:
                cur.hash = node.hash
            if node.kind != cur.kind:
                cur.kind = node.kind
        for attr in ("parent", "file", "line", "hash"):
            if getattr(cur, attr) is None and getattr(node, attr) is not None:
                setattr(cur, attr, getattr(node, attr))
        for k, v in node.meta.items():
            cur.meta.setdefault(k, v)
        return cur

    # -- edges -------------------------------------------------------------
    def add_edge(self, source: str, target: str, kind: str, evidence: Evidence) -> Edge | None:
        if source == target:
            return None
        key = (source, target, kind)
        edge = self.edges.get(key)
        if edge is None:
            edge = self.edges[key] = Edge(source, target, kind)
        if not any(
            (e.file, e.line, e.detail) == (evidence.file, evidence.line, evidence.detail) for e in edge.evidence
        ):
            edge.evidence.append(evidence)
        return edge

    def redirect(self, old_id: str, new_id: str, note: str) -> None:
        """Merge node old_id into new_id (identity resolution). Evidence is downgraded to inferred."""
        old = self.nodes.pop(old_id, None)
        if old is None:
            return
        if new_id in self.nodes:
            tgt = self.nodes[new_id]
            for k, v in old.meta.items():
                tgt.meta.setdefault(k, v)
            tgt.meta.setdefault("aliases", []).append(old.label)
        for key in [k for k in self.edges if old_id in (k[0], k[1])]:
            edge = self.edges.pop(key)
            s = new_id if edge.source == old_id else edge.source
            t = new_id if edge.target == old_id else edge.target
            for ev in edge.evidence:
                conf = INFERRED if ev.confidence == EXACT else ev.confidence
                detail = f"{ev.detail} · {note}" if ev.detail else note
                self.add_edge(s, t, edge.kind, Evidence(conf, ev.lens, ev.file, ev.line, detail))
        for n in self.nodes.values():
            if n.parent == old_id:
                n.parent = new_id

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes.values()],
            "edges": [e.to_dict() for e in self.edges.values()],
        }
