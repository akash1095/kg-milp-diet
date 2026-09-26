"""Explanations built only from provenance records (no free text beyond them).

``explain_text`` is deterministic and template-based. ``llm.explain_with_llm``
(optional) rewrites these same records in friendlier prose.
"""
from __future__ import annotations

from collections import defaultdict

from .compiler import CompiledModel
from .kg import KG
from .model import Plan


def _k(nid: str) -> str:
    return nid.split(":", 1)[1] if ":" in nid else nid


def _src(p) -> str:
    tag = f", {p.status}" if p.status in ("placeholder", "approximate") else ""
    return f"{p.source}{tag}" if p.source else "KG"


def explain_records(kg: KG, cm: CompiledModel, plan_: Plan) -> list[dict]:
    """Structured statements, each tied to rule ids and KG paths."""
    out: list[dict] = []
    grouped: dict[tuple, list[str]] = defaultdict(list)
    for food, provs in cm.exclusions.items():
        p = provs[0]
        grouped[(p.rule, p.path[2], p.source)].append(kg.name(food))
    for (rule, owner, source), names in sorted(grouped.items()):
        verb = {"M2": "allergy to", "M3": "diet", "M4": "medication"}[rule]
        out.append({"rule": rule, "text": f"Excluded {len(names)} food(s) because of your {verb} "
                                          f"{kg.name(owner)}: {', '.join(sorted(names))}.",
                    "source": source})
    for n, m in sorted(cm.merged.items()):
        node = kg.nodes[f"Nutrient:{n}"]
        for side, sym in (("lo", "≥"), ("hi", "≤")):
            if not m.get(side):
                continue
            b = m[side]["binding"][0]
            if b.rule == "M1" and n == "energy_kcal":
                continue
            owner = b.prov.path[2] if len(b.prov.path) > 2 else "your request"
            reason = kg.name(owner) if owner in kg.nodes else owner
            val = m[side]["value"]
            got = plan_.intake.get(n)
            out.append({"rule": b.rule,
                        "text": f"{node['name']} {sym} {val:g} {node['unit']}/day because of {reason} "
                                f"[{_src(b.prov)}]" + (f"; plan has {got:g}." if got is not None else "."),
                        "source": b.prov.source})
    for r in cm.ratios:
        out.append({"rule": r.rule, "text": f"{r.label} [{_src(r.prov)}].", "source": r.prov.source})
    for s in cm.substitutes:
        out.append({"rule": "M12", "text": f"You prefer {kg.name(s['for'])}, which is excluded; "
                                           f"suggested substitute: {kg.name(s['substitute'])} "
                                           f"(similarity {s['similarity']:g}).", "source": "KG"})
    for c in plan_.conflicts:
        lo = "; ".join(kg.name(p["kg_path"][2]) for p in c["lower_from"] if len(p["kg_path"]) > 2) or "your request"
        hi = "; ".join(kg.name(p["kg_path"][2]) for p in c["upper_from"] if len(p["kg_path"]) > 2) or "your request"
        out.append({"rule": "CONFLICT",
                    "text": f"Conflict on {kg.nodes['Nutrient:' + c['nutrient']]['name']}: {lo} requires "
                            f"≥ {c['lower']:g} but {hi} allows ≤ {c['upper']:g}. Ask a clinician which rule "
                            f"takes priority.", "source": ""})
    for r in plan_.relaxations:
        out.append({"rule": "M12", "text": f"Relaxed row {r['row']} by {r['amount']:g} to find a plan.",
                    "source": ""})
    return out


def format_plan(kg: KG, cm: CompiledModel, plan_: Plan) -> str:
    lines = [f"Profile {cm.spec.id}: status = {plan_.status}"
             + (f" ({plan_.message})" if plan_.message else "")]
    if plan_.servings:
        order = ["MealSlot:breakfast", "MealSlot:lunch", "MealSlot:dinner", "MealSlot:snack", "day"]
        meals: dict[str, list[str]] = defaultdict(list)
        for f, per in plan_.meal_servings.items():
            for m, s in per.items():
                g = kg.nodes[f]["serving_g"] * s
                meals[m].append(f"{kg.name(f)} {g:g} g ({s} serving{'s' if s > 1 else ''})")
        lines.append("")
        for m in sorted(meals, key=lambda k: order.index(k) if k in order else 99):
            label = kg.name(m) if m in kg.nodes else "Day"
            lines.append(f"{label}: " + "; ".join(sorted(meals[m])))
        i = plan_.intake
        lines.append("")
        lines.append(f"Totals: {i.get('energy_kcal', 0):.0f} kcal, protein {i.get('protein_g', 0):.0f} g, "
                     f"carbs {i.get('carbs_g', 0):.0f} g, fat {i.get('fat_g', 0):.0f} g, "
                     f"fiber {i.get('fiber_g', 0):.0f} g, sodium {i.get('sodium_mg', 0):.0f} mg, "
                     f"potassium {i.get('potassium_mg', 0):.0f} mg; cost ≈ ${plan_.cost:.2f}; "
                     f"solve {plan_.solve_ms:.0f} ms ({plan_.solver}"
                     + ("" if plan_.proven_optimal else ", time limit hit: best plan found, not proven optimal")
                     + ")")
    lines.append("")
    lines.append("Why:")
    lines += [f"- [{r['rule']}] {r['text']}" for r in explain_records(kg, cm, plan_)]
    return "\n".join(lines)
