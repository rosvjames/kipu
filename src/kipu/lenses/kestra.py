"""Kestra lens: flows, tasks, their order, the SQL they run, the scripts they launch and flow triggers."""
from __future__ import annotations

import re

import yaml as _yaml

from kipu import ids, sqlextract, yamlx
from kipu.files import comment_above, read_text, rel, short_hash, walk
from kipu.graph import EXACT, INFERRED, UNRESOLVED, Evidence, Node
from kipu.lenses import Context

LENS = "kestra"
CONTAINERS = ("If", "ForEach", "WorkingDirectory", "Parallel", "Sequential", "Switch", "EachSequential",
              "EachParallel", "AllowFailure", "Dag", "LoopUntil", "ForEachItem")
_KAGGLE = re.compile(r"kaggle\s+(competitions|datasets)\s+download\s+(?:-[cd]\s+)?([\w/.-]+)")
_URL = re.compile(r"https?://([\w.-]+)")
_PYFILE = re.compile(r"([\w./-]+\.py)\b")
_DBT = re.compile(r"\bdbt\s+(build|run|seed|snapshot|test)\b")
_OUTPUTS = re.compile(r"outputs\.(\w+)")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


def _short(type_: str) -> str:
    return (type_ or "").rsplit(".", 1)[-1]


def _is_container(t: dict) -> bool:
    return _short(t.get("type", "")) in CONTAINERS


def scan(ctx: Context) -> None:
    for p in walk(ctx.root, {".yml", ".yaml"}):
        text = read_text(p)
        if "namespace:" not in text or "tasks:" not in text:
            continue
        try:
            doc = yamlx.load(text)
        except _yaml.YAMLError:
            continue
        if isinstance(doc, dict) and {"id", "namespace", "tasks"} <= doc.keys():
            _scan_flow(ctx, rel(ctx.root, p), text, doc)


def _flow_id(ns: str, fid: str) -> str:
    return f"flow:{ns}.{fid}"


def _scan_flow(ctx: Context, file: str, text: str, doc: dict) -> None:
    g = ctx.graph
    ns, fid = doc["namespace"], doc["id"]
    flow = _flow_id(ns, fid)
    meta = {"namespace": ns}
    if doc.get("description"):
        meta["description"] = str(doc["description"]).strip()
    schedules = [t.get("cron") for t in doc.get("triggers") or [] if _short(t.get("type", "")) == "Schedule"]
    if schedules:
        meta["schedule"] = schedules
    # Hash only flow-level settings: task edits are tracked on the task nodes themselves.
    flow_level = {k: v for k, v in yamlx.strip_lines(doc).items() if k not in ("tasks", "errors", "finally")}
    g.add_node(Node(flow, "flow", fid, file=file, line=1, lens=LENS, defined=True,
                    hash=short_hash(_yaml.safe_dump(flow_level, sort_keys=True)), meta=meta))

    lines = text.splitlines()
    task_ids: dict[str, str] = {}
    all_tasks: list[tuple[dict, str, str | None]] = []

    def collect(tasks, section, container):
        for t in tasks or []:
            if not isinstance(t, dict) or "id" not in t:
                continue
            all_tasks.append((t, section, container))
            for key in ("tasks", "then", "else"):
                if isinstance(t.get(key), list):
                    label = t["id"] if key == "tasks" else f"{t['id']} ({key})"
                    collect(t[key], section, label)
            if isinstance(t.get("cases"), dict):
                for case, lst in t["cases"].items():
                    if isinstance(lst, list):
                        collect(lst, section, f"{t['id']} (case {case})")

    for section in ("tasks", "errors", "finally"):
        collect(doc.get(section), section, None)

    for t, section, container in all_tasks:
        if _is_container(t):
            continue
        tid = f"task:{ns}.{fid}.{t['id']}"
        task_ids[t["id"]] = tid
        clean = yamlx.strip_lines(t)
        tmeta = {"type": _short(t.get("type", "")), "section": section}
        if container:
            tmeta["inside"] = container
        if t.get("description"):
            tmeta["description"] = str(t["description"]).strip()
        note = comment_above(lines, yamlx.line_of(t) or 0)
        if note:
            tmeta["comment"] = note
        g.add_node(Node(tid, "task", t["id"], parent=flow, file=file, line=yamlx.line_of(t), lens=LENS, defined=True,
                        hash=short_hash(_yaml.safe_dump(clean, sort_keys=True)), meta=tmeta))

    for t, section, container in all_tasks:
        if t["id"] in task_ids:
            _analyze_task(ctx, file, text, t, task_ids)

    # Execution order ("next"): main tasks, then finally; errors is its own chain.
    def seq(tasks):
        entries, exits = set(), set()
        first = True
        for t in tasks or []:
            if not isinstance(t, dict) or "id" not in t:
                continue
            e, x = block(t)
            if not e:
                continue
            if first:
                entries, first = e, False
            else:
                for a in exits:
                    for b in e:
                        g.add_edge(task_ids[a], task_ids[b], "next",
                                   Evidence(EXACT, LENS, file, yamlx.line_of(t), "orden de ejecución"))
            exits = x
        return entries, exits

    def block(t):
        if not _is_container(t):
            return {t["id"]}, {t["id"]}
        kind = _short(t["type"])
        if kind in ("Parallel", "EachParallel"):
            ents, exs = set(), set()
            for c in t.get("tasks") or []:
                e, x = seq([c])
                ents |= e
                exs |= x
            return ents, exs
        if kind == "If":
            e1, x1 = seq(t.get("then"))
            e2, x2 = seq(t.get("else"))
            return e1 | e2, x1 | x2
        if kind == "Switch":
            ents, exs = set(), set()
            for lst in (t.get("cases") or {}).values():
                e, x = seq(lst)
                ents |= e
                exs |= x
            return ents, exs
        return seq(t.get("tasks"))

    main_e, main_x = seq(doc.get("tasks"))
    for b in main_e:
        g.add_edge(flow, task_ids[b], "next", Evidence(EXACT, LENS, file, None, "inicio del flow"))
    fin_e, _ = seq(doc.get("finally"))
    for a in main_x:
        for b in fin_e:
            g.add_edge(task_ids[a], task_ids[b], "next", Evidence(EXACT, LENS, file, None, "finally"))
    seq(doc.get("errors"))

    # Triggers between flows.
    for trig in doc.get("triggers") or []:
        if _short(trig.get("type", "")) != "Flow":
            continue
        pre = (trig.get("preconditions") or {}).get("flows") or []
        conds = [c for c in trig.get("conditions") or [] if isinstance(c, dict) and c.get("flowId")]
        for c in list(pre) + conds:
            if not isinstance(c, dict) or not c.get("flowId"):
                continue
            src = _flow_id(c.get("namespace", ns), c["flowId"])
            states = c.get("states")
            detail = f"cuando {c['flowId']} termina" + (f" en {', '.join(map(str, states))}" if states else "")
            g.add_edge(src, flow, "triggers", Evidence(EXACT, LENS, file, yamlx.line_of(trig), detail))


def _commands_text(t: dict) -> str:
    cmds = t.get("commands")
    if isinstance(cmds, list):
        return "\n".join(str(c) for c in cmds)
    return str(cmds or "")


def _analyze_task(ctx: Context, file: str, text: str, t: dict, task_ids: dict[str, str]) -> None:
    g = ctx.graph
    me = task_ids[t["id"]]
    tline = yamlx.line_of(t)
    type_ = t.get("type", "")

    # SQL run by the task.
    sql = t.get("sql")
    if isinstance(sql, str):
        base = yamlx.line_of(t, "sql") or tline
        for a in sqlextract.extract(sql):
            line = base + a.line - 1
            creates = a.detail.startswith("CREATE")
            node_id = _data_node(ctx, a.name, a.is_stage, a.confidence, (file, line) if creates else None)
            ev = Evidence(a.confidence, LENS, file, line, a.detail)
            if a.op == "reads":
                g.add_edge(node_id, me, "reads", ev)
            else:
                g.add_edge(me, node_id, "writes", ev)

    # Upload to a stage.
    if _short(type_) == "Upload" and t.get("stageName"):
        sid = _data_node(ctx, str(t["stageName"]), True, EXACT)
        g.add_edge(me, sid, "writes", Evidence(EXACT, LENS, file, yamlx.line_of(t, "stageName"), "PUT al stage"))

    # Shell / script commands.
    cmd = _commands_text(t)
    cline = yamlx.line_of(t, "commands") or tline

    def at(snippet: str) -> int:
        i = text.find(snippet, sum(len(x) + 1 for x in text.splitlines()[: (tline or 1) - 1]))
        return text.count("\n", 0, i) + 1 if i >= 0 else cline
    if cmd:
        for m in _KAGGLE.finditer(cmd):
            ext = "external:kaggle"
            g.add_node(Node(ext, "external", "Kaggle API", lens=LENS, meta={"dataset": m.group(2)}))
            g.add_edge(ext, me, "reads", Evidence(INFERRED, LENS, file, at(m.group(0)), m.group(0)))
        for m in _URL.finditer(cmd):
            host = m.group(1)
            if host in _LOCAL_HOSTS:
                continue
            ext = f"external:{host}"
            g.add_node(Node(ext, "external", host, lens=LENS))
            g.add_edge(ext, me, "reads", Evidence(INFERRED, LENS, file, at(m.group(0)), m.group(0)))
        for m in _PYFILE.finditer(cmd):
            base = m.group(1).rsplit("/", 1)[-1]
            mod = ctx.python_modules.get(base)
            if mod:
                g.add_edge(me, mod, "runs", Evidence(INFERRED, LENS, file, at(m.group(1)),
                                                     f"comando menciona {m.group(1)} → {base} del proyecto"))
        if _DBT.search(cmd) or _short(type_) in ("DbtCLI", "Build", "Run"):
            for pid in ctx.dbt_projects[:1] if len(ctx.dbt_projects) == 1 else []:
                found = _DBT.search(cmd)
                g.add_edge(me, pid, "runs", Evidence(INFERRED, LENS, file, at(found.group(0)) if found else cline,
                                                     found.group(0) if found else _short(type_)))

    # Sub-flows.
    if _short(type_) == "Subflow" and t.get("flowId"):
        g.add_edge(me, _flow_id(t.get("namespace", ""), t["flowId"]), "triggers",
                   Evidence(EXACT, LENS, file, tline, "Subflow"))

    # Outputs of other tasks used by this one.
    dumped = _yaml.safe_dump(yamlx.strip_lines({k: v for k, v in t.items() if k not in ("tasks", "then", "else")}))
    for m in set(_OUTPUTS.findall(dumped)):
        if m in task_ids and m != t["id"]:
            g.add_edge(task_ids[m], me, "passes", Evidence(EXACT, LENS, file, tline, f"usa outputs.{m}"))


def _data_node(ctx: Context, name: str, is_stage: bool, confidence: str,
               defined_at: tuple[str, int] | None = None) -> str:
    """Node for a table/stage touched by SQL. defined_at=(file, line) when this SQL CREATEs it."""
    g = ctx.graph
    file, line = defined_at or (None, None)
    if confidence == UNRESOLVED:
        nid = f"unresolved:{name}"
        g.add_node(Node(nid, "unknown", name, lens=LENS, meta={"note": "nombre dinámico: no se puede resolver"}))
        return nid
    if is_stage:
        sid, path = ids.stage_id(name)
        node = g.add_node(Node(sid, "stage", "@" + ids.table_label(sid), parent=ids.group_id_for(sid), lens=LENS,
                               file=file, line=line, defined=bool(defined_at)))
        if path:
            paths = node.meta.setdefault("paths", [])
            if path not in paths:
                paths.append(path)
        return sid
    tid, tmeta = ids.table_id(name)
    g.add_node(Node(tid, "table", ids.table_label(tid), parent=ids.group_id_for(tid), lens=LENS, meta=tmeta,
                    file=file, line=line, defined=bool(defined_at)))
    return tid
