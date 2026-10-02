"""Command line: ``kgdiet <command>`` (or ``python -m kgdiet <command>``)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compiler import CompileOptions, compile_spec
from .explain import explain_records, format_plan
from .kg import KG
from .model import plan
from .spec import SpecError, UserSpec
from .verify import Gold

ROOT = Path(__file__).resolve().parents[2]


def _options(args) -> CompileOptions:
    return CompileOptions(hierarchy=not args.no_hierarchy, clinical_rules=not args.no_clinical,
                          drug_rules=not args.no_clinical, meals=not args.no_meals)


def cmd_solve(args) -> int:
    kg = KG.load(args.data)
    raw = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    if args.cost:
        raw["objective"] = "cost"
    try:
        spec = UserSpec.from_dict(raw).resolve(kg)
    except SpecError as exc:
        print(f"Invalid spec: {exc}", file=sys.stderr)
        return 2
    cm = compile_spec(kg, spec, _options(args))
    p = plan(kg, cm, time_limit=args.time_limit, solver=args.solver)
    ev = Gold(args.data).evaluate(spec, p.servings, p.intake)
    if args.json:
        print(json.dumps({
            "profile": spec.id, "status": p.status, "message": p.message,
            "servings": {kg.name(f): s for f, s in p.servings.items()},
            "intake": p.intake, "cost": round(p.cost, 2), "solve_ms": p.solve_ms,
            "explanations": explain_records(kg, cm, p),
            "provenance": cm.provenance_registry(),
            "relaxations": p.relaxations, "conflicts": p.conflicts,
            "check": {"semantic_violations": ev.semantic_violations, "ul_violations": ev.ul_violations,
                      "energy_ok": ev.energy_ok, "dri_shortfall": ev.shortfall},
        }, indent=2, ensure_ascii=False))
        return 0
    print(format_plan(kg, cm, p))
    print("\nIndependent check: "
          + ("no semantic violations" if ev.semantic_ok else f"{len(ev.semantic_violations)} semantic violation(s)")
          + f"; DRI shortfall {ev.shortfall:.1%}")
    for v in ev.semantic_violations:
        print(f"  ! {v}")
    if args.llm_explain:
        from .llm import explain_with_llm
        print("\n" + explain_with_llm(explain_records(kg, cm, p)))
    return 0


def cmd_parse(args) -> int:
    from .llm import parse_query
    kg = KG.load(args.data)
    spec = parse_query(kg, args.query, profile_id=args.id)
    try:
        UserSpec.from_dict(spec).resolve(kg)
        note = "valid"
    except SpecError as exc:
        note = f"INVALID: {exc}"
    text = json.dumps(spec, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nSpec check: {note}", file=sys.stderr)
    return 0


def cmd_experiment(args) -> int:
    from .experiment import run
    rows = run(args.profiles, args.out, systems=args.systems, data_dir=args.data, time_limit=args.time_limit,
               jobs=args.jobs)
    print((Path(args.out) / "summary.md").read_text(encoding="utf-8"))
    print(f"{len(rows)} runs written to {Path(args.out) / 'runs.csv'}")
    return 0


def cmd_stats(args) -> int:
    print(json.dumps(KG.load(args.data).stats(), indent=2))
    return 0


def cmd_export(args) -> int:
    from .cypher_export import export
    print(f"Wrote {export(KG.load(args.data), args.out)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:  # Windows consoles default to cp1252; plan text uses ≤, ≥ and µ
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="kgdiet", description="KG-to-MILP diet planner prototype")
    ap.add_argument("--data", default=str(ROOT / "data"), help="folder with the KG CSV tables")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("solve", help="plan one profile and explain it")
    s.add_argument("profile")
    s.add_argument("--json", action="store_true")
    s.add_argument("--cost", action="store_true", help="minimize cost instead of nutrition only")
    s.add_argument("--no-hierarchy", action="store_true")
    s.add_argument("--no-clinical", action="store_true", help="drop condition and drug rules")
    s.add_argument("--no-meals", action="store_true", help="one daily pool instead of meal slots")
    s.add_argument("--solver", choices=["auto", "cbc", "highs"], default="auto")
    s.add_argument("--time-limit", type=int, default=30)
    s.add_argument("--llm-explain", action="store_true", help="rewrite reasons with an LLM (needs API key)")
    s.set_defaults(func=cmd_solve)

    p = sub.add_parser("parse", help="LLM: natural-language request -> JSON spec (needs API key)")
    p.add_argument("query")
    p.add_argument("--id", default="adhoc")
    p.add_argument("--out")
    p.set_defaults(func=cmd_parse)

    e = sub.add_parser("experiment", help="run the ablation over all profiles")
    e.add_argument("--profiles", default=str(ROOT / "profiles"))
    e.add_argument("--out", default=str(ROOT / "results"))
    e.add_argument("--systems", nargs="*")
    e.add_argument("--time-limit", type=int, default=30)
    e.add_argument("--jobs", type=int, default=1, help="parallel worker processes")
    e.set_defaults(func=cmd_experiment)

    st = sub.add_parser("stats", help="KG node and edge counts")
    st.set_defaults(func=cmd_stats)

    x = sub.add_parser("export-cypher", help="write a Neo4j load script")
    x.add_argument("--out", default=str(ROOT / "cypher" / "load.cypher"))
    x.set_defaults(func=cmd_export)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
