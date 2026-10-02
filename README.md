# kg-milp-diet

Research prototype: compile a food knowledge graph (KG) into a mixed-integer
program (MILP) for personalized diet planning, with provenance for every
constraint.

```
JSON spec (from an LLM or by hand) -> KG queries -> constraint compiler (M1-M12)
    -> MILP (HiGHS / CBC) -> plan + explanations from provenance -> independent check
```

Scope of this prototype: **50 foods, 3 conditions (type 2 diabetes,
hypertension, CKD stage 3), 5 top-level allergens (peanut, tree nut, milk,
egg, fish) with subclasses**, plus 2 drugs, 3 diet patterns and 4 meal slots.

> **Data status.** Nutrient values in `data/foods.csv` come from USDA FoodData
> Central SR Legacy (2018-04): 645 of 650 values; the 5 not reported there are
> imputed and listed in `data/nutrient_provenance.csv`.
> Clinical rules in `data/condition_rules.csv` come from ADA 2026, ACC/AHA
> 2017/2025 and KDOQI 2020 with statement numbers and quotes, but are not yet
> verified by hand (`status` column). Not medical advice.

## Setup

Requires [Poetry](https://python-poetry.org/) and Python 3.14 and Docker (for Neo4j).

```powershell
cd D:\Projects\kg-milp-diet
poetry install              # add -E llm for the optional LLM layer
copy .env.example .env      # then set NEO4J_PASSWORD
docker compose up -d        # Neo4j on bolt://localhost:7694, Browser http://localhost:7481
poetry run kgdiet neo4j-load
poetry run pytest
poetry run kgdiet stats
```

`poetry install` creates an in-project `.venv` and installs the `dev`
dependency group (pytest) by default. Prefix any command with `poetry run`,
or `poetry shell` to activate the virtualenv for the session.

## Where the knowledge lives

- `data/*.csv` is the source of truth. Edit the knowledge there; it is versioned in git.
- `kgdiet neo4j-load` builds the graph from the CSVs and replaces the Neo4j graph with it.
  Run it again after every CSV change.
- The planner reads the KG from **Neo4j** by default (`src/kgdiet/neo4j_kg.py`, queries in
  `cypher/templates.cypher`). `--backend memory` (or `KGDIET_BACKEND=memory`) builds the
  same graph in memory from the CSVs instead, with no Docker needed. Both backends return
  identical rows, so plans are the same; `tests/test_neo4j_backend.py` checks this.
- The unit tests use the memory backend. The Neo4j parity tests reload Neo4j from the CSVs
  and are skipped when the container is down.

To explore the graph, open http://localhost:7481, drag `cypher/style.grass` onto the
Browser for colours and captions, and paste queries from `cypher/explore.cypher`
(schema, class and allergen trees, rule tables with quotes, exclusion paths, data checks).

## Try it

```powershell
kgdiet neo4j-load                                        # rebuild Neo4j from data\*.csv
kgdiet stats                                             # KG node and edge counts
kgdiet --backend memory solve profiles\p07_t2dm_htn_treenut.json   # same plan, no Neo4j
kgdiet solve profiles\p07_t2dm_htn_treenut.json          # plan + reasons + independent check
kgdiet solve profiles\p03_tree_nut_allergy.json --no-hierarchy   # watch the allergy slip through
kgdiet solve profiles\p10_htn_ckd3_conflict.json         # conflict detection and repair
kgdiet solve profiles\p07_t2dm_htn_treenut.json --json   # machine-readable, with provenance
kgdiet experiment                                        # ablation over the pilot profiles -> results\
python scripts\make_benchmark.py                          # regenerate benchmark v1 (62 profiles)
kgdiet experiment --profiles profiles\v1 --out results\v1 --time-limit 12 --jobs 2
kgdiet export-cypher                                     # Neo4j load script -> cypher\load.cypher
```

Optional LLM layer (needs `poetry install -E llm` and `ANTHROPIC_API_KEY`):

```powershell
kgdiet parse "I'm 55, female, 70 kg, diabetic with high blood pressure, allergic to tree nuts, about 1850 kcal" --id p12 --out profiles\p12.json
kgdiet solve profiles\p12.json --llm-explain
```

The parser only sees the KG vocabulary, and its JSON still goes through the
validator, which rejects any term it cannot map to a KG node.

## Repository map

| Path | What it holds |
| --- | --- |
| `data/*.csv` | KG source tables: foods, nutrients, classes, allergens, rules with provenance |
| `src/kgdiet/kg.py` | Shared KG interface, in-memory backend, `open_kg()` backend factory |
| `src/kgdiet/neo4j_kg.py` | Neo4j backend and the loader (`kgdiet neo4j-load`) |
| `cypher/templates.cypher` | Every query the Neo4j backend runs, by name |
| `cypher/explore.cypher`, `cypher/style.grass` | Browser queries and stylesheet for exploring the KG |
| `src/kgdiet/spec.py` | JSON spec schema and validator (term → KG node) |
| `src/kgdiet/compiler.py` | Mapping rules M1-M12: units, tightest-bound merge, conflicts, provenance |
| `src/kgdiet/model.py` | MILP goal program, elastic repair when infeasible |
| `src/kgdiet/verify.py` | Independent gold checker (reads the CSVs with its own code) |
| `src/kgdiet/explain.py` | Explanations built only from provenance records |
| `src/kgdiet/experiment.py` | Ablation runner and summary table |
| `src/kgdiet/llm.py` | Optional: query → spec, friendlier explanations |
| `profiles/*.json` | 11 pilot profiles |
| `profiles/v1/*.json` | Benchmark v1: 62 generated profiles, tiers T1-T4 |
| `scripts/make_benchmark.py` | Benchmark generator (labels T4 from the rule table, not the solver) |
| `tests/test_competency_questions.py` | 15 competency questions the KG must answer |
| `scripts/import_usda.py` | Load nutrient values from a USDA FDC CSV download (writes `nutrient_provenance.csv`) |

## Mapping rules implemented

| ID | KG pattern | Constraint |
| --- | --- | --- |
| M1 | User spec | Energy band, user nutrient bounds, max foods |
| M2 | `HAS_ALLERGY`, `SUBCLASS_OF*0..`, `HAS_ALLERGEN` | Exclude food |
| M3 | `FOLLOWS`, `FORBIDS`, `SUBCLASS_OF*0..`, `IS_A` | Exclude food class |
| M4 | `TAKES`, `AVOID` | Exclude (high severity) or penalize |
| M5 | `LIMITS {per_day \| per_kg}` | Upper bound (per kg × body weight) |
| — | `only_if` / `except_if` on rule edges | Rule applies only with / is suspended by another condition |
| — | `ADVISES` | Qualitative guidance: kept with provenance, never compiled |
| M6 | `LIMITS {pct_kcal}` | kcal/g × intake ≤ share × energy (linear) |
| M7 | `REQUIRES {per_day \| per_1000kcal}` | Lower bound (or rate × energy) |
| M8 | `TAKES`, `STABLE` | Range |
| M9 | `IN_GROUP`, `DRI.rda` | Soft goal (normalized shortfall in objective) |
| M10 | `IN_GROUP`, `DRI.ul` | Upper limit |
| M11 | `SUITABLE_FOR`, `MealSlot` shares | Per-meal variables and energy shares |
| M12 | `SUBSTITUTE_FOR`; elastic slack | Substitutes for excluded favorites; repair report |

## Results so far (run 3: benchmark v2, 92 profiles, USDA data)

Same foods, preferences, objective and solver for every system; only the knowledge changes.
Reproduce: `python scripts/make_benchmark.py` then
`kgdiet experiment --profiles profiles/v2 --out results/v2_usda --time-limit 10 --jobs 2`.

| System | Rule-breaking plans (86 feasible) | Multi-condition (T3) | Infeasible profiles flagged | p vs ours |
| --- | --- | --- | --- | --- |
| `llmn_like` (no clinical/diet/drug rules) | 59% | 91% | 95% | < 0.001 |
| `flat_table` (same rules, no hierarchy, no context) | 27% | 38% | 89% | < 0.001 |
| `kg_no_hierarchy` | 15% | 6% | 100% | < 0.001 |
| `kg_no_context` | 12% | 31% | 89% | 0.002 |
| `kg_full` (ours) | 0% | 0% | 100% | — |

Our 0% holds by construction (its hard constraints are the gold rules); the
result measures what the baselines miss. Earlier runs: `results/v1`, `results/v2`.

## Known limitations

- Violations only show up when the objective "wants" a forbidden food, so rates
  depend on the preference weight (fixed at 0.5) and on nutrient data.
- Some solves hit the time limit; those plans are flagged "not proven optimal".
- The gold checker shares the spec resolver with the system. For the paper,
  use hand-written, passage-cited gold constraints per profile.
- Integer servings of fixed size make portions coarse; plans favor few foods.

## Next steps

1. Verify every rule in `data/condition_rules.csv` against its guideline statement.
2. Grow the benchmark toward 300 profiles.
3. Later papers: CP-SAT / QP solver comparison; LLM front end (`src/kgdiet/llm.py`, parked).
4. Load the full USDA SR Legacy food table into Neo4j (the backend no longer limits KG size).
