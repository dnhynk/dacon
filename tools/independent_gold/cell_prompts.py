"""Item-group prompts for a blind single-notice teacher check; diagnostic, never gold.

The teacher sees the same organizer-only source projection, law context and
system instructions as the multi-notice batch pilot, but is asked for one
predicate family at a time: only that family's rubric sections and decisions.
Nothing in the prompt says which notice is synthetic or which cell is targeted.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from typing import Any

try:
    from tools.independent_gold import claude_batch_pilot as pilot
    from tools.independent_gold import claude_full_record_annotator as base
except ModuleNotFoundError:  # Direct script invocation.
    import claude_batch_pilot as pilot  # type: ignore[no-redef]
    import claude_full_record_annotator as base  # type: ignore[no-redef]


MODE = "source_lean"
GROUPS: dict[str, tuple[str, ...]] = {
    "v1-8": tuple(f"v{number}" for number in range(1, 9)),
    "v9": ("v9",),
    "v10-18": tuple(f"v{number}" for number in range(10, 19)),
    "v19": ("v19",),
    "v20": ("v20",),
    "v21-23": ("v21", "v22", "v23"),
    "v24": ("v24",),
}
_ITEM_SECTION = "## 항목별 경계"
_OUTPUT_SECTION = "## 출력"
_ALL_DECISIONS_SENTENCE = "Return exactly 24 decisions per record"
_ITEM_HEADING_RE = re.compile(r"^### (v\d+)\b", re.M)


def group_of(item: str) -> str:
    for name, items in GROUPS.items():
        if item in items:
            return name
    raise ValueError(f"unknown item: {item}")


def group_rubric(rubric: str, items: Sequence[str]) -> str:
    """Common principles plus only the requested items' sections; the output section is dropped."""

    if rubric.count(_ITEM_SECTION) != 1 or rubric.count(_OUTPUT_SECTION) != 1:
        raise ValueError("rubric group projection is stale")
    common, rest = rubric.split(_ITEM_SECTION)
    item_text = rest.split(_OUTPUT_SECTION)[0]
    starts = [(match.group(1), match.start()) for match in _ITEM_HEADING_RE.finditer(item_text)]
    sections = {
        name: item_text[start:(starts[index + 1][1] if index + 1 < len(starts) else len(item_text))]
        for index, (name, start) in enumerate(starts)
    }
    missing = [item for item in items if item not in sections]
    if missing or len(sections) != 24:
        raise ValueError(f"rubric item sections are stale: {missing or len(sections)}")
    return common + _ITEM_SECTION + "\n\n" + "".join(sections[item] for item in items)


def group_system(rubric: str, items: Sequence[str]) -> str:
    system = pilot._system(group_rubric(rubric, items), MODE)
    if system.count(_ALL_DECISIONS_SENTENCE) != 1:
        raise ValueError("batch pilot system prompt changed")
    return system.replace(
        _ALL_DECISIONS_SENTENCE,
        f"Return exactly these {len(items)} decisions per record ({','.join(items)}) and no other item",
    )


def group_schema(items: Sequence[str], count: int = 1) -> dict[str, Any]:
    schema = copy.deepcopy(pilot.batch_schema(count))
    decisions = schema["properties"]["records"]["items"]["properties"]["annotation"]["properties"]["decisions"]
    decisions["required"] = list(items)
    decisions["properties"] = {item: decisions["properties"][item] for item in items}
    return schema


def group_prompt(projection: Mapping[str, Any], ids: Sequence[str], items: Sequence[str]) -> str:
    return f"Decide only these items: {','.join(items)}.\n" + pilot._prompt(projection, ids)


def prompt_argument(items: Sequence[str]) -> str:
    return (
        "Apply the system rubric to the complete organizer input supplied on stdin. "
        f"Return the requested structured decision for items {','.join(items)} only."
    )


def decisions_of(structured: Any, record_id: str, items: Sequence[str]) -> dict[str, Any]:
    """Raw per-item labels from one single-record response; raises on any shape drift."""

    entries = pilot._batch_entries(structured, [record_id])
    decisions = entries[0]["annotation"]["decisions"]
    if set(decisions) != set(items):
        raise ValueError("decision items differ from the requested group")
    labels = {item: decisions[item]["label"] for item in items}
    if any(label not in (0, 1, "U") for label in labels.values()):
        raise ValueError("label outside 0/1/U")
    return labels


def build_group_call(
    record: Mapping[str, Any], items: Sequence[str], *, rubric: str,
    catalog_index: Any = None, qualification_catalog: Any = None,
) -> dict[str, str]:
    """System, prompt, schema and CLI argument for one record and one item group."""

    kwargs = {}
    if catalog_index is not None:
        kwargs = {"catalog_index": catalog_index, "qualification_catalog": qualification_catalog}
    context = base.full_record_context.build_full_record_context(record, **kwargs)
    projection = pilot.batch_projection([context], MODE)
    return {
        "system": group_system(rubric, items),
        "prompt": group_prompt(projection, [record["id"]], items),
        "schema_json": base.canonical_json(group_schema(items)),
        "prompt_argument": prompt_argument(items),
        "context_sha256": context["context_sha256"],
    }
