| System | SVR (feasible) | SVR T1 | SVR T2 | SVR T3 | Rule F1 | Detection | p vs ours (SVR) | DRI shortfall | Foods | Solve ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| llmn_like | 50% (28/56) | 0% (0/10) | 35% (9/26) | 95% (19/20) | 0.300 | 92% (57/62) | 7.5e-09 | 0.0% | 7.8 | 4659 |
| flat_table | 14% (8/56) | 0% (0/10) | 8% (2/26) | 30% (6/20) | 0.765 | 94% (58/62) | 0.0078 | 1.5% | 8.0 | 4736 |
| kg_no_hierarchy | 7% (4/56) | 0% (0/10) | 8% (2/26) | 10% (2/20) | 0.835 | 100% (62/62) | 0.12 | 1.3% | 8.1 | 4658 |
| kg_no_context | 7% (4/56) | 0% (0/10) | 0% (0/26) | 20% (4/20) | 0.928 | 94% (58/62) | 0.12 | 1.4% | 8.1 | 4933 |
| kg_full | 0% (0/56) | 0% (0/10) | 0% (0/26) | 0% (0/20) | 1.000 | 100% (62/62) | — | 1.2% | 8.1 | 4945 |

SVR = share of rule-feasible profiles whose plan breaks at least one gold semantic rule. Rule F1 = match between the semantic constraints a system applied and the gold set. Detection = 'flagged infeasible' matches the label. p = exact McNemar test on per-profile SVR against kg_full.
