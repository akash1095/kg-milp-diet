"""Ablation experiment: the same solver and food pool, different knowledge.

Systems (only the knowledge used changes):
  table_tags       USDA-style table + literal tags: direct allergen tags, DRI, energy band.
                   No hierarchy, no diet / condition / drug rules (llmn-like baseline).
  kg_no_hierarchy  Full KG rules, but SUBCLASS_OF is not followed (depth 0).
  kg_no_clinical   KG with hierarchy, but no condition or drug rules.
  kg_full          Our method: all mapping rules M1-M12.
"""
from __future__ import annotations

import csv
import math
import json
from dataclasses import asdict
from pathlib import Path

from .compiler import CompileOptions, compile_spec, semantic_rule_set
from .kg import KG
from .model import plan
from .spec import UserSpec
from .verify import Gold, rule_f1

SYSTEMS: dict[str, CompileOptions] = {
    # llmn-like: USDA-style table + literal tags + DRI; no clinical, drug or diet rules
    "llmn_like": CompileOptions(hierarchy=False, rule_context=False, clinical_rules=False,
                                drug_rules=False, diet_rules=False),
    # same rules as ours, but as a flat table: no hierarchy, no only_if / except_if
    "flat_table": CompileOptions(hierarchy=False, rule_context=False),
    "kg_no_hierarchy": CompileOptions(hierarchy=False),
    "kg_no_context": CompileOptions(rule_context=False),
    "kg_full": CompileOptions(),
}


def load_profiles(folder: str | Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json"))]


_CACHE: dict = {}


def _one(task: tuple) -> dict:
    raw, name, data_dir, time_limit = task
    if "kg" not in _CACHE:
        _CACHE["kg"] = KG.load(data_dir) if data_dir else KG.load()
        _CACHE["gold"] = Gold(data_dir) if data_dir else Gold()
    kg, gold = _CACHE["kg"], _CACHE["gold"]
    spec = UserSpec.from_dict(raw).resolve(kg)
    cm = compile_spec(kg, spec, SYSTEMS[name])
    p = plan(kg, cm, time_limit=time_limit, threads=1)
    ev = gold.evaluate(spec, p.servings, p.intake)
    rf = rule_f1(semantic_rule_set(cm), gold.gold_rule_set(spec))
    detected = p.status in ("infeasible", "infeasible_relaxed")
    return {
        "profile": spec.id, "tier": spec.tier, "system": name,
        "expect_infeasible": spec.expect_infeasible,
        "status": p.status, "no_plan": p.status in ("no_plan", "error"), "flagged_infeasible": detected,
        "detection_correct": detected == spec.expect_infeasible,
        "semantic_violations": len(ev.semantic_violations),
        "violation_details": " | ".join(ev.semantic_violations),
        "rule_precision": rf["precision"], "rule_recall": rf["recall"], "rule_f1": rf["f1"],
        "false_rules": " | ".join(rf["false_rules"]), "missed_rules": " | ".join(rf["missed_rules"]),
        "ul_violations": len(ev.ul_violations),
        "energy_ok": ev.energy_ok, "shortfall": ev.shortfall,
        "n_foods": ev.n_foods, "proven_optimal": p.proven_optimal, "cost": round(p.cost, 2),
        "solve_ms": p.solve_ms, "n_exclusions": len(cm.exclusions),
        "n_rows": len(cm.bounds) + len(cm.ratios),
    }


def run(profiles_dir: str | Path, out_dir: str | Path, systems: list[str] | None = None,
        data_dir: str | Path | None = None, time_limit: int = 30, jobs: int = 1) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor
    tasks = [(raw, name, str(data_dir) if data_dir else None, time_limit)
             for raw in load_profiles(profiles_dir) for name in (systems or list(SYSTEMS))]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            for i, row in enumerate(ex.map(_one, tasks, chunksize=1), 1):
                rows.append(row)
                if i % 25 == 0:
                    print(f"  {i}/{len(tasks)} runs done", flush=True)
    else:
        rows = [_one(t) for t in tasks]
    with open(out / "runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "summary.md").write_text(summarize(rows), encoding="utf-8")
    return rows


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value from discordant counts b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def summarize(rows: list[dict], reference: str = "kg_full") -> str:
    systems = list(dict.fromkeys(r["system"] for r in rows))
    tiers = sorted({r["tier"] for r in rows})
    ref = {r["profile"]: r for r in rows if r["system"] == reference}
    head = ["System", "SVR (feasible)"] + [f"SVR {t}" for t in tiers if t != "T4"] + [
        "Rule F1", "Detection", "p vs ours (SVR)", "DRI shortfall", "Foods", "Solve ms", "No plan"]
    lines = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for s in systems:
        rs = [r for r in rows if r["system"] == s]
        no_plan = [r for r in rs if r["no_plan"]]
        feas = [r for r in rs if not r["expect_infeasible"] and not r["no_plan"]]
        planned = [r for r in feas if r["status"] == "optimal"]
        bad = lambda r: r["semantic_violations"] > 0  # noqa: E731
        cells = [s, _pct(sum(map(bad, feas)), len(feas))]
        for t in tiers:
            if t == "T4":
                continue
            tr = [r for r in feas if r["tier"] == t]
            cells.append(_pct(sum(map(bad, tr)), len(tr)))
        cells.append(f"{sum(r['rule_f1'] for r in rs) / len(rs):.3f}")
        cells.append(_pct(sum(r["detection_correct"] for r in rs), len(rs)))
        if s == reference:
            cells.append("\u2014")
        else:
            pairs = [r for r in feas if not ref[r["profile"]]["no_plan"]]
            b = sum(1 for r in pairs if bad(r) and not bad(ref[r["profile"]]))
            c = sum(1 for r in pairs if not bad(r) and bad(ref[r["profile"]]))
            cells.append(f"{mcnemar_exact(b, c):.2g}")
        cells.append(f"{sum(r['shortfall'] for r in planned) / len(planned):.1%}" if planned else "\u2014")
        cells.append(f"{sum(r['n_foods'] for r in planned) / len(planned):.1f}" if planned else "\u2014")
        cells.append(f"{sum(r['solve_ms'] for r in rs) / len(rs):.0f}")
        cells.append(str(len(no_plan)))
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "SVR = share of rule-feasible profiles whose plan breaks at least one gold semantic rule. "
              "Rule F1 = match between the semantic constraints a system applied and the gold set. "
              "Detection = 'flagged infeasible' matches the label. p = exact McNemar test on per-profile "
              "SVR against " + reference + ". No plan = solver found no plan within the time limit; "
              "those profiles are excluded from SVR and listed separately."]
    return "\n".join(lines) + "\n"


def _pct(k: int, n: int) -> str:
    return f"{k / n:.0%} ({k}/{n})" if n else "\u2014"
