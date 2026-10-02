"""Generate benchmark v1: profiles whose preferences or cost pull toward forbidden foods.

Each profile has a natural-language query, a gold JSON spec and a tier:
  T1  nutrient targets only
  T2  one semantic rule (allergy, diet, drug or condition), with a pull toward a forbidden food
  T3  2-4 interacting rules (hierarchy, exceptions, conditional rules)
  T4  a user target that contradicts a guideline rule (infeasible at rule level)

The infeasibility label is computed from the gold rule table only (no solver),
so it cannot be biased by the system under test.

Usage: python scripts/make_benchmark.py --out profiles/v1 --seed 7
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kgdiet.kg import KG  # noqa: E402
from kgdiet.spec import UserSpec  # noqa: E402
from kgdiet.verify import Gold  # noqa: E402

# Allergy -> foods that are forbidden only through the hierarchy or a tag, used as "pull" preferences
ALLERGY_PULL = {"tree nuts": ["Almonds", "Walnuts"], "fish": ["Salmon (cooked)", "Tuna (canned in water)"],
                "milk": ["Greek yogurt (plain nonfat)", "Cheddar cheese"], "egg": ["Egg (hard-boiled)"],
                "peanut": ["Peanut butter"]}
DIET_PULL = {"vegan": ["Greek yogurt (plain nonfat)", "Egg (hard-boiled)", "Chicken breast (cooked)"],
             "vegetarian": ["Chicken breast (cooked)", "Salmon (cooked)"],
             "jain": ["Potato (baked with skin)", "Carrot (raw)", "Egg (hard-boiled)"]}
DRUG_PULL = {"warfarin": ["Kale (raw)", "Spinach (raw)"], "phenelzine": ["Cheddar cheese", "Parmesan cheese"]}
CONDITIONS = ["t2dm", "hypertension", "ckd"]
WORDS = {"t2dm": "type 2 diabetes", "hypertension": "high blood pressure", "ckd": "chronic kidney disease stage 3"}


def person(rng: random.Random) -> dict:
    sex = rng.choice(["F", "M"])
    age = rng.randint(25, 75)
    weight = rng.randint(52, 75) if sex == "F" else rng.randint(65, 95)
    base = (1600 if sex == "F" else 2000) + rng.choice([0, 100, 200, 300])
    return dict(sex=sex, age=age, weight_kg=weight, kcal_min=base, kcal_max=base + 200)


def query(p: dict, extra: list[str]) -> str:
    who = f"I'm a {p['age']}-year-old {'woman' if p['sex'] == 'F' else 'man'}, {p['weight_kg']} kg"
    kcal = f"{p['kcal_min']}-{p['kcal_max']} kcal a day"
    return f"{who}. " + " ".join(extra) + f" Plan a day at {kcal}."


def rule_level_infeasible(gold: Gold, spec: UserSpec) -> bool:
    lo: dict[str, float] = {}
    hi: dict[str, float] = {}
    for r in gold.gold_rules(spec):
        if r.kind == "min":
            lo[r.target] = max(lo.get(r.target, 0.0), r.value)
        elif r.kind == "max":
            hi[r.target] = min(hi.get(r.target, float("inf")), r.value)
        elif r.kind == "per_1000kcal_min":  # lowest energy in the band gives the weakest floor
            lo[r.target] = max(lo.get(r.target, 0.0), r.value / 1000 * spec.kcal_min)
    for n, b in spec.nutrient_bounds.items():
        if b.get("min") is not None:
            lo[n] = max(lo.get(n, 0.0), b["min"])
        if b.get("max") is not None:
            hi[n] = min(hi.get(n, float("inf")), b["max"])
    return any(lo[n] > hi.get(n, float("inf")) + 1e-9 for n in lo)


def build(seed: int, targeted: bool = True) -> list[dict]:
    rng = random.Random(seed)
    out: list[dict] = []
    prefix = "v2" if targeted else "v1"

    def add(tier: str, extra: list[str], **fields) -> None:
        p = person(rng)
        p.update(fields.pop("override", {}))
        spec = dict(id=f"{prefix}_{len(out) + 1:03d}_{tier}", tier=tier, **p, **fields)
        spec["query"] = query(p, extra)
        out.append(spec)

    for i in range(10):  # T1
        if i % 2:
            add("T1", ["I want at least 100 g of protein."], nutrient_bounds={"protein_g": {"min": 100}},
                objective=rng.choice(["nutrition", "cost"]))
        else:
            add("T1", ["I want a balanced day."], objective=rng.choice(["nutrition", "cost"]))

    for allergy, pull in ALLERGY_PULL.items():  # T2 allergies (10)
        for _ in range(2):
            add("T2", [f"I'm allergic to {allergy}, but I love {pull[0].split(' (')[0].lower()}."],
                allergies=[allergy], preferred_foods=pull, objective=rng.choice(["nutrition", "cost"]))
    for diet, pull in DIET_PULL.items():  # T2 diets (6)
        for _ in range(2):
            add("T2", [f"I follow a {diet} diet."], diets=[diet], preferred_foods=pull, objective="cost")
    for drug, pull in DRUG_PULL.items():  # T2 drugs (4)
        for _ in range(2):
            add("T2", [f"I take {drug}. I like {pull[0].split(' (')[0].lower()}."], drugs=[drug],
                preferred_foods=pull, objective=rng.choice(["nutrition", "cost"]))
    for cond in CONDITIONS:  # T2 single condition (6)
        for _ in range(2):
            add("T2", [f"I have {WORDS[cond]}."], conditions=[cond], preferred_foods=["Cheddar cheese", "Potato (baked with skin)"],
                objective=rng.choice(["nutrition", "cost"]))

    combos = [  # T3 (20): interacting rules
        (["hypertension", "ckd"], [], [], []),
        (["hypertension", "ckd"], ["fish"], [], []),
        (["t2dm", "hypertension"], ["tree nuts"], [], []),
        (["t2dm", "hypertension"], ["milk"], [], []),
        (["t2dm", "ckd"], [], [], []),
        (["t2dm", "ckd", "hypertension"], [], [], []),
        (["ckd"], [], ["jain"], []),
        (["t2dm"], [], ["vegan"], ["warfarin"]),
        (["hypertension"], ["tree nuts", "peanut"], ["vegetarian"], []),
        (["ckd"], ["egg"], [], ["phenelzine"]),
    ]
    for conds, alls, diets, drugs in combos:
        for _ in range(2):
            extra = []
            if conds:
                extra.append("I have " + " and ".join(WORDS[c] for c in conds) + ".")
            if alls:
                extra.append("I'm allergic to " + " and ".join(alls) + ".")
            if diets:
                extra.append(f"I follow a {diets[0]} diet.")
            if drugs:
                extra.append(f"I take {drugs[0]}.")
            pull = sum((ALLERGY_PULL.get(a, []) for a in alls), []) + sum((DIET_PULL.get(d, []) for d in diets), []) \
                + sum((DRUG_PULL.get(d, []) for d in drugs), []) + ["Potato (baked with skin)", "Cheddar cheese"]
            add("T3", extra, conditions=conds, allergies=alls, diets=diets, drugs=drugs,
                preferred_foods=sorted(set(pull)), objective="cost")

    if targeted:  # run 2: power for the hierarchy and rule-context ablations (30 profiles)
        for allergy, pull in [("tree nuts", ["Almonds", "Cashews", "Walnuts"]),
                              ("fish", ["Salmon (cooked)", "Tuna (canned in water)", "Cod (cooked)"])]:
            for _ in range(6):
                add("T2", [f"I'm allergic to {allergy}. I love {pull[0].split(' (')[0].lower()}."],
                    allergies=[allergy], preferred_foods=pull, objective="nutrition")
        for diet, pull in [("vegan", ["Greek yogurt (plain nonfat)", "Cheddar cheese", "Milk (2% fat)"]),
                           ("jain", ["Onion (raw)", "Carrot (raw)", "Sweet potato (baked)"])]:
            for _ in range(3):
                add("T2", [f"I follow a {diet} diet."], diets=[diet], preferred_foods=pull, objective="nutrition")
        for conds in (["hypertension", "ckd"], ["t2dm", "ckd"]):
            for _ in range(6):
                add("T3", ["I have " + " and ".join(WORDS[c] for c in conds) + "."], conditions=conds,
                    preferred_foods=["Potato (baked with skin)", "Banana", "Chicken breast (cooked)"],
                    objective="nutrition")

    t4 = [  # user targets that contradict a guideline (6)
        (["ckd"], {"protein_g": {"min": 120}}, "I want 120 g of protein a day."),
        (["ckd"], {"protein_g": {"min": 90}}, "I want at least 90 g of protein."),
        (["hypertension"], {"sodium_mg": {"min": 2000}}, "I need at least 2,000 mg of sodium for training."),
        (["t2dm"], {"fiber_g": {"max": 10}}, "Keep fiber under 10 g; it upsets my stomach."),
        (["t2dm", "ckd"], {"protein_g": {"max": 40}}, "Keep protein under 40 g."),
        (["hypertension", "t2dm"], {"sodium_mg": {"min": 2500}}, "I need at least 2,500 mg of sodium."),
    ]
    for conds, bounds, text in t4:
        add("T4", ["I have " + " and ".join(WORDS[c] for c in conds) + ".", text],
            conditions=conds, nutrient_bounds=bounds)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "profiles" / "v2"))
    ap.add_argument("--no-targeted", action="store_true", help="v1 set (62 profiles) without targeted profiles")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    kg, gold = KG.load(), Gold()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    counts: dict[str, int] = {}
    for spec in build(args.seed, targeted=not args.no_targeted):
        s = UserSpec.from_dict(dict(spec)).resolve(kg)
        spec["expect_infeasible"] = rule_level_infeasible(gold, s)
        if spec["tier"] == "T4" and not spec["expect_infeasible"]:
            print(f"warning: {spec['id']} is labelled T4 but is rule-feasible", file=sys.stderr)
        (out / f"{spec['id']}.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        counts[spec["tier"]] = counts.get(spec["tier"], 0) + 1
    print(f"wrote {sum(counts.values())} profiles to {out}: {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    main()
