---
name: kipu
description: Keep a live, faithful map of the project open while coding, and explain it in plain language. Use when the user asks to see, map, diagram or explain the architecture or data flow of a project ("explica este proyecto con kipu", "show me what you just built", "how does the data get from X to Y"), after you finish a batch of changes they need to understand, or when they say they lost track of what the code does.
---

# kipu

kipu draws a map of the project straight from the code: what exists, what reads and writes what, and what
changed. **The code decides the facts. You only write the words.** Never describe a component kipu did not
find, and never edit `graph.json`.

Run every command from the project root. If `kipu` is not installed, use `uvx kipu` instead.

## 1. Open the map

```bash
kipu view .
```

It serves a local page, watches the files and refreshes on every change, so start it once and leave it
running in the background. Tell the user the URL it prints.

## 2. Explain the project (the main job)

kipu can only say "task `load_week` writes table `SALES_RAW`". The user needs to know *what that means*.
You write that, one short line per node.

```bash
kipu notes status .        # how many explanations exist, how many went stale
kipu notes todo .          # the facts for every node that needs one
```

`todo` prints, for each node: what it is, where it lives, the comments already in the code, what it reads
and writes, and the code itself. Read it, then save your explanations in one batch:

```bash
kipu notes set . --file - <<'JSON'
{
  "summary": "One or two sentences: what the whole project does, end to end.",
  "nodes": {
    "<node id exactly as printed by todo>": {
      "what": "What it is or does, in one short sentence.",
      "why": "Optional: the one thing worth knowing about it."
    }
  }
}
JSON
```

### How to write them

- **Write in the language the user is speaking to you.**
- `what` is one sentence, at most 170 characters. Say what the thing does for the data or for the user, not
  how the code does it. "Copies this week's sales file into the raw table" beats "Executes a COPY INTO
  statement with MATCH_BY_COLUMN_NAME".
- Plain words first. If a term of art is unavoidable (stage, seed, fact table), say what it is the first
  time: "the stage, a waiting area for files inside Snowflake".
- `why` is optional, at most 420 characters: a constraint, a reason, a gotcha. Skip it when there is none.
- Use only what `todo` shows you: the code, its comments, its connections. If the code does not say why
  something exists, do not guess; leave `why` out.
- Groups (flows, schemas, folders) get an explanation too: it is shown when the tour enters them.
- kipu rejects ids that are not on the map and lines that are too long. Fix what it rejects and send only
  those again.

## 3. Keep them fresh

Each explanation is tied to the code it describes. When that code changes, kipu marks it stale and the
viewer warns the user. After you finish a batch of edits:

```bash
kipu notes todo .          # lists only what is missing or stale
```

Rewrite those, and the `summary` if the shape of the project changed.

## 4. Answer questions with it

- "How does X get to Y?" → `kipu trace . --from X --to Y` prints the path with `file:line` for every hop.
- "What did you just change?" → the viewer's **Cambios** button highlights what changed since the baseline
  and everything downstream. `kipu baseline .` resets that baseline; do it when the user starts a new task.

## Rules

- kipu is read-only on the project. Its output goes to `.kipu/`, which ignores itself in git.
- Edges marked `inferred` or `unresolved` are kipu being honest about what it could not prove. Do not
  "fix" them in your explanations by stating the connection as certain.
