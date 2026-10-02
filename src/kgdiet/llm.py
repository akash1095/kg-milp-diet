"""Optional LLM layer: natural-language query -> JSON spec, and friendlier explanations.

Requires ``pip install anthropic`` and ANTHROPIC_API_KEY. Everything else in
the pipeline runs without it. The parser is told the KG vocabulary, and its
output still goes through ``UserSpec.resolve`` (unknown terms are rejected).
"""
from __future__ import annotations

import json
import os
import re

from .kg import BaseKG
from .spec import SPEC_SCHEMA

DEFAULT_MODEL = os.environ.get("KGDIET_LLM_MODEL", "claude-sonnet-5")


def _client():
    try:
        import anthropic  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pip install anthropic to use the LLM layer") from exc
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("set ANTHROPIC_API_KEY to use the LLM layer")
    return anthropic.Anthropic()


def vocabulary(kg: BaseKG) -> dict[str, list[str]]:
    def names(label: str) -> list[str]:
        return sorted(kg.nodes[n]["key"] for n in kg.ids(label))
    return {"conditions": names("Condition"), "allergies": names("Allergen"),
            "drugs": names("Drug"), "diets": names("DietPattern"), "nutrients": names("Nutrient")}


def parse_query(kg: BaseKG, query: str, profile_id: str = "adhoc", model: str = DEFAULT_MODEL) -> dict:
    prompt = (
        "Convert the user's diet request into a JSON object with exactly these fields:\n"
        f"{json.dumps(SPEC_SCHEMA, indent=1)}\n\n"
        f"Use only these KG terms (ids):\n{json.dumps(vocabulary(kg), indent=1)}\n\n"
        "Rules: omit fields the user did not state except the required ones; if age, sex, "
        "weight or calories are missing, use null. Do not add conditions the user did not "
        f"mention. Set id to {profile_id!r} and copy the request into 'query'. "
        "Return only the JSON object.\n\n"
        f"Request: {query}"
    )
    msg = _client().messages.create(model=model, max_tokens=800,
                                    messages=[{"role": "user", "content": prompt}])
    text = "".join(getattr(b, "text", "") for b in msg.content)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"LLM returned no JSON: {text[:200]}")
    spec = json.loads(match.group(0))
    return {k: v for k, v in spec.items() if v is not None}


def explain_with_llm(records: list[dict], model: str = DEFAULT_MODEL) -> str:
    prompt = (
        "Rewrite these diet-plan reasons as a short, friendly explanation for the user. "
        "Use ONLY the statements below; do not add medical facts, numbers or advice that are "
        "not in them. Keep rule ids in brackets so each sentence stays traceable.\n\n"
        + "\n".join(f"[{r['rule']}] {r['text']}" for r in records)
    )
    msg = _client().messages.create(model=model, max_tokens=600,
                                    messages=[{"role": "user", "content": prompt}])
    return "".join(getattr(b, "text", "") for b in msg.content)
