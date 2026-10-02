| System | SVR (feasible) | SVR T1 | SVR T2 | SVR T3 | Rule F1 | Detection | p vs ours (SVR) | DRI shortfall | Foods | Solve ms | No plan |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| llmn_like | 59% (51/86) | 0% (0/10) | 50% (22/44) | 91% (29/32) | 0.202 | 95% (87/92) | 8.9e-16 | 0.0% | 7.9 | 1357 | 0 |
| flat_table | 27% (23/86) | 0% (0/10) | 25% (11/44) | 38% (12/32) | 0.646 | 89% (82/92) | 2.4e-07 | 1.3% | 8.1 | 1707 | 0 |
| kg_no_hierarchy | 15% (13/86) | 0% (0/10) | 25% (11/44) | 6% (2/32) | 0.720 | 100% (92/92) | 0.00024 | 1.1% | 8.2 | 2020 | 0 |
| kg_no_context | 12% (10/86) | 0% (0/10) | 0% (0/44) | 31% (10/32) | 0.924 | 89% (82/92) | 0.002 | 1.3% | 8.1 | 1890 | 0 |
| kg_full | 0% (0/86) | 0% (0/10) | 0% (0/44) | 0% (0/32) | 1.000 | 100% (92/92) | — | 1.1% | 8.2 | 2073 | 0 |

SVR = share of rule-feasible profiles whose plan breaks at least one gold semantic rule. Rule F1 = match between the semantic constraints a system applied and the gold set. Detection = 'flagged infeasible' matches the label. p = exact McNemar test on per-profile SVR against kg_full. No plan = solver found no plan within the time limit; those profiles are excluded from SVR and listed separately.
