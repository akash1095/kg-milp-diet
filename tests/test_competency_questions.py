"""Competency questions (CQs): questions the KG must be able to answer.

Each test answers one CQ through the KG query layer (the same queries as
cypher/templates.cypher) and checks the answer on a known case. If a schema
change breaks a CQ, this file fails.
"""
import pytest

from kgdiet import KG, UserSpec, compile_spec


@pytest.fixture(scope="module")
def kg():
    return KG.load()


def names(kg, ids):
    return {kg.name(i) for i in ids}


def test_cq01_allergy_foods_through_subclasses(kg):
    """CQ1: Which foods must someone allergic to X avoid, including subclasses of X?"""
    rows = kg.q_allergen_foods(["Allergen:tree_nut"])
    assert names(kg, (r["food"] for r in rows)) == {"Almonds", "Cashews", "Walnuts"}


def test_cq02_diet_forbidden_foods(kg):
    """CQ2: Which foods does diet pattern P forbid, through the class hierarchy?"""
    rows = kg.q_diet_forbidden_foods(["DietPattern:jain"])
    assert {"Potato (baked with skin)", "Onion (raw)", "Egg (hard-boiled)"} <= names(kg, (r["food"] for r in rows))


def test_cq03_drug_food_avoidance(kg):
    """CQ3: Which foods must someone taking drug D avoid, and how severe is it?"""
    rows = kg.q_drug_avoid_foods(["Drug:phenelzine"])
    assert names(kg, (r["food"] for r in rows)) == {"Cheddar cheese", "Parmesan cheese"}
    assert {r["severity"] for r in rows} == {"high"}


def test_cq04_numeric_limits_for_condition(kg):
    """CQ4: Which numeric limits apply to condition C, and on what basis?"""
    rows = [r for r in kg.q_condition_rules(["Condition:ckd3"]) if r["active"] and r["rule_type"] != "ADVISES"]
    assert {(r["nutrient"], r["basis"]) for r in rows} == {("Nutrient:protein_g", "per_kg"), ("Nutrient:sodium_mg", "per_day")}


def test_cq05_rules_suspended_by_another_condition(kg):
    """CQ5: Which rules of C are suspended because the person also has C2?"""
    rows = kg.q_condition_rules(["Condition:hypertension", "Condition:ckd3"])
    assert {r["rule_id"] for r in rows if r["blocked"]} == {"R05"}


def test_cq06_rules_that_need_another_condition(kg):
    """CQ6: Which rules apply only when another condition is also present?"""
    rows = kg.q_condition_rules(["Condition:t2dm", "Condition:ckd3"])
    assert {r["rule_id"] for r in rows if r["only_if"] and r["active"]} == {"R03", "R07a", "R07b"}


def test_cq07_guideline_behind_a_rule(kg):
    """CQ7: Which guideline, statement and quote support a rule?"""
    row = next(r for r in kg.q_condition_rules(["Condition:ckd3"]) if r["rule_id"] == "R08")
    assert row["source"].startswith("KDOQI") and row["statement"] == "6.5.1" and "2.3 g/d" in row["quote"]


def test_cq08_qualitative_recommendations(kg):
    """CQ8: Which recommendations for C are qualitative only (not compiled)?"""
    rows = [r for r in kg.q_condition_rules(["Condition:t2dm"]) if r["rule_type"] == "ADVISES"]
    assert {r["rule_id"] for r in rows} == {"R10", "R11"}


def test_cq09_drug_nutrient_range(kg):
    """CQ9: What range must nutrient N stay in for someone taking drug D?"""
    (row,) = kg.q_drug_stable(["Drug:warfarin"])
    assert row["nutrient"] == "Nutrient:vitamin_k_ug" and (row["lo"], row["hi"]) == (90.0, 150.0)


def test_cq10_targets_by_age_and_sex(kg):
    """CQ10: What are the nutrient targets and upper limits for a given age and sex?"""
    stage = kg.q_life_stage("F", 60)
    dri = {r["nutrient"]: r for r in kg.q_dri(stage)}
    assert dri["Nutrient:calcium_mg"]["rda"] == 1200 and dri["Nutrient:calcium_mg"]["ul"] == 2000


def test_cq11_food_nutrients(kg):
    """CQ11: How much of nutrient N does food F contain per 100 g?"""
    assert kg.food_nutrients("Food:F19")["vitamin_k_ug"] > 400  # spinach is a vitamin K source


def test_cq12_meal_slots(kg):
    """CQ12: Which foods suit meal slot M, and what share of energy should M carry?"""
    slots = kg.q_meal_slots()
    snack = slots["MealSlot:snack"]
    assert "Food:F14" in snack["foods"] and snack["share_max"] == 0.15


def test_cq13_substitutes(kg):
    """CQ13: Which allowed food can replace an excluded food F?"""
    assert kg.q_substitutes("Food:F14")[0]["food"] == "Food:F17"


def test_cq14_contradicting_rules(kg):
    """CQ14: Which rules contradict each other for a given person (derived by the compiler)?"""
    spec = UserSpec.from_dict(dict(id="cq14", age=50, sex="M", weight_kg=70, kcal_min=2000, kcal_max=2200,
                                   conditions=["ckd"], nutrient_bounds={"protein_g": {"min": 120}})).resolve(kg)
    (conflict,) = compile_spec(kg, spec).conflicts
    assert conflict.nutrient == "protein_g" and conflict.hi_provs[0].rule_id == "R06"


def test_cq15_path_explaining_an_exclusion(kg):
    """CQ15: Which KG path explains why food F was excluded for a person?"""
    spec = UserSpec.from_dict(dict(id="cq15", age=40, sex="F", weight_kg=60, kcal_min=1800, kcal_max=2000,
                                   allergies=["tree nuts"])).resolve(kg)
    path = compile_spec(kg, spec).exclusions["Food:F14"][0].path
    assert path[:3] == ["User", "HAS_ALLERGY", "Allergen:tree_nut"] and "Allergen:almond" in path
