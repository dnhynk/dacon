"""Frozen, role-separated prompt registry for full-record annotation."""

from __future__ import annotations

import hashlib
import inspect
import pathlib
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

try:
    from tools.independent_gold.prompt_profiles import (
        candidate_full_record_source_direct_v2 as candidate_profile,
    )
    from tools.independent_gold.prompt_profiles import (
        verifier_full_record_falsification_v2 as verifier_profile,
    )
except ModuleNotFoundError:  # Direct execution from this directory.
    from prompt_profiles import (  # type: ignore[no-redef]
        candidate_full_record_source_direct_v2 as candidate_profile,
    )
    from prompt_profiles import (  # type: ignore[no-redef]
        verifier_full_record_falsification_v2 as verifier_profile,
    )


@dataclass(frozen=True)
class PromptProfile:
    name: str
    annotator_role: str
    required_model_family: str
    required_model: str
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


PROFILES = {
    candidate_profile.PROFILE_NAME: PromptProfile(
        name=candidate_profile.PROFILE_NAME,
        annotator_role=candidate_profile.ANNOTATOR_ROLE,
        required_model_family=candidate_profile.REQUIRED_MODEL_FAMILY,
        required_model="gpt-5.6-sol",
        protocol_version=candidate_profile.PROMPT_PROTOCOL_VERSION,
        template=candidate_profile.PROMPT_TEMPLATE,
        render=candidate_profile.build_prompt,
        module_path=pathlib.Path(candidate_profile.__file__).resolve(),
    ),
    verifier_profile.PROFILE_NAME: PromptProfile(
        name=verifier_profile.PROFILE_NAME,
        annotator_role=verifier_profile.ANNOTATOR_ROLE,
        required_model_family=verifier_profile.REQUIRED_MODEL_FAMILY,
        required_model="gpt-6-astra",
        protocol_version=verifier_profile.PROMPT_PROTOCOL_VERSION,
        template=verifier_profile.PROMPT_TEMPLATE,
        render=verifier_profile.build_prompt,
        module_path=pathlib.Path(verifier_profile.__file__).resolve(),
    ),
}

DEFAULT_PROFILE_BY_ROLE = {
    "candidate": candidate_profile.PROFILE_NAME,
    "verifier": verifier_profile.PROFILE_NAME,
}


def get_profile(name: str, *, annotator_role: str) -> PromptProfile:
    try:
        profile = PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown full-record prompt profile: {name}") from exc
    if profile.annotator_role != annotator_role:
        raise ValueError(
            f"prompt profile {name!r} is for {profile.annotator_role!r}, "
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
