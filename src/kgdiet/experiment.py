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
import json
from dataclasses import asdict
from pathlib import Path

from .compiler import CompileOptions, compile_spec
from .kg import KG
from .model import plan
from .spec import UserSpec
from .verify import Gold

SYSTEMS: dict[str, CompileOptions] = {
    "table_tags": CompileOptions(hierarchy=False, clinical_rules=False, drug_rules=False, diet_rules=False),
    "kg_no_hierarchy": CompileOptions(hierarchy=False),
    "kg_no_clinical": CompileOptions(clinical_rules=False, drug_rules=False),
    "kg_full": CompileOptions(),
}


def load_profiles(folder: str | Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json"))]


def run(profiles_dir: str | Path, out_dir: str | Path, systems: list[str] | None = None,
        data_dir: str | Path | None = None, time_limit: int = 30) -> list[dict]:
    kg = KG.load(data_dir) if data_dir else KG.load()
    gold = Gold(data_dir) if data_dir else Gold()
    rows = []
    for raw in load_profiles(profiles_dir):
        for name in systems or list(SYSTEMS):
            spec = UserSpec.from_dict(raw).resolve(kg)
            cm = compile_spec(kg, spec, SYSTEMS[name])
            p = plan(kg, cm, time_limit=time_limit)
            ev = gold.evaluate(spec, p.servings, p.intake)
            detected = p.status != "optimal"
            rows.append({
                "profile": spec.id, "tier": spec.tier, "system": name,
                "expect_infeasible": spec.expect_infeasible,
                "status": p.status, "flagged_infeasible": detected,
                "detection_correct": detected == spec.expect_infeasible,
                "semantic_violations": len(ev.semantic_violations),
                "violation_details": " | ".join(ev.semantic_violations),
                "ul_violations": len(ev.ul_violations),
                "energy_ok": ev.energy_ok, "shortfall": ev.shortfall,
                "n_foods": ev.n_foods, "proven_optimal": p.proven_optimal, "cost": round(p.cost, 2), "solve_ms": p.solve_ms,
                "n_exclusions": len(cm.exclusions), "n_rows": len(cm.bounds) + len(cm.ratios),
            })
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "runs.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    summary = summarize(rows)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    return rows


def summarize(rows: list[dict]) -> str:
    systems = list(dict.fromkeys(r["system"] for r in rows))
    lines = ["| System | SVR (feasible profiles) | Mean semantic violations | Infeasibility detection | "
             "Mean DRI shortfall | Mean foods | Mean solve ms |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for s in systems:
        rs = [r for r in rows if r["system"] == s]
        feas = [r for r in rs if not r["expect_infeasible"]]
        planned = [r for r in feas if r["status"] == "optimal"]
        svr = sum(r["semantic_violations"] > 0 for r in feas) / len(feas) if feas else 0
        mv = sum(r["semantic_violations"] for r in feas) / len(feas) if feas else 0
        det = sum(r["detection_correct"] for r in rs) / len(rs)
        sf = sum(r["shortfall"] for r in planned) / len(planned) if planned else 0
        nf = sum(r["n_foods"] for r in planned) / len(planned) if planned else 0
        ms = sum(r["solve_ms"] for r in rs) / len(rs)
        lines.append(f"| {s} | {svr:.0%} | {mv:.2f} | {det:.0%} | {sf:.1%} | {nf:.1f} | {ms:.0f} |")
    lines.append("")
    lines.append("SVR = share of feasible profiles whose plan breaks at least one gold semantic rule "
                 "(allergy, diet, drug, condition). Detection = share of profiles where "
                 "'flagged infeasible' matches the benchmark label.")
    return "\n".join(lines) + "\n"
