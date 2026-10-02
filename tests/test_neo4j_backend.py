"""The Neo4j backend must answer every query exactly like the in-memory KG.

Needs the Neo4j container (`docker compose up -d`); skipped when it is not
reachable. The fixture reloads Neo4j from data/*.csv (the source of truth).
"""
import dataclasses
import json
from pathlib import Path

import pytest

from kgdiet import CompileOptions, KG, UserSpec, compile_spec

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def kgs():
    pytest.importorskip("neo4j")
    from kgdiet.neo4j_kg import KGUnavailable, Neo4jKG, connect, push
    mem = KG.load()
    try:
        driver = connect()
        driver.verify_connectivity()
    except Exception as exc:  # no container, no password, ...
        pytest.skip(f"Neo4j not reachable: {exc}")
    try:
        push(mem, driver, source="tests")
        neo = Neo4jKG(driver=driver)
    except KGUnavailable as exc:
        driver.close()
        pytest.skip(str(exc))
    yield mem, neo
    neo.close()


def test_load_counts(kgs):
    mem, neo = kgs
    assert neo.stats() == mem.stats()


def test_node_table(kgs):
    mem, neo = kgs
    assert list(neo.nodes.items()) == list(mem.nodes.items())  # same props, nulls and order


@pytest.mark.parametrize("hierarchy", [True, False])
def test_exclusion_queries(kgs, hierarchy):
    mem, neo = kgs
    for q, label in (("q_allergen_foods", "Allergen"), ("q_diet_forbidden_foods", "DietPattern"),
                     ("q_drug_avoid_foods", "Drug")):
        ids = mem.ids(label)
        assert getattr(neo, q)(ids, hierarchy) == getattr(mem, q)(ids, hierarchy), q
        for one in ids:
            assert getattr(neo, q)([one], hierarchy) == getattr(mem, q)([one], hierarchy), (q, one)


@pytest.mark.parametrize("context", [True, False])
def test_condition_rules(kgs, context):
    mem, neo = kgs
    conds = mem.ids("Condition")
    assert neo.q_condition_rules(conds, context) == mem.q_condition_rules(conds, context)
    for c in conds:
        assert neo.q_condition_rules([c], context) == mem.q_condition_rules([c], context)


def test_other_queries(kgs):
    mem, neo = kgs
    assert neo.q_drug_stable(mem.ids("Drug")) == mem.q_drug_stable(mem.ids("Drug"))
    for s in mem.ids("LifeStage"):
        assert neo.q_dri(s) == mem.q_dri(s)
    assert list(neo.q_meal_slots().items()) == list(mem.q_meal_slots().items())
    for f in mem.ids("Food"):
        assert neo.q_substitutes(f) == mem.q_substitutes(f)
        assert list(neo.food_nutrients(f).items()) == list(mem.food_nutrients(f).items())
        assert neo.food_class(f) == mem.food_class(f)
    assert neo.q_life_stage("F", 55) == mem.q_life_stage("F", 55)
    assert neo.resolve("Condition", "high blood pressure") == "Condition:hypertension"


@pytest.mark.parametrize("profile", sorted((ROOT / "profiles").glob("p*.json")), ids=lambda p: p.stem)
def test_compiled_model_identical(kgs, profile):
    """Same rows in the same order give the same MILP, hence the same plan."""
    mem, neo = kgs
    raw = json.loads(profile.read_text(encoding="utf-8"))
    for opts in (CompileOptions(), CompileOptions(hierarchy=False, rule_context=False)):
        a = compile_spec(mem, UserSpec.from_dict(raw).resolve(mem), opts)
        b = compile_spec(neo, UserSpec.from_dict(raw).resolve(neo), opts)
        assert _canon(a) == _canon(b)


def _canon(obj):
    """Comparable form that keeps list and dict order (it drives the MILP) but not set order."""
    if dataclasses.is_dataclass(obj):
        return [(f.name, _canon(getattr(obj, f.name))) for f in dataclasses.fields(obj)]
    if isinstance(obj, dict):
        return [(k, _canon(v)) for k, v in obj.items()]
    if isinstance(obj, (list, tuple)):
        return [_canon(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    return obj
