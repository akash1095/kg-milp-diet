"""User spec: the JSON the LLM parser produces (or a hand-written profile).

The validator resolves every free-text term to a KG node and rejects
anything it cannot resolve, so no unknown word ever reaches the compiler.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .kg import BaseKG

SPEC_SCHEMA = {
    "id": "str (profile id)",
    "query": "str (original natural-language request, optional)",
    "tier": "T1|T2|T3|T4 (benchmark tier, optional)",
    "age": "int",
    "sex": "F|M",
    "weight_kg": "float",
    "kcal_min": "float",
    "kcal_max": "float",
    "conditions": "list[str] (KG Condition names or aliases)",
    "allergies": "list[str] (KG Allergen names or aliases)",
    "drugs": "list[str] (KG Drug names or aliases)",
    "diets": "list[str] (KG DietPattern names or aliases)",
    "nutrient_bounds": "dict[nutrient_id, {min?, max?}] (user's own targets)",
    "max_foods": "int (default 12)",
    "preferred_foods": "list[str] (food ids or names, optional)",
    "objective": "nutrition|cost (default nutrition)",
    "expect_infeasible": "bool (benchmark label, optional)",
}


class SpecError(ValueError):
    pass


@dataclass
class UserSpec:
    id: str
    age: int
    sex: str
    weight_kg: float
    kcal_min: float
    kcal_max: float
    conditions: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    drugs: list[str] = field(default_factory=list)
    diets: list[str] = field(default_factory=list)
    nutrient_bounds: dict[str, dict] = field(default_factory=dict)
    max_foods: int = 12
    preferred_foods: list[str] = field(default_factory=list)
    objective: str = "nutrition"
    query: str = ""
    tier: str = ""
    expect_infeasible: bool = False
    # filled by resolve(): KG node ids
    life_stage: str | None = None
    condition_ids: list[str] = field(default_factory=list)
    allergy_ids: list[str] = field(default_factory=list)
    drug_ids: list[str] = field(default_factory=list)
    diet_ids: list[str] = field(default_factory=list)
    preferred_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "UserSpec":
        known = set(cls.__dataclass_fields__) - {
            "life_stage", "condition_ids", "allergy_ids", "drug_ids", "diet_ids", "preferred_ids"}
        unknown = set(d) - known
        if unknown:
            raise SpecError(f"unknown spec fields: {sorted(unknown)}")
        missing = [k for k in ("id", "age", "sex", "weight_kg", "kcal_min", "kcal_max") if k not in d]
        if missing:
            raise SpecError(f"missing required fields: {missing}")
        return cls(**d)

    @classmethod
    def from_file(cls, path: str | Path) -> "UserSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def resolve(self, kg: BaseKG) -> "UserSpec":
        """Validate values and map every term to a KG node id (raises SpecError)."""
        errors = []
        if self.sex.upper()[:1] not in ("F", "M"):
            errors.append(f"sex must be F or M, got {self.sex!r}")
        if not (1 <= self.age <= 120):
            errors.append(f"age out of range: {self.age}")
        if not (20 <= self.weight_kg <= 300):
            errors.append(f"weight_kg out of range: {self.weight_kg}")
        if not (800 <= self.kcal_min <= self.kcal_max <= 5000):
            errors.append(f"kcal band invalid: {self.kcal_min}-{self.kcal_max}")
        if self.objective not in ("nutrition", "cost"):
            errors.append(f"objective must be nutrition or cost, got {self.objective!r}")
        nutrients = {kg.nodes[n]["key"] for n in kg.ids("Nutrient")}
        for n, b in self.nutrient_bounds.items():
            if n not in nutrients:
                errors.append(f"unknown nutrient {n!r}")
            if set(b) - {"min", "max"}:
                errors.append(f"nutrient bound for {n} may only have min/max")

        def _resolve_all(label: str, words: list[str]) -> list[str]:
            ids = []
            for w in words:
                nid = kg.resolve(label, w)
                if nid is None:
                    errors.append(f"unknown {label} {w!r}")
                elif nid not in ids:
                    ids.append(nid)
            return ids

        self.condition_ids = _resolve_all("Condition", self.conditions)
        self.allergy_ids = _resolve_all("Allergen", self.allergies)
        self.drug_ids = _resolve_all("Drug", self.drugs)
        self.diet_ids = _resolve_all("DietPattern", self.diets)
        self.preferred_ids = []
        for w in self.preferred_foods:
            nid = f"Food:{w}" if f"Food:{w}" in kg.nodes else kg.resolve("Food", w)
            if nid is None:
                errors.append(f"unknown food {w!r}")
            else:
                self.preferred_ids.append(nid)
        self.life_stage = kg.q_life_stage(self.sex, self.age)
        if self.life_stage is None:
            errors.append(f"no DRI life stage for sex={self.sex} age={self.age} (adults 19+ only)")
        if errors:
            raise SpecError("; ".join(errors))
        return self
