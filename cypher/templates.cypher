// Parameterized Cypher templates, one per mapping rule.
// src/kgdiet/kg.py implements the same queries in memory (method name in brackets),
// so the compiler runs without Neo4j. Load the graph with: kgdiet export-cypher.
// Parameters come from the validated JSON spec, never from free LLM text.

// M2 allergen exclusion, including allergen subclasses          [q_allergen_foods]
MATCH (a:Allergen) WHERE a.id IN $allergies
MATCH (a)<-[:SUBCLASS_OF*0..]-(sub:Allergen)<-[h:HAS_ALLERGEN]-(f:Food)
RETURN DISTINCT f.id AS food, a.id AS reason, sub.id AS via, h.confidence AS confidence;

// M3 diet category exclusion                                   [q_diet_forbidden_foods]
MATCH (p:DietPattern) WHERE p.id IN $diets
MATCH (p)-[r:FORBIDS]->(k:FoodClass)<-[:SUBCLASS_OF*0..]-(:FoodClass)<-[:IS_A]-(f:Food)
RETURN DISTINCT f.id AS food, p.id AS reason, k.id AS via, r.source AS source;

// M4 drug-food avoidance (severity high = hard, else soft)     [q_drug_avoid_foods]
MATCH (d:Drug) WHERE d.id IN $drugs
MATCH (d)-[r:AVOID]->(k:FoodClass)<-[:SUBCLASS_OF*0..]-(:FoodClass)<-[:IS_A]-(f:Food)
RETURN DISTINCT f.id AS food, d.id AS reason, k.id AS via, r.severity AS severity, r.source AS source;

// M5-M7 condition rules (per_day, per_kg, pct_kcal, per_1000kcal) [q_condition_rules]
MATCH (c:Condition)-[r:LIMITS|REQUIRES]->(n:Nutrient) WHERE c.id IN $conditions
RETURN c.id AS owner, type(r) AS rule_type, n.id AS nutrient, n.unit AS nutrient_unit,
       r.max AS max, r.min AS min, r.unit AS unit, r.basis AS basis,
       r.source AS source, r.section AS section, r.url AS url, r.status AS status;

// M8 drug nutrient range                                        [q_drug_stable]
MATCH (d:Drug)-[r:STABLE]->(n:Nutrient) WHERE d.id IN $drugs
RETURN d.id AS owner, n.id AS nutrient, r.lo AS lo, r.hi AS hi, r.unit AS unit, r.source AS source;

// M9/M10 DRI goals and upper limits                             [q_life_stage + q_dri]
MATCH (g:LifeStage {sex: $sex}) WHERE g.age_min <= $age <= g.age_max
MATCH (g)-[r:DRI]->(n:Nutrient)
RETURN g.id AS owner, n.id AS nutrient, r.rda AS rda, r.ul AS ul, r.source AS source;

// M11 meal slots and suitability                                [q_meal_slots]
MATCH (m:MealSlot) OPTIONAL MATCH (f:Food)-[:SUITABLE_FOR]->(m)
RETURN m.id AS slot, m.kcal_share_min AS share_min, m.kcal_share_max AS share_max, collect(f.id) AS foods;

// M12 substitutes for an excluded preferred food                [q_substitutes]
MATCH (g:Food)-[s:SUBSTITUTE_FOR]->(f:Food {id: $food})
RETURN g.id AS food, s.similarity AS similarity ORDER BY similarity DESC;

// Coefficients: nutrient amounts per 100 g for the allowed foods
MATCH (f:Food)-[c:CONTAINS]->(n:Nutrient) WHERE NOT f.id IN $excluded
RETURN f.id AS food, f.serving_g AS serving_g, n.id AS nutrient, c.amount_per_100g AS amount;
