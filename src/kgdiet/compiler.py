"""KG-to-constraint compiler (mapping rules M1-M12).

Input: a resolved UserSpec and the KG. Output: a solver-independent
``CompiledModel`` in which every exclusion, bound, ratio row and goal
carries the provenance (rule id, KG path, guideline source) that produced it.

Compiler rules (see the proposal's "KG schema and mapping" tab):
  1. Units: rule values are converted to the nutrient's unit; unknown units stop compilation.
  2. Merge: several upper bounds keep the tightest (min); several lower bounds keep the highest.
  3. Pre-solve conflicts: merged lower > merged upper is reported before solving.
  4. Provenance: every row name maps to its provenance records.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .kg import KG
from .spec import UserSpec

UNIT_FACTORS = {"g": 1.0, "mg": 1e-3, "ug": 1e-6, "µg": 1e-6, "mcg": 1e-6}


class CompileError(ValueError):
    pass


@dataclass
class CompileOptions:
    """Switches used for ablations; the full method has everything on."""
    hierarchy: bool = True        # follow SUBCLASS_OF*0.. (off = direct tags only)
    clinical_rules: bool = True   # Condition LIMITS / REQUIRES (M5-M7)
    drug_rules: bool = True       # Drug STABLE / AVOID (M4, M8)
    diet_rules: bool = True       # DietPattern FORBIDS (M3)
    allergy_rules: bool = True    # HAS_ALLERGY (M2)
    dri: bool = True              # DRI goals and upper limits (M9, M10)
    meals: bool = True            # meal slots and shares (M11)


@dataclass
class Prov:
    rule: str
    path: list[str]
    source: str = ""
    status: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "kg_path": self.path, "source": self.source, "status": self.status}


@dataclass
class Bound:
    nutrient: str
    sense: str          # "min" | "max"
    value: float        # in the nutrient's unit, per day
    rule: str
    prov: Prov


@dataclass
class RatioRow:
    """Linear row relative to energy E. pct_kcal: kcal_per_g*I <= share*E ; per_1000kcal: I >= rate*E."""
    name: str
    nutrient: str
    sense: str
    coef_intake: float
    coef_energy: float
    rule: str
    prov: Prov
    label: str


@dataclass
class Goal:
    nutrient: str
    target: float
    weight: float
    prov: Prov


@dataclass
class Conflict:
    nutrient: str
    lo: float
    hi: float
    lo_provs: list[Prov]
    hi_provs: list[Prov]


@dataclass
class CompiledModel:
    spec: UserSpec
    options: CompileOptions
    all_foods: list[str]
    exclusions: dict[str, list[Prov]] = field(default_factory=dict)
    soft_avoid: dict[str, list[Prov]] = field(default_factory=dict)
    bounds: list[Bound] = field(default_factory=list)
    merged: dict[str, dict] = field(default_factory=dict)
    ratios: list[RatioRow] = field(default_factory=list)
    goals: list[Goal] = field(default_factory=list)
    meals: dict[str, dict] | None = None
    conflicts: list[Conflict] = field(default_factory=list)
    substitutes: list[dict] = field(default_factory=list)
    preferred: list[str] = field(default_factory=list)
    max_foods: int = 12

    @property
    def allowed_foods(self) -> list[str]:
        return [f for f in self.all_foods if f not in self.exclusions]

    def provenance_registry(self) -> dict[str, list[dict]]:
        """Row name -> provenance records (what the explainer is allowed to cite)."""
        reg: dict[str, list[dict]] = {}
        for f, provs in self.exclusions.items():
            reg[f"EX_{f.split(':', 1)[1]}"] = [p.as_dict() for p in provs]
        for n, m in self.merged.items():
            for side in ("lo", "hi"):
                if m.get(side):
                    reg[f"B_{n}_{side}"] = [b.prov.as_dict() for b in m[side]["binding"]]
        for r in self.ratios:
            reg[r.name] = [r.prov.as_dict()]
        for g in self.goals:
            reg[f"G_{g.nutrient}"] = [g.prov.as_dict()]
        return reg


def _key(nid: str) -> str:
    return nid.split(":", 1)[1]


def convert(value: float, unit: str, target_unit: str) -> float:
    if unit == target_unit:
        return value
    if unit not in UNIT_FACTORS or target_unit not in UNIT_FACTORS:
        raise CompileError(f"cannot convert {unit!r} to {target_unit!r}")
    return value * UNIT_FACTORS[unit] / UNIT_FACTORS[target_unit]


def compile_spec(kg: KG, spec: UserSpec, options: CompileOptions | None = None) -> CompiledModel:
    opts = options or CompileOptions()
    if spec.life_stage is None:
        spec.resolve(kg)
    cm = CompiledModel(spec=spec, options=opts, all_foods=sorted(kg.ids("Food")),
                       max_foods=spec.max_foods)

    # M1: user's own energy band, nutrient bounds and food count (from the spec)
    user_prov = Prov("M1", ["User", "spec"], "user request")
    cm.bounds.append(Bound("energy_kcal", "min", spec.kcal_min, "M1", user_prov))
    cm.bounds.append(Bound("energy_kcal", "max", spec.kcal_max, "M1", user_prov))
    for n, b in spec.nutrient_bounds.items():
        for sense in ("min", "max"):
            if b.get(sense) is not None:
                cm.bounds.append(Bound(n, sense, float(b[sense]), "M1", user_prov))

    # M2-M4: exclusions (hard) and soft avoidance
    def _exclude(rows: list[dict], rule: str) -> None:
        for r in rows:
            prov = Prov(rule, r["path"], r.get("source", ""))
            if rule == "M4" and r.get("severity", "high") != "high":
                cm.soft_avoid.setdefault(r["food"], []).append(prov)
            else:
                cm.exclusions.setdefault(r["food"], []).append(prov)

    if opts.allergy_rules:
        _exclude(kg.q_allergen_foods(spec.allergy_ids, opts.hierarchy), "M2")
    if opts.diet_rules:
        _exclude(kg.q_diet_forbidden_foods(spec.diet_ids, opts.hierarchy), "M3")
    if opts.drug_rules:
        _exclude(kg.q_drug_avoid_foods(spec.drug_ids, opts.hierarchy), "M4")

    # M5-M7: condition rules
    if opts.clinical_rules:
        for r in kg.q_condition_rules(spec.condition_ids):
            _compile_rate_rule(kg, cm, r, "M5" if r["rule_type"] == "LIMITS" else "M7")

    # M8: drug ranges
    if opts.drug_rules:
        for r in kg.q_drug_stable(spec.drug_ids):
            n = _key(r["nutrient"])
            unit = kg.nodes[r["nutrient"]]["unit"]
            prov = Prov("M8", r["path"], r.get("source", ""), r.get("status", ""))
            cm.bounds.append(Bound(n, "min", convert(r["lo"], r["unit"], unit), "M8", prov))
            cm.bounds.append(Bound(n, "max", convert(r["hi"], r["unit"], unit), "M8", prov))

    # M9 / M10: DRI goals (soft) and upper limits (hard)
    if opts.dri:
        for r in kg.q_dri(spec.life_stage):
            n = _key(r["nutrient"])
            prov = Prov("M9", r["path"], r.get("source", ""), r.get("status", ""))
            if r.get("rda") is not None:
                cm.goals.append(Goal(n, r["rda"], 1.0, prov))
            if r.get("ul") is not None:
                prov10 = Prov("M10", r["path"], r.get("source", ""), r.get("status", ""))
                cm.bounds.append(Bound(n, "max", r["ul"], "M10", prov10))

    # M11: meal slots
    if opts.meals:
        cm.meals = kg.q_meal_slots()

    # Preferred foods: bonus if allowed; M12 substitutes if excluded
    for f in spec.preferred_ids:
        if f in cm.exclusions:
            for s in kg.q_substitutes(f):
                if s["food"] not in cm.exclusions:
                    cm.substitutes.append({
                        "for": f, "substitute": s["food"], "similarity": s["similarity"],
                        "prov": Prov("M12", [f, "SUBSTITUTE_FOR(inv)", s["food"]], "KG substitute edge")})
                    cm.preferred.append(s["food"])
                    break
        else:
            cm.preferred.append(f)

    _merge(cm)
    return cm


def _compile_rate_rule(kg: KG, cm: CompiledModel, r: dict, rule: str) -> None:
    n = _key(r["nutrient"])
    node = kg.nodes[r["nutrient"]]
    basis = r["basis"]
    sense = "max" if r["rule_type"] == "LIMITS" else "min"
    raw = r["max"] if sense == "max" else r["min"]
    cond = kg.name(r["owner"])
    if basis == "pct_kcal":
        kpg = node.get("kcal_per_g")
        if not kpg:
            raise CompileError(f"{n} has no kcal_per_g; cannot apply a % of energy rule")
        prov = Prov("M6", r["path"], r.get("source", ""), r.get("status", ""))
        # kcal_per_g * I_n  (<= or >=)  (raw/100) * E
        cm.ratios.append(RatioRow(f"R_{n}_{sense}_pctkcal_{_key(r['owner'])}", n, sense, kpg,
                                  raw / 100.0, "M6", prov,
                                  f"{node['name']} {'≤' if sense == 'max' else '≥'} {raw:g}% of energy ({cond})"))
        return
    if basis == "per_1000kcal":
        value = convert(raw, r["unit"], node["unit"])
        prov = Prov("M7", r["path"], r.get("source", ""), r.get("status", ""))
        cm.ratios.append(RatioRow(f"R_{n}_{sense}_per1000_{_key(r['owner'])}", n, sense, 1.0,
                                  value / 1000.0, "M7", prov,
                                  f"{node['name']} {'≤' if sense == 'max' else '≥'} {raw:g} {r['unit']} per 1,000 kcal ({cond})"))
        return
    if basis == "per_kg":
        value = convert(raw * cm.spec.weight_kg, r["unit"], node["unit"])
    elif basis == "per_day":
        value = convert(raw, r["unit"], node["unit"])
    else:
        raise CompileError(f"unknown basis {basis!r} on {r['path']}")
    prov = Prov(rule, r["path"], r.get("source", ""), r.get("status", ""))
    cm.bounds.append(Bound(n, sense, value, rule, prov))


def _merge(cm: CompiledModel) -> None:
    """Tightest upper, highest lower; record binding provenance and conflicts."""
    by_n: dict[str, dict[str, list[Bound]]] = {}
    for b in cm.bounds:
        by_n.setdefault(b.nutrient, {"min": [], "max": []})[b.sense].append(b)
    for n, sides in by_n.items():
        entry: dict = {}
        if sides["min"]:
            v = max(b.value for b in sides["min"])
            entry["lo"] = {"value": v, "binding": [b for b in sides["min"] if abs(b.value - v) < 1e-9],
                           "all": sides["min"]}
        if sides["max"]:
            v = min(b.value for b in sides["max"])
            entry["hi"] = {"value": v, "binding": [b for b in sides["max"] if abs(b.value - v) < 1e-9],
                           "all": sides["max"]}
        cm.merged[n] = entry
        if entry.get("lo") and entry.get("hi") and entry["lo"]["value"] > entry["hi"]["value"] + 1e-9:
            cm.conflicts.append(Conflict(n, entry["lo"]["value"], entry["hi"]["value"],
                                         [b.prov for b in entry["lo"]["binding"]],
                                         [b.prov for b in entry["hi"]["binding"]]))
