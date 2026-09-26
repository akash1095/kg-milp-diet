from pathlib import Path

import pytest

from kgdiet import CompileOptions, KG, UserSpec, compile_spec, plan
from kgdiet.spec import SpecError
from kgdiet.verify import Gold

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def kg():
    return KG.load()


def spec(kg, **kw):
    base = dict(id="t", age=40, sex="F", weight_kg=65, kcal_min=1800, kcal_max=2000)
    base.update(kw)
    return UserSpec.from_dict(base).resolve(kg)


def test_kg_counts(kg):
    s = kg.stats()
    assert s["nodes"]["Food"] == 50
    assert s["nodes"]["Condition"] == 3
    assert sum(1 for n in kg.ids("Allergen") if not kg.out(n, "SUBCLASS_OF")) == 5


def test_alias_resolution(kg):
    assert kg.resolve("Condition", "high blood pressure") == "Condition:hypertension"
    assert kg.resolve("Allergen", "tree nuts") == "Allergen:tree_nut"


def test_unknown_term_rejected(kg):
    with pytest.raises(SpecError):
        spec(kg, conditions=["gout"])


def test_tree_nut_hierarchy(kg):
    s = spec(kg, allergies=["tree nut"])
    full = compile_spec(kg, s)
    flat = compile_spec(kg, s, CompileOptions(hierarchy=False))
    names = {kg.name(f) for f in full.exclusions}
    assert {"Almonds", "Cashews", "Walnuts"} <= names
    assert not flat.exclusions  # no food is tagged with the parent allergen directly


def test_jain_excludes_roots_and_animals_keeps_dairy(kg):
    cm = compile_spec(kg, spec(kg, diets=["jain"]))
    ex = {kg.name(f) for f in cm.exclusions}
    assert {"Potato (baked with skin)", "Carrot (raw)", "Chicken breast (cooked)", "Egg (hard-boiled)"} <= ex
    assert "Milk (2% fat)" not in ex


def test_tightest_bound_and_provenance(kg):
    cm = compile_spec(kg, spec(kg, age=55, conditions=["t2dm", "hypertension"]))
    hi = cm.merged["sodium_mg"]["hi"]
    assert hi["value"] == 1500
    assert "Condition:hypertension" in hi["binding"][0].prov.path
    assert "B_sodium_mg_hi" in cm.provenance_registry()


def test_per_kg_rule(kg):
    cm = compile_spec(kg, spec(kg, weight_kg=60, conditions=["ckd"]))
    assert cm.merged["protein_g"]["hi"]["value"] == pytest.approx(48.0)


def test_presolve_conflict_detected(kg):
    cm = compile_spec(kg, spec(kg, age=62, sex="M", conditions=["hypertension", "ckd"]))
    assert [c.nutrient for c in cm.conflicts] == ["potassium_mg"]


def test_full_plan_passes_independent_check(kg):
    s = spec(kg, age=55, weight_kg=70, kcal_min=1800, kcal_max=1900,
             conditions=["t2dm", "hypertension"], allergies=["tree nuts"])
    p = plan(kg, compile_spec(kg, s), time_limit=20)
    assert p.status == "optimal"
    ev = Gold().evaluate(s, p.servings, p.intake)
    assert ev.semantic_ok and ev.energy_ok


def test_table_baseline_can_violate(kg):
    s = spec(kg, allergies=["tree nuts"], preferred_foods=["Almonds"], nutrient_bounds={"fat_g": {"min": 70}})
    table = CompileOptions(hierarchy=False, clinical_rules=False, drug_rules=False, diet_rules=False)
    cm = compile_spec(kg, s, table)
    assert "Food:F14" in cm.allowed_foods  # almonds stay available without the hierarchy


def test_substitute_for_excluded_preference(kg):
    cm = compile_spec(kg, spec(kg, allergies=["tree nuts"], preferred_foods=["Almonds"]))
    assert cm.substitutes and kg.name(cm.substitutes[0]["substitute"]) == "Sunflower seeds"
