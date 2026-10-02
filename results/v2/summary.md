| System | SVR (feasible) | SVR T1 | SVR T2 | SVR T3 | Rule F1 | Detection | p vs ours (SVR) | DRI shortfall | Foods | Solve ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| llmn_like | 67% (58/86) | 0% (0/10) | 66% (29/44) | 91% (29/32) | 0.202 | 95% (87/92) | 1.4e-17 | 0.0% | 7.6 | 1196 |
| flat_table | 35% (30/86) | 0% (0/10) | 41% (18/44) | 38% (12/32) | 0.646 | 89% (82/92) | 3.7e-09 | 1.9% | 8.0 | 2364 |
| kg_no_hierarchy | 24% (21/86) | 0% (0/10) | 41% (18/44) | 9% (3/32) | 0.720 | 99% (91/92) | 1.9e-06 | 1.7% | 8.2 | 2401 |
| kg_no_context | 12% (10/86) | 0% (0/10) | 0% (0/44) | 31% (10/32) | 0.924 | 89% (82/92) | 0.0039 | 1.9% | 8.0 | 2284 |
| kg_full | 1% (1/86) | 0% (0/10) | 0% (0/44) | 3% (1/32) | 1.000 | 99% (91/92) | — | 1.7% | 8.2 | 2369 |

SVR = share of rule-feasible profiles whose plan breaks at least one gold semantic rule. Rule F1 = match between the semantic constraints a system applied and the gold set. Detection = 'flagged infeasible' matches the label. p = exact McNemar test on per-profile SVR against kg_full.
