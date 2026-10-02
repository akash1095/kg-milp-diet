"""Neo4j backend for the diet KG, and the loader that fills Neo4j from the CSV tables.

The CSVs stay the source of truth: ``push()`` writes the in-memory ``KG`` into
Neo4j (``kgdiet neo4j-load``), and ``Neo4jKG`` answers the same ``q_*`` queries
from Neo4j using the Cypher in ``cypher/templates.cypher``. Rows match the
in-memory backend exactly, including order (via the ``seq`` load order), so the
compiler and solver give the same plans on either backend.

Connection settings come from arguments, then the environment, then the repo
``.env`` file: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from .kg import (BaseKG, Edge, KG, allergen_row, condition_row, diet_row, dri_row,
                 drug_avoid_row, drug_stable_row)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "cypher" / "templates.cypher"
DEFAULT_URI = "bolt://localhost:7694"
BATCH = 5000


class KGUnavailable(RuntimeError):
    """Neo4j cannot be reached, or holds no diet KG."""


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    path = ROOT / ".env"
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'")
    return out


def env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _dotenv().get(name) or default


@lru_cache(maxsize=1)
def queries() -> dict[str, str]:
    """Named query blocks from templates.cypher: ``// [name]`` then lines up to a blank line."""
    out: dict[str, str] = {}
    name, lines = None, []
    for line in TEMPLATES.read_text(encoding="utf-8").splitlines() + [""]:
        s = line.strip()
        m = re.match(r"//\s*\[(\w+)\]", s)
        if m:
            name, lines = m.group(1), []
        elif not s:
            if name and lines:
                out[name] = "\n".join(lines).rstrip().rstrip(";")
            name, lines = None, []
        elif name and not s.startswith("//"):
            lines.append(line)
    return out


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _query(name: str, **idents: str) -> str:
    """Template text with __LABEL__-style placeholders filled (identifiers only, never user text)."""
    q = queries()[name]
    for k, v in idents.items():
        if not _IDENT.match(v):
            raise ValueError(f"not a valid Cypher identifier: {v!r}")
        q = q.replace(f"__{k}__", v)
    return q


def _key(nid: str) -> str:
    return nid.split(":", 1)[1]


def connect(uri: str | None = None, user: str | None = None, password: str | None = None):
    from neo4j import GraphDatabase
    uri = uri or env("NEO4J_URI", DEFAULT_URI)
    user = user or env("NEO4J_USER", "neo4j")
    password = password or env("NEO4J_PASSWORD")
    if not password:
        raise KGUnavailable("NEO4J_PASSWORD is not set (see .env.example)")
    return GraphDatabase.driver(uri, auth=(user, password))


def _unavailable(uri: str, exc: Exception) -> KGUnavailable:
    return KGUnavailable(f"cannot use Neo4j at {uri}: {exc}\n"
                         "Start it with `docker compose up -d`, load it with `kgdiet neo4j-load`, "
                         "or run with `--backend memory`.")


class Neo4jKG(BaseKG):
    """KG backend that queries Neo4j. The node table is cached once; edges are queried."""

    def __init__(self, uri: str | None = None, user: str | None = None, password: str | None = None,
                 database: str | None = None, driver=None) -> None:
        from neo4j import RoutingControl
        from neo4j.exceptions import AuthError, ServiceUnavailable
        super().__init__()
        self.uri = uri or env("NEO4J_URI", DEFAULT_URI)
        self.database = database or env("NEO4J_DATABASE", "neo4j")
        self._read = RoutingControl.READ
        self.driver = driver or connect(self.uri, user, password)
        try:
            meta = self._run("meta")
        except (ServiceUnavailable, AuthError, OSError) as exc:
            self.driver.close()
            raise _unavailable(self.uri, exc) from exc
        if not meta:
            self.driver.close()
            raise _unavailable(self.uri, RuntimeError("database holds no diet KG"))
        schema = json.loads(meta[0]["schema"])
        self.node_keys: dict[str, list[str]] = schema["nodes"]
        self.edge_keys: dict[str, list[str]] = schema["edges"]
        self.source = meta[0]["source"]
        for r in self._run("nodes"):
            p = dict(r["props"])
            key = p.pop("id")
            p.pop("seq", None)
            self.nodes[f"{r['label']}:{key}"] = {"label": r["label"], "key": key,
                                                 **_with_nulls(p, self.node_keys.get(r["label"], []))}
        self._nutrients: dict[str, dict[str, float]] | None = None

    def close(self) -> None:
        self.driver.close()

    def __enter__(self) -> "Neo4jKG":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _run(self, name: str, query: str | None = None, **params) -> list:
        return self.driver.execute_query(query or queries()[name], params, database_=self.database,
                                         routing_=self._read).records

    def _eprops(self, etype: str, props: dict) -> dict:
        p = dict(props)
        p.pop("seq", None)
        return _with_nulls(p, self.edge_keys.get(etype, []))

    # ---------------------------------------------------------------- helpers
    def out(self, nid: str, etype: str) -> list[Edge]:
        label, key = nid.split(":", 1)
        rows = self._run("out", _query("out", LABEL=label, TYPE=etype), key=key)
        return [Edge(etype, nid, f"{r['label']}:{r['key']}", self._eprops(etype, r["props"]),
                     r["props"].get("seq", 0)) for r in rows]

    def food_nutrients(self, food: str) -> dict[str, float]:
        if self._nutrients is None:  # one query for all foods; the model asks food by food
            cache: dict[str, dict[str, float]] = defaultdict(dict)
            for r in self._run("food_nutrients"):
                cache[f"Food:{r['food']}"][r["nutrient"]] = r["amount"]
            self._nutrients = dict(cache)
        return dict(self._nutrients.get(food, {}))

    # ------------------------------------------------- queries (Cypher templates)
    def _class_path_rows(self, name: str, owners: list[str], hierarchy: bool,
                         chain_label: str) -> list[tuple[str, dict, list[str], str]]:
        """Rows of a ``(owner)-...->(k)<-[:SUBCLASS_OF*0..]-(sub)<-...-(food)`` template.

        Keeps one path per (owner, rule, sub): the shortest, then the earliest
        loaded, which is the path the in-memory breadth-first walk finds. Returns
        (owner, row, chain, food) in the in-memory order.
        """
        rank = {o: i for i, o in enumerate(owners)}
        prefix = owners[0].split(":", 1)[0] if owners else ""
        rows = self._run(name, owners=[_key(o) for o in owners], hierarchy=hierarchy)
        best: dict[tuple, tuple] = {}
        for r in rows:
            k = (r["owner"], r["rule_seq"], r["chain"][-1])
            pr = (len(r["chain"]), r["chain_seq"])
            if k not in best or pr < best[k]:
                best[k] = pr
        kept = [r for r in rows
                if (len(r["chain"]), r["chain_seq"]) == best[(r["owner"], r["rule_seq"], r["chain"][-1])]]
        kept.sort(key=lambda r: (rank[f"{prefix}:{r['owner']}"], r["rule_seq"], len(r["chain"]),
                                 r["chain_seq"], r["seq"]))
        return [(f"{prefix}:{r['owner']}", r, [f"{chain_label}:{c}" for c in r["chain"]], f"Food:{r['food']}")
                for r in kept]

    def q_allergen_foods(self, allergies: list[str], hierarchy: bool = True) -> list[dict]:
        return [allergen_row(a, chain[-1], chain, food)
                for a, _, chain, food in self._class_path_rows("q_allergen_foods", allergies, hierarchy,
                                                               "Allergen")]

    def q_diet_forbidden_foods(self, diets: list[str], hierarchy: bool = True) -> list[dict]:
        return [diet_row(p, f"FoodClass:{r['cls']}", self._eprops("FORBIDS", r["props"]), chain, food)
                for p, r, chain, food in self._class_path_rows("q_diet_forbidden_foods", diets, hierarchy,
                                                               "FoodClass")]

    def q_drug_avoid_foods(self, drugs: list[str], hierarchy: bool = True) -> list[dict]:
        return [drug_avoid_row(d, f"FoodClass:{r['cls']}", self._eprops("AVOID", r["props"]), chain, food)
                for d, r, chain, food in self._class_path_rows("q_drug_avoid_foods", drugs, hierarchy,
                                                               "FoodClass")]

    def _owner_rows(self, name: str, owners: list[str]) -> list:
        rank = {_key(o): i for i, o in enumerate(owners)}
        rows = self._run(name, owners=list(rank))
        return sorted(rows, key=lambda r: (rank[r["owner"]], r["seq"]))

    def q_condition_rules(self, conditions: list[str], context: bool = True) -> list[dict]:
        return [condition_row(f"Condition:{r['owner']}", r["rule_type"], f"Nutrient:{r['nutrient']}",
                              self._eprops(r["rule_type"], r["props"]), conditions, context)
                for r in self._owner_rows("q_condition_rules", conditions)]

    def q_drug_stable(self, drugs: list[str]) -> list[dict]:
        return [drug_stable_row(f"Drug:{r['owner']}", f"Nutrient:{r['nutrient']}",
                                self._eprops("STABLE", r["props"]))
                for r in self._owner_rows("q_drug_stable", drugs)]

    def q_dri(self, stage: str) -> list[dict]:
        return [dri_row(stage, f"Nutrient:{r['nutrient']}", self._eprops("DRI", r["props"]))
                for r in self._run("q_dri", stage=_key(stage))]

    def q_meal_slots(self) -> dict[str, dict]:
        return {f"MealSlot:{r['slot']}": {"share_min": r["share_min"], "share_max": r["share_max"],
                                          "foods": {f"Food:{f}" for f in r["foods"]}}
                for r in self._run("q_meal_slots")}

    def q_substitutes(self, food: str) -> list[dict]:
        return [{"food": f"Food:{r['food']}", "similarity": r["similarity"]}
                for r in self._run("q_substitutes", food=_key(food))]

    def stats(self) -> dict:
        return {"nodes": {r["label"]: r["n"] for r in self._run("stats_nodes")},
                "edges": {r["type"]: r["n"] for r in self._run("stats_edges")}}


def _with_nulls(props: dict, keys: list[str]) -> dict:
    """Neo4j drops null properties; put them back so rows equal the in-memory ones."""
    out = {k: props.get(k) for k in keys}
    out.update((k, v) for k, v in props.items() if k not in out)
    return out


def push(kg: KG, driver=None, database: str | None = None, source: str = "") -> dict:
    """Replace the Neo4j graph with ``kg``. Returns Neo4j's node and edge counts."""
    own = driver is None
    driver = driver or connect()
    database = database or env("NEO4J_DATABASE", "neo4j")

    def run(q: str, **params):
        return driver.execute_query(q, params, database_=database).records

    try:
        run(_query("wipe"))
        node_keys: dict[str, list[str]] = {}
        nodes: dict[str, list[dict]] = defaultdict(list)
        for seq, n in enumerate(kg.nodes.values(), 1):
            props = {k: v for k, v in n.items() if k not in ("label", "key")}
            keys = node_keys.setdefault(n["label"], [])
            keys += [k for k in props if k not in keys]
            nodes[n["label"]].append({"id": n["key"], "seq": seq,
                                      **{k: v for k, v in props.items() if v is not None}})
        for label, rows in nodes.items():
            run(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE")
            for i in range(0, len(rows), BATCH):
                run(_query("load_nodes", LABEL=label), rows=rows[i:i + BATCH])

        edge_keys: dict[str, list[str]] = {}
        edges: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for out_edges in kg.out_edges.values():
            for e in out_edges:
                keys = edge_keys.setdefault(e.type, [])
                keys += [k for k in e.props if k not in keys]
                (sl, sk), (dl, dk) = e.src.split(":", 1), e.dst.split(":", 1)
                edges[(e.type, sl, dl)].append(
                    {"s": sk, "d": dk, "p": {"seq": e.seq, **{k: v for k, v in e.props.items() if v is not None}}})
        for (etype, sl, dl), rows in edges.items():
            q = _query("load_edges", TYPE=etype, SRC=sl, DST=dl)
            for i in range(0, len(rows), BATCH):
                run(q, rows=rows[i:i + BATCH])

        run(_query("load_meta"), schema=json.dumps({"nodes": node_keys, "edges": edge_keys}), source=source)
        counts = {"nodes": {r["label"]: r["n"] for r in run(_query("stats_nodes"))},
                  "edges": {r["type"]: r["n"] for r in run(_query("stats_edges"))}}
    finally:
        if own:
            driver.close()
    if counts != kg.stats():
        raise RuntimeError(f"Neo4j counts {counts} differ from the KG {kg.stats()}")
    return counts
