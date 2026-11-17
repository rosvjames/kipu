"""End-to-end checks on a synthetic project (Kestra + dbt + Python), never on user code."""
import json
import shutil
from pathlib import Path

import pytest

from kipu import scan as scanner
from kipu.sqlextract import extract

FIXTURE = Path(__file__).parent / "fixtures" / "shop"


@pytest.fixture()
def project(tmp_path):
    dst = tmp_path / "shop"
    shutil.copytree(FIXTURE, dst)
    return dst


@pytest.fixture()
def graph(project, tmp_path):
    return scanner.run(project, tmp_path / "out")


def nodes(g):
    return {n["id"]: n for n in g["nodes"]}


def edge(g, src, dst, kind):
    return next((e for e in g["edges"] if e["source"] == src and e["target"] == dst and e["kind"] == kind), None)


def test_end_to_end_path_crosses_every_tool(graph):
    path = scanner.trace(graph, "data.example.com", "daily_revenue.parquet")
    assert path, "no path from the external source to the exported file"
    hops = [p["target"] for p in path]
    assert "stage:RAW.LANDING" in hops
    assert "table:RAW.ORDERS" in hops
    assert "table:STAGING.STG_ORDERS" in hops
    assert "table:STAGING_MARTS.FCT_DAILY_SALES" in hops
    assert "module:jobs/export.py" in hops


def test_same_table_from_kestra_and_dbt_is_one_node(graph):
    n = nodes(graph)
    orders = n["table:RAW.ORDERS"]
    assert orders["meta"]["dbt_source"] == "raw.orders"
    assert orders["file"] == "flows/shop.ingest.yml"  # defined by the CREATE TABLE in Kestra
    assert [i for i in n if i.startswith("table:") and i.split(".")[-1] == "ORDERS"] == ["table:RAW.ORDERS"]


def test_dbt_default_schema_rule_without_custom_macro(graph):
    fct = nodes(graph)["table:STAGING_MARTS.FCT_DAILY_SALES"]
    assert fct["meta"]["identity_confidence"] == "inferred"
    assert "regla por defecto" in fct["meta"]["identity"]
    e = edge(graph, "table:STAGING.STG_ORDERS", "table:STAGING_MARTS.FCT_DAILY_SALES", "feeds")
    assert e and e["confidence"] == "exact" and e["evidence"][0]["line"] == 1


def test_wrapper_with_fstring_is_resolved_at_call_site(graph):
    e = edge(graph, "table:STAGING_MARTS.FCT_DAILY_SALES", "module:jobs/export.py", "reads")
    assert e and e["confidence"] == "inferred"
    assert "vía read_table" in e["evidence"][0]["detail"]
    assert e["evidence"][0]["line"] == 10


def test_dynamic_access_stays_unresolved(graph):
    unresolved = [e for e in graph["edges"] if e["confidence"] == "unresolved"]
    assert any(e["target"] == "module:jobs/export.py" for e in unresolved)


def test_dynamic_path_inside_literal_stage_keeps_the_stage(graph):
    e = edge(graph, "stage:RAW.LANDING", "task:shop.ingest.load_orders", "reads")
    assert e and e["confidence"] == "exact"


def test_cte_is_not_a_table(graph):
    assert "table:C" not in nodes(graph)


def test_kestra_order_trigger_and_commands(graph):
    assert edge(graph, "task:shop.ingest.download", "task:shop.ingest.upload", "next")
    assert edge(graph, "task:shop.ingest.download", "task:shop.ingest.upload", "passes")
    assert edge(graph, "flow:shop.ingest", "flow:shop.transform", "triggers")
    assert edge(graph, "task:shop.transform.dbt_build", "dbt:shop", "runs")
    run = edge(graph, "task:shop.transform.export", "module:jobs/export.py", "runs")
    assert run and run["evidence"][0]["line"] == 11
    assert nodes(graph)["flow:shop.ingest"]["meta"]["schedule"] == ["0 5 * * *"]


def test_every_edge_has_evidence_with_a_location(graph):
    for e in graph["edges"]:
        assert e["evidence"], e["id"]
        assert any(ev.get("file") for ev in e["evidence"]), e["id"]


def test_secrets_are_never_read(graph, tmp_path):
    blob = json.dumps(graph)
    assert "super-secret-value" not in blob
    assert "SHOULD_NEVER_BE_READ" not in blob
    assert ".env" not in {n.get("file") for n in graph["nodes"]}


def test_output_goes_only_to_out_dir(project, tmp_path):
    before = sorted(p.relative_to(project) for p in project.rglob("*"))
    scanner.run(project, tmp_path / "out2")
    assert sorted(p.relative_to(project) for p in project.rglob("*")) == before


def test_changes_against_baseline(project, tmp_path):
    out = tmp_path / "out3"
    scanner.run(project, out, reset_baseline=True)
    stg = project / "warehouse/models/staging/stg_orders.sql"
    stg.write_text(stg.read_text() + "where amount > 0\n")
    (project / "warehouse/models/marts/fct_weekly.sql").write_text("select * from {{ ref('fct_daily_sales') }}\n")
    g = scanner.run(project, out)
    n = nodes(g)
    assert n["table:STAGING.STG_ORDERS"]["change"] == "modified"
    assert n["table:STAGING_MARTS.FCT_WEEKLY"]["change"] == "added"
    assert "change" not in n["table:RAW.ORDERS"]
    assert g["changes"]["modified"] == 1 and g["changes"]["added"] == 1


def test_sqlextract_reads_writes_and_templates():
    acc = extract("{% set k = 1 %}\nINSERT INTO A.T SELECT * FROM B.S JOIN {{ ref('x') }} r ON 1=1;\n")
    ops = {(a.op, a.name) for a in acc}
    assert ("writes", "A.T") in ops and ("reads", "B.S") in ops
    assert all(a.line == 2 for a in acc)


def test_secret_files_are_skipped_even_with_watched_extensions(project):
    from kipu.files import walk

    names = {p.name for p in walk(project)}
    assert "kaggle.json" not in names
    assert "dbt_project.yml" in names


def test_secret_values_never_reach_the_graph(graph):
    assert "fake-kaggle-key" not in json.dumps(graph)


def test_editing_one_task_marks_only_that_task(project, tmp_path):
    out = tmp_path / "out4"
    scanner.run(project, out, reset_baseline=True)
    flow = project / "flows/shop.ingest.yml"
    text = flow.read_text().replace("-o orders.csv", "-o orders.csv --retry 3")
    flow.write_text("# a comment changes nothing\n" + text)
    n = nodes(scanner.run(project, out))
    assert n["task:shop.ingest.download"]["change"] == "modified"
    assert "change" not in n["flow:shop.ingest"]
    assert "change" not in n["task:shop.ingest.load_orders"]


def test_model_artifact_joins_train_and_predict(graph):
    path = scanner.trace(graph, "train.py", "predict.py")
    assert path and [p["target"] for p in path] == ["file:models/demand.pkl", "module:jobs/predict.py"]


def test_ui_table_call_is_not_data_access(graph):
    assert not [n for n in graph["nodes"] if n["kind"] == "unknown" and n.get("file") == "jobs/train.py"]


def test_comment_above_a_task_is_kept_as_its_description(project, tmp_path):
    flow = project / "flows/shop.ingest.yml"
    flow.write_text(flow.read_text().replace("  - id: upload\n", "  # ---------- LANDING ----------\n  # Puts the file in the landing stage.\n  - id: upload\n"))
    n = nodes(scanner.run(project, tmp_path / "out5"))
    assert n["task:shop.ingest.upload"]["meta"]["comment"] == "LANDING Puts the file in the landing stage."


def test_notes_are_tied_to_the_code_and_go_stale(project, tmp_path):
    from kipu import notes

    out = tmp_path / "out6"
    g = scanner.run(project, out)
    report = notes.save(g, out, {
        "summary": "Loads orders and reports daily revenue.",
        "nodes": {
            "table:STAGING.STG_ORDERS": {"what": "Orders, cleaned.", "why": "One row per order."},
            "table:MADE.UP": {"what": "An invented table."},
            "module:jobs/export.py": {"what": "x" * 400},
        },
    })
    assert set(report["saved"]) == {"table:STAGING.STG_ORDERS", "summary"}
    assert len(report["errors"]) == 2                       # unknown id and over-long line are refused

    g = scanner.run(project, out)
    note = nodes(g)["table:STAGING.STG_ORDERS"]["note"]
    assert note == {"what": "Orders, cleaned.", "why": "One row per order.", "stale": False}
    assert g["summary"] == {"text": "Loads orders and reports daily revenue.", "stale": False}
    assert g["notes"]["fresh"] == 1 and "table:MADE.UP" not in nodes(g)
    assert "table:STAGING.STG_ORDERS" not in notes.todo(g, project)

    stg = project / "warehouse/models/staging/stg_orders.sql"
    stg.write_text(stg.read_text() + "where amount > 0\n")
    g = scanner.run(project, out)
    assert nodes(g)["table:STAGING.STG_ORDERS"]["note"]["stale"] is True
    assert "explicación anterior (el código cambió): Orders, cleaned." in notes.todo(g, project)


def test_notes_todo_never_prints_secret_files(graph, project):
    from kipu import notes

    brief = notes.todo(graph, project, include_all=True)
    assert "fake-kaggle-key" not in brief and "super-secret-value" not in brief
    assert "## module:jobs/export.py" in brief and "df.to_parquet(OUT)" in brief
