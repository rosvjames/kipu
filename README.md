<p align="center">
  <img src=".github/logo.png" alt="kipu" width="360">
</p>

<p align="center"><b>See what your AI built.</b></p>

Coding agents write code faster than anyone can read it. After a few sessions the project works, but nobody
on the team can explain how the data gets from A to B. kipu fixes that: it reads your code, draws a live map
of the architecture and the data flow, and walks you through it step by step, in plain language.

> **Status:** early prototype. Works today on Python, dbt and Kestra projects (Snowflake-flavoured SQL).
> The interface is in Spanish for now.

## Why kipu is different

- **Nothing invented.** The map comes from parsing the code, not from an LLM guessing. Every arrow carries its
  evidence (`file:line`) and how sure kipu is: **exact**, **inferred** or **unresolved**. Unresolved
  connections are shown, never hidden.
- **End to end, across tools.** One map follows the data through the orchestrator, the SQL it runs, the dbt
  models downstream and the Python or Spark job that reads them. The same table written as
  `M5.BRONZE.SALES_RAW` in a Kestra flow and as `source('bronze', 'sales_raw')` in dbt is one node.
- **Live.** The map refreshes while you or your agent edit files, and highlights what changed since you
  started and everything downstream of it.
- **Explained, without trusting the AI with facts.** Your coding agent writes one short plain-language line
  per component. The code decides what exists; the agent only writes the words. Explanations are marked as
  AI-written and flagged the moment the code under them changes.

## Quick start

kipu is not on PyPI yet. Run it from a clone:

```bash
git clone https://github.com/rosvjames/kipu.git
cd kipu
uv run kipu view path/to/your/project
```

This opens a local page (http://127.0.0.1:7333) and keeps watching the project. kipu never writes inside your
project except its own output folder, `.kipu/`, which ignores itself in git. Use `--out DIR` to put it
somewhere else.

## Using the map

- **Level 0** shows a handful of blocks, left to right: sources, orchestration flows, data layers, code.
  One arrow between two blocks stands for all the connections between them; the number on it says how many.
- **Click a block** to open it in place: a flow shows its steps in order, a schema its tables.
- **Hover or select** anything to light up only its connections. The side panel lists each connection with
  the line of code it comes from.
- **▶ Recorrido** (tour) starts with a one-paragraph summary of the project, then follows the data from its
  source, one stop at a time. At each fork it asks which branch to follow.
- **Cambios** highlights what changed since the baseline and everything that depends on it.
- **Orquestación** overlays who runs or triggers what.
- **Código** switches to the import map, for projects with no data flow.

## Plain-language explanations

kipu ships an [agent skill](skills/kipu/SKILL.md) that teaches Claude Code, or any agent that can run shell
commands, to write the explanations. Install it in a project:

```bash
mkdir -p .claude/skills && cp -r path/to/kipu/skills/kipu .claude/skills/
```

Then ask your agent: *"explain this project with kipu"*. Behind the scenes it runs:

```bash
kipu notes todo .     # the facts kipu found for every node that needs an explanation
kipu notes set . --file notes.json
kipu notes status .   # how many are fresh, stale or missing
```

kipu refuses explanations for nodes that are not on the map, and lines that are too long to read at a glance.
Comments already in your code (above a Kestra task, at the top of a dbt model, in a module docstring) are
shown without any AI at all.

## Command line

| Command | What it does |
|---|---|
| `kipu view [path]` | Opens the live map in your browser |
| `kipu scan [path]` | Writes `graph.json` and prints a summary |
| `kipu trace [path] --from X --to Y` | Prints the path the data takes from X to Y, with `file:line` per hop |
| `kipu baseline [path]` | Resets "what changed": changes are measured from now on |
| `kipu notes todo\|set\|status [path]` | Manages the plain-language explanations |

```text
$ kipu trace . --from kaggle --to OBT_SALES
Kaggle API
  └─reads [inferred] kaggle competitions download -c m5-forecasting-accuracy  kestra/ingest_raw.yml:110
download_m5
  └─passes [exact] usa outputs.download_m5  kestra/ingest_raw.yml:133
upload_file
  └─writes [exact] PUT al stage  kestra/ingest_raw.yml:136
...
build_obt.py
  └─writes [inferred] option('dbtable') · schema OBT por **sf_options('OBT')  spark/build_obt.py:102
OBT_SALES
```

## What kipu understands today

| Lens | What it reads |
|---|---|
| **Python** | Modules, functions and imports (exact). Data access by pattern (inferred): Spark readers and writers, pandas I/O, SQL passed to `execute()` / `sql()`, `joblib` model files. Resolves string constants, f-strings and one level of wrapper functions. |
| **dbt** | Models, sources and seeds wired by `ref()` and `source()`. Table identity from a fresh `manifest.json`, or inferred from `dbt_project.yml`, the profile's schema and your `generate_schema_name` macro. |
| **Kestra** | Flows, tasks and their order, the SQL each task runs, stages, `outputs` passed between tasks, flow triggers, schedules, and the scripts or dbt projects a task launches. |

SQL is parsed with [sqlglot](https://github.com/tobymao/sqlglot) after stripping Jinja templates.
kipu never opens `.env` files, keys or `kaggle.json`, and reads only `schema` and `database` from
`profiles.yml`.

## Limitations

- Static analysis: a table name built at runtime stays **unresolved**. Paths built with `os.path.join` or
  `Path / "x"` are not followed yet.
- A stage is one node, so two files inside the same stage are not told apart.
- The viewer is in Spanish; English is next.
- Large repositories have not been tested yet.

## Development

```bash
uv run pytest
```

Tests run on a synthetic project in `tests/fixtures/shop` (Kestra + dbt + Python).

## Name

The *khipu* (kipu) is the Andean record-keeping system: data stored as knots on strings. A graph, made of cord.

## License

[MIT](LICENSE)
