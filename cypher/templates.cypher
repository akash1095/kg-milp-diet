// Parameterized Cypher queries used by the Neo4j backend (src/kgdiet/neo4j_kg.py).
// Each block starts with a comment holding its name in brackets; the backend reads
// the query lines that follow, up to the next blank line. src/kgdiet/kg.py implements
// the q_* queries in memory, and tests/test_neo4j_backend.py checks both give the same rows.
// Ids are node keys without the label prefix ("tree_nut", not "Allergen:tree_nut").
// Parameters come from the validated JSON spec, never from free LLM text.
// `seq` is the load order: it makes rows come back in the same order as in memory.
// Load the graph with: kgdiet neo4j-load

// [q_allergen_foods] M2 allergen exclusion, including allergen subclasses
UNWIND $owners AS oid
MATCH p = (:Allergen {id: oid})<-[:SUBCLASS_OF*0..]-(sub:Allergen)
WHERE $hierarchy OR length(p) = 0
MATCH (sub)<-[h:HAS_ALLERGEN]-(f:Food)
RETURN oid AS owner, 0 AS rule_seq, null AS cls, {} AS props,
       [n IN nodes(p) | n.id] AS chain, [r IN relationships(p) | r.seq] AS chain_seq,
       f.id AS food, h.seq AS seq;

// [q_diet_forbidden_foods] M3 diet category exclusion through the class hierarchy
UNWIND $owners AS oid
MATCH (:DietPattern {id: oid})-[r:FORBIDS]->(k:FoodClass)
MATCH p = (k)<-[:SUBCLASS_OF*0..]-(sub:FoodClass)
WHERE $hierarchy OR length(p) = 0
MATCH (sub)<-[i:IS_A]-(f:Food)
RETURN oid AS owner, r.seq AS rule_seq, k.id AS cls, properties(r) AS props,
       [n IN nodes(p) | n.id] AS chain, [x IN relationships(p) | x.seq] AS chain_seq,
       f.id AS food, i.seq AS seq;

// [q_drug_avoid_foods] M4 drug-food avoidance (severity high = hard, else soft)
UNWIND $owners AS oid
MATCH (:Drug {id: oid})-[r:AVOID]->(k:FoodClass)
MATCH p = (k)<-[:SUBCLASS_OF*0..]-(sub:FoodClass)
WHERE $hierarchy OR length(p) = 0
MATCH (sub)<-[i:IS_A]-(f:Food)
RETURN oid AS owner, r.seq AS rule_seq, k.id AS cls, properties(r) AS props,
       [n IN nodes(p) | n.id] AS chain, [x IN relationships(p) | x.seq] AS chain_seq,
       f.id AS food, i.seq AS seq;

// [q_condition_rules] M5-M7 condition rules (only_if / except_if are checked in Python)
UNWIND $owners AS oid
MATCH (:Condition {id: oid})-[r:LIMITS|REQUIRES|ADVISES]->(n:Nutrient)
RETURN oid AS owner, type(r) AS rule_type, n.id AS nutrient, properties(r) AS props, r.seq AS seq;

// [q_drug_stable] M8 drug nutrient range
UNWIND $owners AS oid
MATCH (:Drug {id: oid})-[r:STABLE]->(n:Nutrient)
RETURN oid AS owner, n.id AS nutrient, properties(r) AS props, r.seq AS seq;

// [q_dri] M9/M10 DRI goals and upper limits for one life stage
MATCH (:LifeStage {id: $stage})-[r:DRI]->(n:Nutrient)
RETURN n.id AS nutrient, properties(r) AS props
ORDER BY r.seq;

// [q_meal_slots] M11 meal slots and suitability
MATCH (m:MealSlot)
OPTIONAL MATCH (f:Food)-[:SUITABLE_FOR]->(m)
RETURN m.id AS slot, m.kcal_share_min AS share_min, m.kcal_share_max AS share_max,
       collect(f.id) AS foods, m.seq AS seq
ORDER BY seq;

// [q_substitutes] M12 substitutes for an excluded preferred food
MATCH (g:Food)-[s:SUBSTITUTE_FOR]->(:Food {id: $food})
RETURN g.id AS food, s.similarity AS similarity
ORDER BY similarity DESC, s.seq;

// [food_nutrients] coefficients: nutrient amounts per 100 g for every food
MATCH (f:Food)-[c:CONTAINS]->(n:Nutrient)
RETURN f.id AS food, n.id AS nutrient, c.amount_per_100g AS amount
ORDER BY c.seq;

// [nodes] node table (cached once per process)
MATCH (n) WHERE NOT n:KGMeta
RETURN labels(n)[0] AS label, properties(n) AS props
ORDER BY n.seq;

// [out] edges of one type leaving one node (__LABEL__ and __TYPE__ come from the KG schema)
MATCH (:__LABEL__ {id: $key})-[r:__TYPE__]->(d)
RETURN labels(d)[0] AS label, d.id AS key, properties(r) AS props
ORDER BY r.seq;

// [stats_nodes]
MATCH (n) WHERE NOT n:KGMeta
RETURN labels(n)[0] AS label, count(*) AS n;

// [stats_edges]
MATCH ()-[r]->()
RETURN type(r) AS type, count(*) AS n;

// [meta] property schema written by the loader (Neo4j drops null properties)
MATCH (m:KGMeta {id: 'schema'})
RETURN m.schema AS schema, m.source AS source, m.loaded_at AS loaded_at;

// [wipe]
MATCH (n) DETACH DELETE n;

// [load_nodes]
UNWIND $rows AS row
CREATE (n:__LABEL__) SET n = row;

// [load_edges]
UNWIND $rows AS row
MATCH (a:__SRC__ {id: row.s}), (b:__DST__ {id: row.d})
CREATE (a)-[r:__TYPE__]->(b) SET r = row.p;

// [load_meta]
CREATE (:KGMeta {id: 'schema', schema: $schema, source: $source, loaded_at: datetime()});
