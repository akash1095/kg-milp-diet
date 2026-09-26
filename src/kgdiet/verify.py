"""Independent plan checker (the evaluation's gold standard).

It reads the raw CSV tables directly and re-implements the semantics with
its own code (ancestor walks, rule lookups), sharing nothing with the KG
query layer or the compiler. Any system's plan (ours, ablations, baselines)
is scored the same way.

For the paper, replace ``gold_rules`` with hand-written, passage-cited gold
constraints per benchmark profile.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from .kg import DEFAULT_DATA_DIR
from .spec import UserSpec

TOL = 0.005  # 0.5% relative tolerance for rounding


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@dataclass
class GoldRule:
    kind: str          # exclude | min | max | pct_kcal_max | per_1000kcal_min
    target: str        # food id or nutrient id
    value: float = 0.0
    label: str = ""
    semantic: bool = True


@dataclass
class Evaluation:
    semantic_violations: list[str] = field(default_factory=list)
    ul_violations: list[str] = field(default_factory=list)
    energy_ok: bool = True
    shortfall: float = 0.0          # mean relative DRI shortfall (0 = all goals met)
    n_foods: int = 0

    @property
    def semantic_ok(self) -> bool:
        return not self.semantic_violations


class Gold:
    def __init__(self, data_dir: str | Path = DEFAULT_DATA_DIR) -> None:
        d = Path(data_dir)
        self.foods = {r["food_id"]: r for r in _rows(d / "foods.csv")}
        self.class_parent = {r["class_id"]: r["parent"] for r in _rows(d / "food_classes.csv")}
        self.allergen_parent = {r["allergen_id"]: r["parent"] for r in _rows(d / "allergens.csv")}
        self.cond_rules = _rows(d / "condition_rules.csv")
        self.drug_rules = _rows(d / "drug_rules.csv")
        self.diet_rules = _rows(d / "diet_rules.csv")
        self.dri = _rows(d / "dri.csv")
        self.kcal_per_g = {r["nutrient_id"]: float(r["kcal_per_g"])
                           for r in _rows(d / "nutrients.csv") if r["kcal_per_g"]}

    @staticmethod
    def _ancestors(node: str, parent: dict[str, str]) -> set[str]:
        out, cur = {node}, node
        while parent.get(cur):
            cur = parent[cur]
            out.add(cur)
        return out

    def gold_rules(self, spec: UserSpec) -> list[GoldRule]:
        k = lambda ids: [i.split(":", 1)[1] for i in ids]  # noqa: E731
        allergies, diets, drugs, conds = k(spec.allergy_ids), k(spec.diet_ids), k(spec.drug_ids), k(spec.condition_ids)
        rules: list[GoldRule] = []
        forbidden_classes = {r["forbids_class"]: r["diet_id"] for r in self.diet_rules if r["diet_id"] in diets}
        avoid_classes = {r["target"]: r["drug_id"] for r in self.drug_rules
                         if r["rule_type"] == "AVOID" and r["drug_id"] in drugs and (r["severity"] or "high") == "high"}
        for fid, food in self.foods.items():
            for a in filter(None, food["allergens"].split("|")):
                hit = self._ancestors(a, self.allergen_parent) & set(allergies)
                if hit:
                    rules.append(GoldRule("exclude", fid, label=f"{food['name']}: allergy {sorted(hit)[0]}"))
            anc = self._ancestors(food["class"], self.class_parent)
            for cls, diet in forbidden_classes.items():
                if cls in anc:
                    rules.append(GoldRule("exclude", fid, label=f"{food['name']}: {diet} forbids {cls}"))
            for cls, drug in avoid_classes.items():
                if cls in anc:
                    rules.append(GoldRule("exclude", fid, label=f"{food['name']}: {drug} avoid {cls}"))
        for r in self.cond_rules:
            if r["condition_id"] not in conds:
                continue
            sense = "max" if r["rule_type"] == "LIMITS" else "min"
            v = float(r["value"])
            lab = f"{r['condition_id']} {r['rule_type']} {r['nutrient_id']} {v:g} {r['basis']}"
            if r["basis"] == "per_day":
                rules.append(GoldRule(sense, r["nutrient_id"], v, lab))
            elif r["basis"] == "per_kg":
                rules.append(GoldRule(sense, r["nutrient_id"], v * spec.weight_kg, lab))
            elif r["basis"] == "pct_kcal":
                rules.append(GoldRule(f"pct_kcal_{sense}", r["nutrient_id"], v, lab))
            elif r["basis"] == "per_1000kcal":
                rules.append(GoldRule(f"per_1000kcal_{sense}", r["nutrient_id"], v, lab))
        for r in self.drug_rules:
            if r["rule_type"] == "STABLE" and r["drug_id"] in drugs:
                lab = f"{r['drug_id']} STABLE {r['target']} {r['lo']}-{r['hi']}"
                rules.append(GoldRule("min", r["target"], float(r["lo"]), lab))
                rules.append(GoldRule("max", r["target"], float(r["hi"]), lab))
        stage = spec.life_stage.split(":", 1)[1] if spec.life_stage else ""
        for r in self.dri:
            if r["stage_id"] == stage and r["ul"]:
                rules.append(GoldRule("max", r["nutrient_id"], float(r["ul"]),
                                      f"DRI upper limit {r['nutrient_id']} {r['ul']}", semantic=False))
        return rules

    def evaluate(self, spec: UserSpec, servings: dict[str, int], intake: dict[str, float]) -> Evaluation:
        ev = Evaluation(n_foods=len(servings))
        present = {f.split(":", 1)[1] for f, s in servings.items() if s > 0}
        energy = intake.get("energy_kcal", 0.0)
        ev.energy_ok = spec.kcal_min * (1 - TOL) <= energy <= spec.kcal_max * (1 + TOL)
        for r in self.gold_rules(spec):
            bad = False
            a = intake.get(r.target, 0.0)
            if r.kind == "exclude":
                bad = r.target in present
            elif r.kind == "max":
                bad = a > r.value * (1 + TOL)
            elif r.kind == "min":
                bad = a < r.value * (1 - TOL)
            elif r.kind == "pct_kcal_max":
                bad = energy > 0 and self.kcal_per_g[r.target] * a > r.value / 100 * energy * (1 + TOL)
            elif r.kind == "pct_kcal_min":
                bad = energy > 0 and self.kcal_per_g[r.target] * a < r.value / 100 * energy * (1 - TOL)
            elif r.kind == "per_1000kcal_min":
                bad = a < r.value / 1000 * energy * (1 - TOL)
            elif r.kind == "per_1000kcal_max":
                bad = a > r.value / 1000 * energy * (1 + TOL)
            if bad:
                msg = f"{r.label} (plan: {a:g})" if r.kind != "exclude" else r.label
                (ev.semantic_violations if r.semantic else ev.ul_violations).append(msg)
        stage = spec.life_stage.split(":", 1)[1] if spec.life_stage else ""
        gaps = [max(0.0, (float(r["rda"]) - intake.get(r["nutrient_id"], 0.0)) / float(r["rda"]))
                for r in self.dri if r["stage_id"] == stage and r["rda"]]
        ev.shortfall = round(sum(gaps) / len(gaps), 4) if gaps else 0.0
        return ev
