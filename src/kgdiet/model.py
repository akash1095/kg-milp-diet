"""MILP builder and solver (PuLP + CBC; HiGHS works too via ``solver=``).

Mixed-integer goal program:
  min  sum_n w_n * s_n / T_n  (DRI shortfall)  + penalties (soft avoid, #foods, cost)
  s.t. merged hard bounds, energy-ratio rows, meal shares, exclusions,
       q_f <= U_f * y_f, sum_f y_f <= max_foods, integer servings.
If the strict model is infeasible (or the compiler found a conflict), an
elastic copy adds penalized slack to every hard nutrient row and reports
which rows had to move, with their provenance (repair phase, M12).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pulp

from .compiler import CompiledModel, Prov
from .kg import KG
from .verify import TOL

ELASTIC_PENALTY = 1000.0
# Benchmark protocol (fixed before run 2, identical for every system): a stated
# preference ("I love X") is worth half of one fully missed DRI goal.
PREFERENCE_WEIGHT = 0.5
PER_MEAL_MAX_SERVINGS = 2


@dataclass
class Plan:
    status: str                      # optimal | infeasible_relaxed | infeasible | error
    servings: dict[str, int] = field(default_factory=dict)
    meal_servings: dict[str, dict[str, int]] = field(default_factory=dict)
    intake: dict[str, float] = field(default_factory=dict)
    cost: float = 0.0
    objective: float | None = None
    solve_ms: float = 0.0
    relaxations: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    message: str = ""
    proven_optimal: bool = True      # False when the time limit stopped the search early
    solver: str = ""

    @property
    def feasible(self) -> bool:
        return self.status == "optimal"


def plan(kg: KG, cm: CompiledModel, time_limit: int = 30, solver: str = "auto",
         threads: int | None = None) -> Plan:
    """Strict solve; on a compile-time conflict or solver infeasibility, run the elastic repair."""
    conflicts = [{
        "nutrient": c.nutrient, "lower": c.lo, "upper": c.hi,
        "lower_from": [p.as_dict() for p in c.lo_provs],
        "upper_from": [p.as_dict() for p in c.hi_provs]} for c in cm.conflicts]
    if not conflicts:
        strict = solve(kg, cm, elastic=False, time_limit=time_limit, solver=solver, threads=threads)
        if strict.status in ("optimal", "error", "no_plan"):
            return strict
    relaxed = solve(kg, cm, elastic=True, time_limit=time_limit, solver=solver, threads=threads)
    relaxed.conflicts = conflicts
    if relaxed.status == "optimal":
        relaxed.status = "infeasible_relaxed"
        relaxed.message = ("Hard rules conflict; the plan below relaxes the rows listed in "
                           "'relaxations'. Review them before use.")
    else:
        relaxed.status = "infeasible"
        relaxed.message = "No plan even after relaxing nutrient rows (check exclusions and meal slots)."
    return relaxed


def solve(kg: KG, cm: CompiledModel, elastic: bool = False, time_limit: int = 30,
          solver: str = "auto", threads: int | None = None) -> Plan:
    t0 = time.perf_counter()
    prob = pulp.LpProblem("diet", pulp.LpMinimize)
    foods = cm.allowed_foods
    fn = {f: kg.nodes[f] for f in foods}
    coef = {f: {n: amt / 100.0 * fn[f]["serving_g"] for n, amt in kg.food_nutrients(f).items()}
            for f in foods}  # nutrient per serving

    # --- decision variables ------------------------------------------------
    x: dict[tuple[str, str], pulp.LpVariable] = {}
    if cm.meals:
        for m, info in cm.meals.items():
            for f in foods:
                if f in info["foods"]:
                    ub = min(PER_MEAL_MAX_SERVINGS, fn[f]["max_servings"])
                    x[f, m] = pulp.LpVariable(f"x_{_k(f)}_{_k(m)}", 0, ub, cat="Integer")
    else:
        for f in foods:
            x[f, "day"] = pulp.LpVariable(f"x_{_k(f)}", 0, fn[f]["max_servings"], cat="Integer")
    q = {f: pulp.lpSum(v for (ff, _), v in x.items() if ff == f) for f in foods}
    y = {f: pulp.LpVariable(f"y_{_k(f)}", cat="Binary") for f in foods}
    for f in foods:
        prob += q[f] <= fn[f]["max_servings"] * y[f], f"LINK_{_k(f)}"
    prob += pulp.lpSum(y.values()) <= cm.max_foods, "M1_max_foods"

    def intake(n: str):
        return pulp.lpSum(coef[f].get(n, 0.0) * q[f] for f in foods)

    energy = intake("energy_kcal")
    slacks: list[tuple[str, pulp.LpVariable, float, list[Prov]]] = []

    def add_row(name: str, expr, sense: str, rhs: float, provs: list[Prov]) -> None:
        if elastic and not name.startswith("B_energy_kcal"):  # the energy band stays hard
            e = pulp.LpVariable(f"e_{name}", 0)
            slacks.append((name, e, max(1.0, abs(rhs)), provs))
            prob.addConstraint((expr + e >= rhs) if sense == "min" else (expr - e <= rhs), name)
        else:
            # same relative tolerance as the checker (TOL), so point ranges such as
            # 0.8 <= protein/kg <= 0.8 become narrow but full-dimensional bands
            tol_rhs = rhs * (1 - TOL) if sense == "min" else rhs * (1 + TOL)
            prob.addConstraint((expr >= tol_rhs) if sense == "min" else (expr <= tol_rhs), name)

    # --- hard nutrient bounds (merged) ---------------------------------------
    for n, m in cm.merged.items():
        if m.get("lo"):
            add_row(f"B_{n}_lo", intake(n), "min", m["lo"]["value"],
                    [b.prov for b in m["lo"]["binding"]])
        if m.get("hi"):
            add_row(f"B_{n}_hi", intake(n), "max", m["hi"]["value"],
                    [b.prov for b in m["hi"]["binding"]])
    # --- ratio rows relative to energy ---------------------------------------
    for r in cm.ratios:
        add_row(r.name, r.coef_intake * intake(r.nutrient) - r.coef_energy * energy, r.sense, 0.0, [r.prov])
    # --- meal energy shares ---------------------------------------------------
    if cm.meals:
        for m, info in cm.meals.items():
            e_m = pulp.lpSum(coef[f].get("energy_kcal", 0.0) * v for (f, mm), v in x.items() if mm == m)
            prob += e_m >= info["share_min"] * energy, f"M11_{_k(m)}_min"
            prob += e_m <= info["share_max"] * energy, f"M11_{_k(m)}_max"

    # --- objective ------------------------------------------------------------
    terms = []
    for g in cm.goals:
        s = pulp.LpVariable(f"s_{g.nutrient}", 0)
        prob += intake(g.nutrient) + s >= g.target, f"G_{g.nutrient}"
        terms.append(g.weight * s / g.target)
    for f, provs in cm.soft_avoid.items():
        if f in q:
            terms.append(0.5 * q[f])
    terms.append(0.002 * pulp.lpSum(y.values()))
    terms.append(-PREFERENCE_WEIGHT * pulp.lpSum(y[f] for f in cm.preferred if f in y))
    cost_expr = pulp.lpSum((fn[f]["price_per_100g"] or 0.0) * fn[f]["serving_g"] / 100.0 * q[f] for f in foods)
    terms.append((0.05 if cm.spec.objective == "cost" else 0.0005) * cost_expr)
    for name, e, scale, _ in slacks:
        terms.append(ELASTIC_PENALTY * e / scale)
    prob += pulp.lpSum(terms)

    # --- solve ----------------------------------------------------------------
    if solver == "auto":
        solver = "highs" if "HiGHS" in pulp.listSolvers(onlyAvailable=True) else "cbc"
    if solver == "highs":
        engine = pulp.HiGHS(msg=False, timeLimit=time_limit, gapRel=0.01, threads=threads)
    else:
        engine = pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=0.01)
    try:
        prob.solve(engine)
    except pulp.PulpSolverError as exc:  # pragma: no cover
        return Plan(status="error", message=str(exc), solve_ms=_ms(t0))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        kind = {"Infeasible": "infeasible", "Not Solved": "no_plan"}.get(status, "error")
        return Plan(status=kind, message=f"solver status: {status} (time limit {time_limit} s)"
                    if kind == "no_plan" else f"solver status: {status}", solve_ms=_ms(t0))

    plan_ = Plan(status="optimal", objective=pulp.value(prob.objective), solve_ms=_ms(t0), solver=solver,
                 proven_optimal=prob.sol_status == pulp.LpSolutionOptimal)
    for (f, m), v in x.items():
        val = int(round(v.value() or 0))
        if val:
            plan_.meal_servings.setdefault(f, {})[m] = val
            plan_.servings[f] = plan_.servings.get(f, 0) + val
    plan_.intake = compute_intake(kg, plan_.servings)
    plan_.cost = sum((fn[f]["price_per_100g"] or 0.0) * fn[f]["serving_g"] / 100.0 * s
                     for f, s in plan_.servings.items())
    for name, e, _, provs in slacks:
        amount = e.value() or 0.0
        if amount > 1e-6:
            plan_.relaxations.append({"row": name, "amount": round(amount, 2),
                                      "provenance": [p.as_dict() for p in provs]})
    return plan_


def compute_intake(kg: KG, servings: dict[str, int]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for f, s in servings.items():
        g = kg.nodes[f]["serving_g"] * s
        for n, amt in kg.food_nutrients(f).items():
            totals[n] = totals.get(n, 0.0) + amt * g / 100.0
    return {n: round(v, 2) for n, v in totals.items()}


def _k(nid: str) -> str:
    return nid.split(":", 1)[1]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
