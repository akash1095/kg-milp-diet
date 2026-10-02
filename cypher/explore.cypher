// Queries for exploring the diet KG in the Neo4j Browser (http://localhost:7481).
// Load the graph first with `kgdiet neo4j-load`, then drag cypher/style.grass onto the
// Browser for colours and captions. Paste one query at a time. Graph results show as
// pictures; table results show in the Table tab.

// 1. Schema: which node labels connect through which relationship types
CALL db.schema.visualization();

// 2. Counts per label and per relationship type
MATCH (n) WHERE NOT n:KGMeta RETURN labels(n)[0] AS label, count(*) AS nodes ORDER BY nodes DESC;
MATCH ()-[r]->() RETURN type(r) AS relationship, count(*) AS edges ORDER BY edges DESC;

// 3. Food-class tree, with the foods hanging off each class
MATCH p = (:FoodClass)-[:SUBCLASS_OF]->(:FoodClass) RETURN p;
MATCH p = (:Food)-[:IS_A]->(:FoodClass)-[:SUBCLASS_OF*0..]->(:FoodClass {id: 'food'}) RETURN p;

// 4. Allergen tree, and which foods declare each allergen
MATCH p = (:Allergen)-[:SUBCLASS_OF*0..]->(:Allergen) RETURN p;
MATCH p = (:Food)-[:HAS_ALLERGEN]->(:Allergen) RETURN p;

// 5. One food with everything around it (change the name)
MATCH (f:Food) WHERE f.name STARTS WITH 'Brown rice'
MATCH p = (f)-[:IS_A|HAS_ALLERGEN|SUITABLE_FOR|SUBSTITUTE_FOR]-() RETURN p;

// 6. A food's nutrient profile per 100 g, as a table
MATCH (f:Food {id: 'F01'})-[c:CONTAINS]->(n:Nutrient)
RETURN n.name AS nutrient, c.amount_per_100g AS per_100g, n.unit AS unit ORDER BY n.seq;

// 7. All clinical rules, with their source and quote
MATCH (c:Condition)-[r:LIMITS|REQUIRES|ADVISES]->(n:Nutrient)
RETURN c.name AS condition, type(r) AS rule, n.name AS nutrient,
       coalesce(r.max, r.min) AS value, r.unit AS unit, r.basis AS basis,
       r.only_if AS only_if, r.except_if AS except_if, r.status AS status,
       r.source AS source, r.quote AS quote
ORDER BY condition, rule;

// 8. Rules as a picture: conditions, drugs and diets with what they touch
MATCH p = (:Condition|Drug|DietPattern)-[:LIMITS|REQUIRES|ADVISES|STABLE|AVOID|FORBIDS]->() RETURN p;

// 9. Why foods are excluded for a tree-nut allergy (M2): the full path
MATCH p = (:Allergen {id: 'tree_nut'})<-[:SUBCLASS_OF*0..]-(:Allergen)<-[:HAS_ALLERGEN]-(:Food) RETURN p;

// 10. Jain diet: forbidden classes and every food they reach (M3)
MATCH p = (:DietPattern {id: 'jain'})-[:FORBIDS]->(:FoodClass)<-[:SUBCLASS_OF*0..]-(:FoodClass)<-[:IS_A]-(:Food)
RETURN p;

// 11. Phenelzine (MAOI) avoidance (M4) and warfarin vitamin K range (M8)
MATCH p = (:Drug {id: 'phenelzine'})-[:AVOID]->(:FoodClass)<-[:SUBCLASS_OF*0..]-(:FoodClass)<-[:IS_A]-(:Food)
RETURN p;
MATCH (:Drug {id: 'warfarin'})-[s:STABLE]->(n:Nutrient)<-[c:CONTAINS]-(f:Food)
WHERE c.amount_per_100g > 0
RETURN f.name AS food, c.amount_per_100g AS vitamin_k_per_100g, s.lo AS daily_lo, s.hi AS daily_hi
ORDER BY vitamin_k_per_100g DESC;

// 12. DRI goals and upper limits for one life stage (M9/M10)
MATCH (g:LifeStage {id: 'F_51_70'})-[r:DRI]->(n:Nutrient)
RETURN g.name AS group, n.name AS nutrient, r.rda AS rda, r.ul AS ul, n.unit AS unit, r.source AS source
ORDER BY n.seq;

// 13. Substitutes: which foods can stand in for which (M12)
MATCH p = (:Food)-[:SUBSTITUTE_FOR]->(:Food) RETURN p;

// 14. Data checks: foods with no meal slot, classes with no foods, rules not yet verified
MATCH (f:Food) WHERE NOT (f)-[:SUITABLE_FOR]->() RETURN 'food without meal slot' AS issue, f.name AS item
UNION
MATCH (k:FoodClass) WHERE NOT (k)<-[:SUBCLASS_OF*0..]-()<-[:IS_A]-(:Food) RETURN 'class with no foods' AS issue, k.name AS item
UNION
MATCH ()-[r]->() WHERE r.status IS NOT NULL AND r.status <> 'verified'
RETURN 'rule status ' + r.status AS issue, coalesce(r.rule_id, type(r)) AS item;

// 15. Top foods for one nutrient per 100 kcal (e.g. potassium)
MATCH (f:Food)-[k:CONTAINS]->(:Nutrient {id: 'potassium_mg'}), (f)-[e:CONTAINS]->(:Nutrient {id: 'energy_kcal'})
WHERE e.amount_per_100g > 0
RETURN f.name AS food, round(100 * k.amount_per_100g / e.amount_per_100g, 1) AS mg_per_100kcal
ORDER BY mg_per_100kcal DESC LIMIT 15;
