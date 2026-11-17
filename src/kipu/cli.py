"""kipu command line."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from kipu import __version__
from kipu import scan as scanner


def _summary(data: dict) -> str:
    kinds = Counter(n["kind"] for n in data["nodes"] if n["kind"] not in ("schema", "dir"))
    conf = Counter(e["confidence"] for e in data["edges"])
    ch = data.get("changes") or {}
    lines = [
        f"kipu {__version__} · {data['project']}",
        "  nodos:   " + ", ".join(f"{v} {k}" for k, v in kinds.most_common()),
        f"  aristas: {len(data['edges'])} ({conf.get('exact', 0)} exactas, {conf.get('inferred', 0)} inferidas, "
        f"{conf.get('unresolved', 0)} sin resolver)",
    ]
    for key, rep in (data.get("report") or {}).items():
        lines.append(f"  {key}: " + ", ".join(f"{k}={v}" for k, v in rep.items()))
    if ch.get("baseline_at"):
        lines.append(f"  cambios desde la línea base: {ch['added']} nuevos, {ch['modified']} modificados, "
                     f"{len(ch['removed'])} eliminados")
    return "\n".join(lines)


def _notes(args, root: Path, data: dict) -> int:
    from kipu import notes

    out = (args.out or scanner.default_out(root)).resolve()
    if args.action == "todo":
        print(notes.todo(data, root, include_all=args.all, code=not args.no_code, limit=args.limit))
        return 0
    if args.action == "status":
        c = data["notes"]
        print(f"explicaciones: {c['fresh']} vigentes, {c['stale']} desactualizadas, {c['missing']} sin escribir"
              + (" · resumen: " + ("desactualizado" if data["summary"]["stale"] else "vigente") if data.get("summary") else " · sin resumen"))
        return 0
    incoming: dict = {}
    if args.file:
        raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
        try:
            incoming = json.loads(raw)
        except ValueError as e:
            print(f"kipu: el JSON no es válido: {e}", file=sys.stderr)
            return 2
    if args.id:
        incoming.setdefault("nodes", {})[args.id] = {"what": args.what or "", "why": args.why or ""}
    if args.summary:
        incoming["summary"] = args.summary
    if not incoming:
        print("kipu: nada que guardar. Usa --file, o --id con --what, o --summary.", file=sys.stderr)
        return 2
    report = notes.save(data, out, incoming)
    scanner.run(root, args.out)          # refresh graph.json so a running viewer picks the notes up
    print(f"guardadas: {len(report['saved'])}")
    for err in report["errors"]:
        print(f"  rechazada · {err}")
    return 1 if report["errors"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kipu", description="See what your AI built.")
    ap.add_argument("--version", action="version", version=f"kipu {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("path", nargs="?", default=".", help="proyecto a analizar (por defecto: .)")
        p.add_argument("--out", type=Path, help="carpeta de salida (por defecto: <proyecto>/.kipu)")

    p_scan = sub.add_parser("scan", help="analiza el proyecto y escribe graph.json")
    common(p_scan)
    p_scan.add_argument("--json", action="store_true", help="imprime el grafo en stdout")

    p_view = sub.add_parser("view", help="abre el visor en localhost y vigila los cambios")
    common(p_view)
    p_view.add_argument("--port", type=int, default=7333)
    p_view.add_argument("--no-open", action="store_true", help="no abrir el navegador")

    p_base = sub.add_parser("baseline", help="reinicia la línea base: los cambios se miden desde ahora")
    common(p_base)

    p_trace = sub.add_parser("trace", help="camino del dato entre dos nodos")
    common(p_trace)
    p_trace.add_argument("--from", dest="src", required=True)
    p_trace.add_argument("--to", dest="dst", required=True)

    p_notes = sub.add_parser("notes", help="explicaciones en lenguaje claro escritas por tu agente de IA")
    p_notes.add_argument("action", choices=["todo", "set", "status"],
                         help="todo: qué falta explicar · set: guardar explicaciones · status: cuántas hay")
    common(p_notes)
    p_notes.add_argument("--file", help="JSON con {summary, nodes: {id: {what, why}}}; '-' para stdin (con set)")
    p_notes.add_argument("--id", help="nodo a explicar (con set)")
    p_notes.add_argument("--what", help="qué es o qué hace, en una frase corta (con set)")
    p_notes.add_argument("--why", help="detalle opcional: por qué existe o qué hay que saber (con set)")
    p_notes.add_argument("--summary", help="resumen del proyecto en 1 o 2 frases (con set)")
    p_notes.add_argument("--all", action="store_true", help="incluir también lo ya explicado (con todo)")
    p_notes.add_argument("--no-code", action="store_true", help="no incluir fragmentos de código (con todo)")
    p_notes.add_argument("--limit", type=int, default=60)

    args = ap.parse_args(argv)
    root = Path(args.path)
    if not root.is_dir():
        print(f"kipu: no existe la carpeta {root}", file=sys.stderr)
        return 2

    if args.cmd == "view":
        from kipu.viewer.server import serve
        return serve(root, args.out, args.port, open_browser=not args.no_open)

    data = scanner.run(root, args.out, reset_baseline=args.cmd == "baseline")
    if args.cmd == "notes":
        return _notes(args, root.resolve(), data)
    if args.cmd == "trace":
        path = scanner.trace(data, args.src, args.dst)
        if not path:
            print(f"Sin camino de '{args.src}' a '{args.dst}'.")
            return 1
        labels = {n["id"]: n["label"] for n in data["nodes"]}
        print(labels[path[0]["source"]])
        for e in path:
            best = [ev for ev in e["evidence"] if ev["confidence"] == e["confidence"]]
            details = " + ".join(dict.fromkeys(ev.get("detail", "") for ev in best))
            ev = best[0]
            where = f"{ev.get('file')}:{ev.get('line')}" if ev.get("file") else ""
            print(f"  └─{e['kind']} [{e['confidence']}] {details}  {where}")
            print(f"{labels[e['target']]}")
        return 0
    if getattr(args, "json", False):
        print(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        print(_summary(data))
        out = (args.out or scanner.default_out(root.resolve())).resolve()
        print(f"  → {out / scanner.GRAPH_FILE}")
    return 0
