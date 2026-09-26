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

> **Data status.** Nutrient values in `data/foods.csv` are approximate
> per-100 g values typed from general knowledge, not yet pulled from USDA.
> Clinical rule values are marked `placeholder` and DRI values `approximate`.
> Replace them before reporting results (see "Next steps"). Not medical advice.

## Setup (Windows, PowerShell)

```powershell
cd D:\Projects\kg-milp-diet
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # add ,llm for the optional LLM layer
pytest
```

macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`, then the same.

## Try it

```powershell
kgdiet stats                                             # KG node and edge counts
kgdiet solve profiles\p07_t2dm_htn_treenut.json          # plan + reasons + independent check
kgdiet solve profiles\p03_tree_nut_allergy.json --no-hierarchy   # watch the allergy slip through
kgdiet solve profiles\p10_htn_ckd3_conflict.json         # conflict detection and repair
kgdiet solve profiles\p07_t2dm_htn_treenut.json --json   # machine-readable, with provenance
kgdiet experiment                                        # ablation over all profiles -> results\
kgdiet export-cypher                                     # Neo4j load script -> cypher\load.cypher
```

Optional LLM layer (needs `pip install -e ".[llm]"` and `ANTHROPIC_API_KEY`):

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
| `src/kgdiet/kg.py` | In-memory property graph; each `q_*` method mirrors a Cypher template |
| `cypher/templates.cypher` | The same queries as parameterized Cypher (for Neo4j) |
| `src/kgdiet/spec.py` | JSON spec schema and validator (term → KG node) |
| `src/kgdiet/compiler.py` | Mapping rules M1-M12: units, tightest-bound merge, conflicts, provenance |
| `src/kgdiet/model.py` | MILP goal program, elastic repair when infeasible |
| `src/kgdiet/verify.py` | Independent gold checker (reads the CSVs with its own code) |
| `src/kgdiet/explain.py` | Explanations built only from provenance records |
| `src/kgdiet/experiment.py` | Ablation runner and summary table |
| `src/kgdiet/llm.py` | Optional: query → spec, friendlier explanations |
| `profiles/*.json` | 11 benchmark profiles, tiers T1-T4 (T4 = deliberately infeasible) |
| `scripts/import_usda.py` | Overwrite nutrient values from a USDA FDC CSV download |

## Mapping rules implemented

| ID | KG pattern | Constraint |
| --- | --- | --- |
| M1 | User spec | Energy band, user nutrient bounds, max foods |
| M2 | `HAS_ALLERGY`, `SUBCLASS_OF*0..`, `HAS_ALLERGEN` | Exclude food |
| M3 | `FOLLOWS`, `FORBIDS`, `SUBCLASS_OF*0..`, `IS_A` | Exclude food class |
| M4 | `TAKES`, `AVOID` | Exclude (high severity) or penalize |
| M5 | `LIMITS {per_day \| per_kg}` | Upper bound (per kg × body weight) |
| M6 | `LIMITS {pct_kcal}` | kcal/g × intake ≤ share × energy (linear) |
| M7 | `REQUIRES {per_day \| per_1000kcal}` | Lower bound (or rate × energy) |
| M8 | `TAKES`, `STABLE` | Range |
| M9 | `IN_GROUP`, `DRI.rda` | Soft goal (normalized shortfall in objective) |
| M10 | `IN_GROUP`, `DRI.ul` | Upper limit |
| M11 | `SUITABLE_FOR`, `MealSlot` shares | Per-meal variables and energy shares |
| M12 | `SUBSTITUTE_FOR`; elastic slack | Substitutes for excluded favorites; repair report |

## First results (approximate data; 11 profiles, 4 systems)

Same solver and foods for every system; only the knowledge changes.
Regenerate with `kgdiet experiment`; see `results/summary.md` and `results/runs.csv`.

| System | SVR | Infeasibility detection |
| --- | --- | --- |
| `table_tags` (llmn-like: direct tags + DRI) | 44% | 82% |
| `kg_no_hierarchy` | 11% | 100% |
| `kg_no_clinical` | 33% | 82% |
| `kg_full` (ours) | 0% | 100% |

SVR = share of the 9 feasible profiles whose plan breaks at least one gold
semantic rule. Treat these as a smoke test, not paper results.

## Known limitations

- Violations only show up when the objective "wants" a forbidden food. The
  vegan and fish-allergy profiles pass even without the rules because the
  optimizer never picks those foods. The benchmark needs profiles whose
  preferences or costs pull toward forbidden foods.
- `p08_jain_ckd3` hits the 20-30 s time limit; its plan is the best found, not
  proven optimal (flagged in the output and in `runs.csv`).
- The gold checker shares the spec resolver with the system. For the paper,
  use hand-written, passage-cited gold constraints per profile.
- Integer servings of fixed size make portions coarse; plans favor few foods.

## Next steps

1. Fill `fdc_id` in `data/foods.csv` and run `scripts/import_usda.py`.
2. Transcribe each rule value and its guideline section into `condition_rules.csv`.
3. Grow the profiles toward the 300-profile benchmark, adding adversarial preferences.
4. Add the LLM-only and LLM + MILP (no KG) baselines.
5. Optional: a Neo4j backend that runs `cypher/templates.cypher` behind the same interface.
