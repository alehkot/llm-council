"""Read-only skill prompt previews from archived bytes, never mutable paths.

This does not select references, persist a routing decision or final prompt,
authorize dispatch, or execute scripts. Cancelled runs remain inspectable.
"""

from __future__ import annotations

from typing import Any, Sequence

from . import run_skill_provenance, run_skill_snapshot, skills


class SkillRenderingConflict(ValueError):
    """Archived content or caller selections cannot be rendered unambiguously."""


def _reference(raw: dict[str, Any]) -> skills.SkillReference:
    path = raw["path"]
    if (
        skills._normalize_relpath(path) != path
        or not skills._is_reference_path(path)
        or ".." in path.split("/")
        or "\\" in path
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
    ):
        raise ValueError("Unsupported archived reference path")
    if (
        raw["chars"] != len(raw["text"])
        or raw["headings"] != skills._extract_headings(raw["text"])
        or raw["summary"] != skills._extract_summary(raw["text"])
    ):
        raise ValueError("Archived reference metadata does not match its text")
    sections = [skills.ReferenceSection(**section) for section in raw["sections"]]
    reference = skills.SkillReference(
        path=path,
        chars=raw["chars"],
        headings=list(raw["headings"]),
        mentions=list(raw["mentions"]),
        summary=raw["summary"],
        sections=[],
    )
    # Version-1 captures preserve the legacy synthetic whole-file section, not
    # the original empty manifest list. Recognize only that exact known shape.
    if sections == skills.reference_sections(reference):
        return reference
    expected = [
        skills.ReferenceSection(
            str(index), heading, len(chunk), skills._extract_summary(chunk)
        )
        for index, (heading, chunk) in enumerate(
            skills._split_reference_sections(raw["text"]), 1
        )
    ]
    if sections != expected:
        raise ValueError("Archived reference sections do not match their text")
    return skills.SkillReference(
        path=path,
        chars=raw["chars"],
        headings=list(raw["headings"]),
        mentions=list(raw["mentions"]),
        summary=raw["summary"],
        sections=sections,
    )


def render(
    run_id: str,
    selected_sections: dict[str, list[skills.SectionSelection]],
    *,
    max_chars: int,
    reasons: dict[str, dict[str, str]] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Render exact archived input with explicit selections, without any writes.

    Unknown skills/references/sections and noncanonical arguments fail rather
    than silently broadening/dropping a requested reference. Empty section IDs
    explicitly mean the whole file. Legacy synthetic section 1 also means the
    whole file, matching the existing renderer. The result is only a preview;
    later execution needs retained routing/settings/provenance and run guards.
    """
    if type(max_chars) is not int or max_chars < 0:
        raise ValueError("Character budget must be a nonnegative integer")
    captured = run_skill_snapshot.load(run_id)
    documents = {item.id: item.document() for item in captured}
    try:
        models = []
        references = {}
        for item in captured:
            document = documents[item.id]
            refs = [_reference(raw) for raw in document["references"]]
            if len({ref.path for ref in refs}) != len(refs):
                raise ValueError("Duplicate archived reference paths")
            references[item.id] = {ref.path: ref for ref in refs}
            models.append(
                skills.Skill(
                    id=item.id,
                    name=document["name"],
                    description=document["description"],
                    version=document["version"],
                    enabled=True,
                    created_at="",
                    updated_at="",
                    source_filename=None,
                    archive_sha256="",
                    skill_md_chars=len(document["body"]),
                    references=refs,
                )
            )
        if (
            not isinstance(selected_sections, dict)
            or set(selected_sections) - documents.keys()
        ):
            raise ValueError("Unknown selected skill")
        selected = {}
        for skill_id, choices in selected_sections.items():
            if not isinstance(choices, list):
                raise ValueError("Selections must be a list")
            seen = set()
            selected[skill_id] = []
            for choice in choices:
                if (
                    not isinstance(choice, skills.SectionSelection)
                    or not isinstance(choice.path, str)
                    or choice.path not in references[skill_id]
                    or choice.path in seen
                    or not isinstance(choice.section_ids, tuple)
                    or any(not isinstance(key, str) for key in choice.section_ids)
                    or len(set(choice.section_ids)) != len(choice.section_ids)
                ):
                    raise ValueError("Unknown or ambiguous reference selection")
                ref = references[skill_id][choice.path]
                available = {
                    section.section_id for section in skills.reference_sections(ref)
                }
                if set(choice.section_ids) - available:
                    raise ValueError("Unknown selected section")
                seen.add(choice.path)
                selected[skill_id].append(
                    skills.SectionSelection(choice.path, tuple(choice.section_ids))
                )
        reason_map = {}
        if reasons is not None:
            if not isinstance(reasons, dict) or set(reasons) - set(selected):
                raise ValueError("Reasons must refer to selected skills")
            for skill_id, entries in reasons.items():
                valid = {choice.path for choice in selected[skill_id]}
                valid.update(
                    f"{choice.path}::{section.section_id}"
                    for choice in selected[skill_id]
                    for section in skills.reference_sections(
                        references[skill_id][choice.path]
                    )
                    if not choice.section_ids
                    or section.section_id in choice.section_ids
                )
                if (
                    not isinstance(entries, dict)
                    or set(entries) - valid
                    or any(not isinstance(value, str) for value in entries.values())
                ):
                    raise ValueError("Reasons must refer to selected references")
                reason_map[skill_id] = dict(entries)
    except (KeyError, TypeError, ValueError) as exc:
        raise SkillRenderingConflict(
            "Archived skill rendering inputs are unsupported"
        ) from exc

    def read_sections(
        skill_id: str, path: str, requested: Sequence[str]
    ) -> list[tuple[str, str]]:
        raw = next(
            ref for ref in documents[skill_id]["references"] if ref["path"] == path
        )
        pairs = [
            (str(index), text)
            for index, (_, text) in enumerate(
                skills._split_reference_sections(raw["text"]), 1
            )
        ]
        wanted = set(requested)
        return [pair for pair in pairs if not wanted or pair[0] in wanted]

    block, report = skills._render_skills_context(
        models,
        selected,
        max_chars,
        reason_map,
        read_markdown=lambda skill_id: documents[skill_id]["body"],
        read_sections=read_sections,
    )
    if block is not None and len(block) > max_chars:
        raise SkillRenderingConflict("Character budget cannot hold skill framing")
    # The archive deliberately excludes scripts/assets/evals and their counts.
    # Do not pretend the original bundle had zero unused files.
    for entry in report["resolved"]:
        entry.pop("bundled_unused", None)
    report["bundle_inventory"] = "not_archived"
    report["renderer_version"] = 1
    report["source"] = run_skill_provenance._identity(captured)
    return block, report
