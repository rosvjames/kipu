"""Lenses: one per technology. Each one only adds nodes and edges to the shared graph."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kipu.graph import Graph


@dataclass
class Context:
    root: Path
    graph: Graph
    report: dict = field(default_factory=dict)  # per-lens notes shown in the viewer ("manifest used", counts...)
    python_modules: dict[str, str] = field(default_factory=dict)  # basename.py -> module node id
    dbt_projects: list[str] = field(default_factory=list)  # dbt project node ids


def run_all(ctx: Context) -> None:
    from kipu.lenses import dbt, kestra, python

    # Order matters: Python and dbt register what Kestra commands can point to.
    python.scan(ctx)
    dbt.scan(ctx)
    kestra.scan(ctx)
