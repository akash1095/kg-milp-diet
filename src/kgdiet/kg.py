"""In-memory property graph for the diet KG.

Each ``q_*`` method mirrors one parameterized Cypher template in
``cypher/templates.cypher``, so the compiler works the same whether the
graph lives here or in Neo4j. Every returned row carries the KG path that
produced it (used later as constraint provenance).
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data"

NUTRIENT_COLUMNS = [
    "energy_kcal", "protein_g", "carbs_g", "fat_g", "sat_fat_g", "fiber_g",
    "sugar_g", "sodium_mg", "potassium_mg", "phosphorus_mg", "calcium_mg",
    "iron_mg", "vitamin_k_ug",
]


@dataclass
class Edge:
    type: str
    src: str
    dst: str
    props: dict = field(default_factory=dict)


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                for row in csv.DictReader(fh)]


def _num(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


class KG:
    """Nodes are keyed by globally unique ids with a label prefix, e.g. ``Food:F01``."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.out_edges: dict[str, list[Edge]] = defaultdict(list)
        self.in_edges: dict[str, list[Edge]] = defaultdict(list)

    # ------------------------------------------------------------------ build
    def add_node(self, label: str, key: str, **props) -> str:
        nid = f"{label}:{key}"
        self.nodes[nid] = {"label": label, "key": key, **props}
        return nid

    def add_edge(self, etype: str, src: str, dst: str, **props) -> None:
        if src not in self.nodes or dst not in self.nodes:
            raise KeyError(f"{etype}: unknown node {src if src not in self.nodes else dst}")
        edge = Edge(etype, src, dst, props)
        self.out_edges[src].append(edge)
        self.in_edges[dst].append(edge)

    @classmethod
    def load(cls, data_dir: str | Path = DEFAULT_DATA_DIR) -> "KG":
        d = Path(data_dir)
        kg = cls()
        for r in _read(d / "nutrients.csv"):
            kg.add_node("Nutrient", r["nutrient_id"], name=r["name"], unit=r["unit"],
                        kcal_per_g=_num(r["kcal_per_g"]))
        for r in _read(d / "food_classes.csv"):
            kg.add_node("FoodClass", r["class_id"], name=r["label"])
        for r in _read(d / "food_classes.csv"):
            if r["parent"]:
                kg.add_edge("SUBCLASS_OF", f"FoodClass:{r['class_id']}", f"FoodClass:{r['parent']}")
        for r in _read(d / "allergens.csv"):
            kg.add_node("Allergen", r["allergen_id"], name=r["label"], list=r["list"],
                        aliases=_aliases(r))
        for r in _read(d / "allergens.csv"):
            if r["parent"]:
                kg.add_edge("SUBCLASS_OF", f"Allergen:{r['allergen_id']}", f"Allergen:{r['parent']}")
        for r in _read(d / "meal_slots.csv"):
            kg.add_node("MealSlot", r["slot_id"], name=r["label"], code=r["code"],
                        kcal_share_min=float(r["kcal_share_min"]),
                        kcal_share_max=float(r["kcal_share_max"]))
        code_to_slot = {n["code"]: nid for nid, n in kg.nodes.items() if n["label"] == "MealSlot"}
        for r in _read(d / "foods.csv"):
            fid = kg.add_node("Food", r["food_id"], name=r["name"], fdc_id=r["fdc_id"],
                              serving_g=float(r["serving_g"]),
                              max_servings=int(r["max_servings"]),
                              price_per_100g=_num(r["price_per_100g"]))
            kg.add_edge("IS_A", fid, f"FoodClass:{r['class']}")
            for a in filter(None, r["allergens"].split("|")):
                kg.add_edge("HAS_ALLERGEN", fid, f"Allergen:{a}", confidence="declared")
            for code in filter(None, r["meal_slots"].split("|")):
                kg.add_edge("SUITABLE_FOR", fid, code_to_slot[code])
            for n in NUTRIENT_COLUMNS:
                kg.add_edge("CONTAINS", fid, f"Nutrient:{n}", amount_per_100g=float(r[n]))
        for r in _read(d / "substitutes.csv"):
            kg.add_edge("SUBSTITUTE_FOR", f"Food:{r['substitute_id']}", f"Food:{r['food_id']}",
                        similarity=float(r["similarity"]))
        for r in _read(d / "life_stages.csv"):
            kg.add_node("LifeStage", r["stage_id"], name=_stage_name(r), sex=r["sex"],
                        age_min=int(r["age_min"]), age_max=int(r["age_max"]))
        for r in _read(d / "dri.csv"):
            kg.add_edge("DRI", f"LifeStage:{r['stage_id']}", f"Nutrient:{r['nutrient_id']}",
                        rda=_num(r["rda"]), ul=_num(r["ul"]), source=r["source"],
                        status=r["status"])
        for r in _read(d / "conditions.csv"):
            kg.add_node("Condition", r["condition_id"], name=r["label"], icd10=r["icd10"],
                        aliases=_aliases(r))
        for r in _read(d / "condition_rules.csv"):
            props = dict(unit=r["unit"], basis=r["basis"], source=r["source"],
                         section=r["section"], url=r["url"], status=r["status"])
            props["max" if r["rule_type"] == "LIMITS" else "min"] = float(r["value"])
            kg.add_edge(r["rule_type"], f"Condition:{r['condition_id']}",
                        f"Nutrient:{r['nutrient_id']}", **props)
        for r in _read(d / "drugs.csv"):
            kg.add_node("Drug", r["drug_id"], name=r["label"], code=r["code"], aliases=_aliases(r))
        for r in _read(d / "drug_rules.csv"):
            common = dict(source=r["source"], section=r["section"], url=r["url"], status=r["status"])
            if r["rule_type"] == "STABLE":
                kg.add_edge("STABLE", f"Drug:{r['drug_id']}", f"Nutrient:{r['target']}",
                            lo=float(r["lo"]), hi=float(r["hi"]), unit=r["unit"], **common)
            elif r["rule_type"] == "AVOID":
                kg.add_edge("AVOID", f"Drug:{r['drug_id']}", f"FoodClass:{r['target']}",
                            severity=r["severity"] or "high", **common)
        for r in _read(d / "diets.csv"):
            kg.add_node("DietPattern", r["diet_id"], name=r["label"], aliases=_aliases(r))
        for r in _read(d / "diet_rules.csv"):
            kg.add_edge("FORBIDS", f"DietPattern:{r['diet_id']}", f"FoodClass:{r['forbids_class']}",
                        source=r["source"], status=r["status"])
        return kg

    # ---------------------------------------------------------------- helpers
    def ids(self, label: str) -> list[str]:
        return [nid for nid, n in self.nodes.items() if n["label"] == label]

    def name(self, nid: str) -> str:
        return self.nodes[nid].get("name", nid)

    def out(self, nid: str, etype: str) -> list[Edge]:
        return [e for e in self.out_edges[nid] if e.type == etype]

    def inc(self, nid: str, etype: str) -> list[Edge]:
        return [e for e in self.in_edges[nid] if e.type == etype]

    def resolve(self, label: str, text: str) -> str | None:
        """Map a user word (id, name or alias) to a node id of ``label``."""
        t = text.strip().lower().replace("-", " ").replace("_", " ")
        for nid in self.ids(label):
            n = self.nodes[nid]
            names = {n["key"].lower().replace("_", " "), str(n.get("name", "")).lower()}
            names |= {a.lower() for a in n.get("aliases", [])}
            if t in names:
                return nid
        return None

    def descendants(self, nid: str, max_depth: int | None = None) -> dict[str, list[str]]:
        """Nodes reachable by walking SUBCLASS_OF backwards (``*0..max_depth``), with paths."""
        paths = {nid: [nid]}
        frontier = [nid]
        depth = 0
        while frontier and (max_depth is None or depth < max_depth):
            nxt = []
            for cur in frontier:
                for e in self.inc(cur, "SUBCLASS_OF"):
                    if e.src not in paths:
                        paths[e.src] = paths[cur] + [e.src]
                        nxt.append(e.src)
            frontier, depth = nxt, depth + 1
        return paths

    def food_nutrients(self, food: str) -> dict[str, float]:
        return {e.dst.split(":", 1)[1]: e.props["amount_per_100g"] for e in self.out(food, "CONTAINS")}

    def food_class(self, food: str) -> str:
        return self.out(food, "IS_A")[0].dst

    # ------------------------------------------------- queries (Cypher mirrors)
    def q_allergen_foods(self, allergies: list[str], hierarchy: bool = True) -> list[dict]:
        """M2: (a)<-[:SUBCLASS_OF*0..]-(sub)<-[:HAS_ALLERGEN]-(f)."""
        rows = []
        for a in allergies:
            for sub, path in self.descendants(a, None if hierarchy else 0).items():
                for e in self.inc(sub, "HAS_ALLERGEN"):
                    rows.append({"food": e.src, "reason": a, "via": sub,
                                 "path": ["User", "HAS_ALLERGY", a]
                                 + _hops(path, "SUBCLASS_OF")
                                 + ["HAS_ALLERGEN(inv)", e.src],
                                 "source": "user allergy + allergen hierarchy"})
        return rows

    def _class_foods(self, cls: str, hierarchy: bool) -> list[tuple[str, list[str]]]:
        out = []
        for sub, path in self.descendants(cls, None if hierarchy else 0).items():
            for e in self.inc(sub, "IS_A"):
                out.append((e.src, path))
        return out

    def q_diet_forbidden_foods(self, diets: list[str], hierarchy: bool = True) -> list[dict]:
        """M3: (u)-[:FOLLOWS]->(p)-[:FORBIDS]->(k)<-[:SUBCLASS_OF*0..]-()<-[:IS_A]-(f)."""
        rows = []
        for p in diets:
            for e in self.out(p, "FORBIDS"):
                for food, path in self._class_foods(e.dst, hierarchy):
                    rows.append({"food": food, "reason": p, "via": e.dst,
                                 "path": ["User", "FOLLOWS", p, "FORBIDS"]
                                 + _hops(path, "SUBCLASS_OF") + ["IS_A(inv)", food],
                                 "source": e.props.get("source", "")})
        return rows

    def q_drug_avoid_foods(self, drugs: list[str], hierarchy: bool = True) -> list[dict]:
        """M4: (u)-[:TAKES]->(d)-[r:AVOID]->(k)<-[:SUBCLASS_OF*0..]-()<-[:IS_A]-(f)."""
        rows = []
        for d in drugs:
            for e in self.out(d, "AVOID"):
                for food, path in self._class_foods(e.dst, hierarchy):
                    rows.append({"food": food, "reason": d, "via": e.dst,
                                 "severity": e.props.get("severity", "high"),
                                 "path": ["User", "TAKES", d, "AVOID"]
                                 + _hops(path, "SUBCLASS_OF") + ["IS_A(inv)", food],
                                 "source": e.props.get("source", "")})
        return rows

    def q_condition_rules(self, conditions: list[str]) -> list[dict]:
        """M5-M7: (u)-[:HAS_CONDITION]->(c)-[r:LIMITS|REQUIRES]->(n)."""
        rows = []
        for c in conditions:
            for e in self.out_edges[c]:
                if e.type in ("LIMITS", "REQUIRES"):
                    rows.append({"rule_type": e.type, "owner": c, "nutrient": e.dst, **e.props,
                                 "path": ["User", "HAS_CONDITION", c, e.type, e.dst]})
        return rows

    def q_drug_stable(self, drugs: list[str]) -> list[dict]:
        """M8: (u)-[:TAKES]->(d)-[r:STABLE]->(n)."""
        return [{"owner": d, "nutrient": e.dst, **e.props,
                 "path": ["User", "TAKES", d, "STABLE", e.dst]}
                for d in drugs for e in self.out(d, "STABLE")]

    def q_dri(self, stage: str) -> list[dict]:
        """M9/M10: (u)-[:IN_GROUP]->(g)-[r:DRI]->(n)."""
        return [{"owner": stage, "nutrient": e.dst, **e.props,
                 "path": ["User", "IN_GROUP", stage, "DRI", e.dst]}
                for e in self.out(stage, "DRI")]

    def q_life_stage(self, sex: str, age: int) -> str | None:
        for nid in self.ids("LifeStage"):
            n = self.nodes[nid]
            if n["sex"] == sex.upper()[0] and n["age_min"] <= age <= n["age_max"]:
                return nid
        return None

    def q_meal_slots(self) -> dict[str, dict]:
        """M11: MealSlot shares + (f)-[:SUITABLE_FOR]->(m)."""
        return {m: {"share_min": self.nodes[m]["kcal_share_min"],
                    "share_max": self.nodes[m]["kcal_share_max"],
                    "foods": {e.src for e in self.inc(m, "SUITABLE_FOR")}}
                for m in self.ids("MealSlot")}

    def q_substitutes(self, food: str) -> list[dict]:
        """M12: (g)-[s:SUBSTITUTE_FOR]->(f)."""
        return sorted(({"food": e.src, "similarity": e.props["similarity"]}
                       for e in self.inc(food, "SUBSTITUTE_FOR")),
                      key=lambda r: -r["similarity"])

    def stats(self) -> dict:
        labels: dict[str, int] = defaultdict(int)
        etypes: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            labels[n["label"]] += 1
        for edges in self.out_edges.values():
            for e in edges:
                etypes[e.type] += 1
        return {"nodes": dict(labels), "edges": dict(etypes)}


def _stage_name(r: dict) -> str:
    who = "women" if r["sex"] == "F" else "men"
    top = "+" if int(r["age_max"]) >= 120 else f"-{r['age_max']}"
    return f"DRI group ({who} {r['age_min']}{top})"


def _aliases(row: dict) -> list[str]:
    return [a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()]


def _hops(chain: list[str], etype: str) -> list[str]:
    """Render a class chain top->down as alternating node / inverse edge labels."""
    out: list[str] = []
    for i, node in enumerate(chain):
        if i:
            out.append(f"{etype}(inv)")
        out.append(node)
    return out[1:] if len(out) > 1 else []
