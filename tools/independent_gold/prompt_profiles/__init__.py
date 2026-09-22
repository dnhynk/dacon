"""Frozen prompt profiles for independent first-pass annotation.

The candidate and verifier renderers intentionally live in separate modules.
They may consume the same normative rubric and source/context messages, but a
profile never accepts another annotator's identity, vote, confidence, rationale,
or evidence as an argument.
"""

from __future__ import annotations

import hashlib
import inspect
import pathlib
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from . import candidate_source_direct_v1
from . import verifier_source_falsification_v1


@dataclass(frozen=True)
class PromptProfile:
    """One immutable role-specific prompt renderer."""

    name: str
    annotator_role: str
    required_model_family: str
    protocol_version: str
    template: str
    render: Callable[..., str]
    module_path: pathlib.Path

    @property
    def template_sha256(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()

    @property
    def builder_source_sha256(self) -> str:
        return hashlib.sha256(self.module_path.read_bytes()).hexdigest()

    @property
    def renderer_signature(self) -> str:
        return str(inspect.signature(self.render))


PROFILES: dict[str, PromptProfile] = {
    candidate_source_direct_v1.PROFILE_NAME: PromptProfile(
        name=candidate_source_direct_v1.PROFILE_NAME,
        annotator_role=candidate_source_direct_v1.ANNOTATOR_ROLE,
        required_model_family=candidate_source_direct_v1.REQUIRED_MODEL_FAMILY,
        protocol_version=candidate_source_direct_v1.PROMPT_PROTOCOL_VERSION,
        template=candidate_source_direct_v1.PROMPT_TEMPLATE,
        render=candidate_source_direct_v1.build_prompt,
        module_path=pathlib.Path(candidate_source_direct_v1.__file__).resolve(),
    ),
    verifier_source_falsification_v1.PROFILE_NAME: PromptProfile(
        name=verifier_source_falsification_v1.PROFILE_NAME,
        annotator_role=verifier_source_falsification_v1.ANNOTATOR_ROLE,
        required_model_family=verifier_source_falsification_v1.REQUIRED_MODEL_FAMILY,
        protocol_version=verifier_source_falsification_v1.PROMPT_PROTOCOL_VERSION,
        template=verifier_source_falsification_v1.PROMPT_TEMPLATE,
        render=verifier_source_falsification_v1.build_prompt,
        module_path=pathlib.Path(verifier_source_falsification_v1.__file__).resolve(),
    ),
}

DEFAULT_PROFILE_BY_ROLE = {
    "candidate": candidate_source_direct_v1.PROFILE_NAME,
    "verifier": verifier_source_falsification_v1.PROFILE_NAME,
}


def get_profile(name: str, *, annotator_role: str) -> PromptProfile:
    """Return a profile only when its frozen role matches the requested role."""

    try:
        profile = PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown prompt profile: {name}") from exc
    if profile.annotator_role != annotator_role:
        raise ValueError(
            f"prompt profile {name!r} is for role {profile.annotator_role!r}, "
            f"not {annotator_role!r}"
        )
    return profile


def render_profile_prompt(
    profile: PromptProfile,
    messages: Sequence[Mapping[str, str]],
    *,
    record_id: str,
    group_name: str,
    target_items: Sequence[str],
) -> str:
    """Render from the admissible messages only.

    The narrow signature is deliberate: there is no channel for a peer vote or
    peer provenance to enter a blind first pass.
    """

    return profile.render(
        messages,
        record_id=record_id,
        group_name=group_name,
        target_items=target_items,
    )


__all__ = [
    "DEFAULT_PROFILE_BY_ROLE",
    "PROFILES",
    "PromptProfile",
    "get_profile",
    "render_profile_prompt",
]
