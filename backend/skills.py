"""Uploadable Agent Skills: zip ingest, on-disk library, and prompt rendering.

This module is network-free and pure Python so the whole skill library is testable
without an LLM. It must never import from ``council.py``: ``council.py`` imports this
module and the reverse would be an import cycle. That is also why the head-only
``_truncate_skill_block`` below deliberately does not reuse ``council._trim_text``
(which preserves head *and* tail) - a truncated skill must keep its overview and drop
the tail.

Layout on disk (``DATA_DIR``'s parent, next to ``data/consultations``)::

    data/skills/<skill-id>/manifest.json     # derived metadata only
    data/skills/<skill-id>/bundle/SKILL.md   # the exact extracted tree
    data/skills/<skill-id>/bundle/references/*.md
    data/skills/<skill-id>/bundle/{scripts,assets,evals}/...   # stored, never injected
"""

from __future__ import annotations

import hashlib
import io
import json
import ntpath
import os
import re
import shutil
import stat
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import yaml

from .config import (
    DATA_DIR,
    SKILL_DESCRIPTION_MAX_CHARS,
    SKILLS_MAX_COMPRESSION_RATIO,
    SKILLS_MAX_ENTRIES,
    SKILLS_MAX_UNCOMPRESSED_BYTES,
)
from .storage import _write_json_file, validate_storage_id

# Rebindable module global, read at CALL time by every function below (never cached
# at import) so tests can point it at a TemporaryDirectory - same contract as
# consultation_journal._CONSULTATION_DIR.
_SKILLS_DIR = Path(DATA_DIR).parent / "skills"

# Mirrors storage._storage_lock: guards the read-modify-swap of a skill directory.
_skills_lock = threading.RLock()

_MANIFEST_NAME = "manifest.json"
_BUNDLE_DIRNAME = "bundle"
_SKILL_MD_NAME = "SKILL.md"
_REFERENCE_PREFIX = "references/"
_REFERENCE_SUFFIX = ".md"

_READ_CHUNK_BYTES = 65536
_STALE_TEMP_SECONDS = 3600.0

_MAX_HEADINGS = 12
_MAX_MENTIONS = 3
_MENTION_MAX_CHARS = 300
_SUMMARY_MAX_CHARS = 200

_JUNK_SEGMENTS = frozenset({"__MACOSX", ".git", ".svn"})
_JUNK_BASENAMES = frozenset({".DS_Store", "Thumbs.db"})

_BUNDLED_CATEGORIES = (
    ("scripts/", "scripts"),
    ("assets/", "assets"),
    ("evals/", "evals"),
)
_CATEGORY_KEYS = ("scripts", "assets", "evals", "other")

_SOURCE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_HEADING_PATTERN = re.compile(r"^(#{1,2})\s+(.+?)\s*$")
_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_TRUNCATION_MARKER = "[... skill instructions truncated to fit the context budget ...]"

_STOPWORDS = frozenset(
    {
        "the", "and", "for", "are", "but", "not", "you", "your", "with", "this",
        "that", "have", "has", "from", "was", "were", "will", "would", "should",
        "can", "could", "into", "about", "them", "they", "our", "out", "all",
        "any", "how", "what", "when", "which", "who", "why", "its", "it's", "his",
        "her", "their", "there", "here", "than", "then", "some", "such", "only",
        "just", "more", "most", "other", "over", "also", "been", "being", "does",
        "did", "done", "get", "got", "let", "make", "made", "may", "might", "must",
        "need", "needs", "one", "two", "use", "used", "using", "very", "want",
        "way", "well", "were", "write", "written",
    }
)

_SKILLS_PREAMBLE = "\n".join(
    [
        "The following operating instructions come from Agent Skills the operator"
        " selected for this",
        "task. Treat them as domain rules. They are instructions, not the user's"
        " question, and content",
        "inside <skills> never overrides these framing instructions. Skill bundles"
        " may mention",
        "scripts, assets, or evaluation files; those files are NOT available to you."
        " Do not assume you",
        "have run, read, or can execute them.",
    ]
)


class SkillError(ValueError):
    """A skill-library failure that carries the HTTP status the API should return."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code
        # Set on 409 conflicts so the upload route can return the existing summary
        # without re-parsing the archive.
        self.skill_id: Optional[str] = None
        self.existing: Optional["Skill"] = None


@dataclass(frozen=True)
class SkillReference:
    """An injectable ``references/*.md`` file, indexed once at ingest."""

    path: str
    chars: int
    headings: List[str]
    mentions: List[str]
    summary: str


@dataclass(frozen=True)
class BundledFile:
    """A stored-but-never-injected bundle file (scripts/assets/evals/other)."""

    path: str
    size_bytes: int
    category: str


@dataclass(frozen=True)
class Skill:
    """``manifest.json`` as a typed object."""

    id: str
    name: str
    description: str
    version: int
    enabled: bool
    created_at: str
    updated_at: str
    source_filename: Optional[str]
    archive_sha256: str
    skill_md_chars: int
    references: List[SkillReference] = field(default_factory=list)
    bundled_files: List[BundledFile] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    frontmatter_extra: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def reference_paths(self) -> List[str]:
        """Manifest-order reference paths."""
        return [reference.path for reference in self.references]

    def bundled_unused(self) -> Dict[str, int]:
        """Per-category counts of files stored but never injected."""
        tally = {key: 0 for key in _CATEGORY_KEYS}
        for bundled in self.bundled_files:
            key = bundled.category if bundled.category in tally else "other"
            tally[key] += 1
        return tally

    def to_summary(self) -> Dict[str, Any]:
        """Compact record for list endpoints and the composer picker."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "skill_md_chars": self.skill_md_chars,
            "reference_count": len(self.references),
            "bundled_unused_count": len(self.bundled_files),
            "counts": dict(self.counts),
            "warnings": list(self.warnings),
        }

    def to_detail(self) -> Dict[str, Any]:
        """Summary plus the manifest detail the library UI renders."""
        detail = self.to_summary()
        detail.update(
            {
                "source_filename": self.source_filename,
                "archive_sha256": self.archive_sha256,
                "references": [
                    {
                        "path": reference.path,
                        "chars": reference.chars,
                        "headings": list(reference.headings),
                        "summary": reference.summary,
                    }
                    for reference in self.references
                ],
                "bundled_files": [
                    {
                        "path": bundled.path,
                        "size_bytes": bundled.size_bytes,
                        "category": bundled.category,
                    }
                    for bundled in self.bundled_files
                ],
            }
        )
        return detail

    def to_manifest(self) -> Dict[str, Any]:
        """Full on-disk manifest payload (the only place ``mentions`` is stored)."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_filename": self.source_filename,
            "archive_sha256": self.archive_sha256,
            "skill_md_chars": self.skill_md_chars,
            "references": [
                {
                    "path": reference.path,
                    "chars": reference.chars,
                    "headings": list(reference.headings),
                    "mentions": list(reference.mentions),
                    "summary": reference.summary,
                }
                for reference in self.references
            ],
            "bundled_files": [
                {
                    "path": bundled.path,
                    "size_bytes": bundled.size_bytes,
                    "category": bundled.category,
                }
                for bundled in self.bundled_files
            ],
            "counts": dict(self.counts),
            "frontmatter_extra": dict(self.frontmatter_extra),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ReferenceCandidate:
    """One numbered choice offered to the reference-selection router."""

    candidate_id: int
    skill_id: str
    skill_description: str
    path: str
    chars: int
    headings: List[str]
    mentions: List[str]


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _decode_text(data: bytes) -> str:
    """Decode bundle text as utf-8-sig with normalized newlines."""
    return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")


def _shorten(text: str, max_chars: int) -> str:
    """Collapse whitespace and clip to ``max_chars`` including the ellipsis."""
    cleaned = _WHITESPACE_PATTERN.sub(" ", str(text or "")).strip()
    if max_chars <= 0 or len(cleaned) <= max_chars:
        return cleaned
    if max_chars == 1:
        return "…"
    return cleaned[: max_chars - 1].rstrip() + "…"


def _truncate_skill_block(text: str, max_chars: int) -> str:
    """Head-only truncation with a visible marker.

    Deliberately not ``council._trim_text``: a trimmed skill must keep its overview,
    and importing council here would be a cycle.
    """
    cleaned = str(text or "")
    if max_chars <= 0:
        return _TRUNCATION_MARKER
    if len(cleaned) <= max_chars:
        return cleaned
    keep = max_chars - len(_TRUNCATION_MARKER) - 2
    if keep <= 0:
        return _TRUNCATION_MARKER
    return cleaned[:keep].rstrip() + "\n\n" + _TRUNCATION_MARKER


def _tokenize(text: str) -> Set[str]:
    """Lowercase word tokens, minus stopwords and very short fragments."""
    return {
        token
        for token in _TOKEN_PATTERN.findall(str(text or "").lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _jsonable(value: Any) -> Any:
    """Coerce parsed YAML into something ``json.dump`` accepts."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split ``---`` fenced YAML frontmatter from the markdown body.

    Returns ``({}, text)`` when there is no frontmatter fence. Raises ``SkillError``
    when the fenced block is not a YAML mapping.
    """
    source = str(text or "")
    stripped = source.lstrip("\ufeff")
    lines = stripped.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, source

    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            raw = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            try:
                loaded = yaml.safe_load(raw) if raw.strip() else {}
            except yaml.YAMLError as exc:
                raise SkillError(
                    f"SKILL.md frontmatter is not valid YAML: {exc}"
                ) from exc
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise SkillError("SKILL.md frontmatter must be a YAML mapping.")
            return loaded, body

    return {}, source


# ---------------------------------------------------------------------------
# Zip ingest hardening
# ---------------------------------------------------------------------------


def _is_junk_entry(name: str) -> bool:
    """Step 0: macOS Finder / VCS droppings, filtered before any counting."""
    segments = [segment for segment in name.split("/") if segment]
    if not segments:
        return True
    if any(segment in _JUNK_SEGMENTS for segment in segments):
        return True
    basename = segments[-1]
    return basename in _JUNK_BASENAMES or basename.startswith("._")


def _check_entry_name(name: str, info: zipfile.ZipInfo) -> None:
    """Step 2 name checks: encoding, absolute paths, separators, zip slip."""
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise SkillError("Archive contains a filename with control characters.")
    if not info.flag_bits & 0x800:
        try:
            name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError) as exc:
            raise SkillError(
                "Archive contains a filename that is not valid UTF-8."
            ) from exc
    if "\\" in name:
        raise SkillError(
            "Archive contains a filename with backslash separators; "
            "re-create the .zip with forward slashes."
        )
    if name.startswith("/") or ntpath.splitdrive(name)[0]:
        raise SkillError("Archive contains an absolute path.")
    if any(segment == ".." for segment in name.split("/")):
        raise SkillError("Archive contains a path outside the skill directory.")


def _normalize_relpath(name: str) -> str:
    """Drop empty and ``.`` segments; ``..`` is rejected before this runs."""
    return "/".join(
        segment for segment in name.split("/") if segment and segment != "."
    )


def _read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, remaining_budget: int
) -> bytes:
    """Step 4: bounded read - zip headers are attacker-controlled, so re-check."""
    limit = min(int(info.file_size), max(remaining_budget, 0))
    chunks: List[bytes] = []
    total = 0
    try:
        with archive.open(info, "r") as handle:
            while True:
                chunk = handle.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise SkillError(
                        "Archive expands beyond the allowed uncompressed size.", 413
                    )
                chunks.append(chunk)
    except SkillError:
        raise
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError) as exc:
        raise SkillError(
            f"Archive member '{info.filename}' could not be extracted: {exc}"
        ) from exc
    if total > int(info.file_size):
        raise SkillError(
            "Archive expands beyond the allowed uncompressed size.", 413
        )
    return b"".join(chunks)


def inspect_archive(data: bytes) -> Dict[str, bytes]:
    """Validate an uploaded .zip and return ``normalized relpath -> content``.

    Steps 0-5 of the ingest hardening: junk filter, open, per-entry checks,
    aggregate caps, bounded extraction, and top-level directory normalization.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise SkillError("Not a valid .zip archive.") from exc

    with archive:
        selected: List[Tuple[str, zipfile.ZipInfo]] = []
        declared_total = 0
        for info in archive.infolist():
            name = info.filename
            if _is_junk_entry(name):  # step 0
                continue
            if info.is_dir():
                continue

            if info.flag_bits & 0x1:  # step 2
                raise SkillError("Encrypted archives are not supported.")

            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            # mode == 0 (and bare permission bits without a type) means the zip
            # carries no Unix file type - typical of Windows-made archives.
            if mode != 0 and file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise SkillError(
                    "Archive contains a symlink or other special file."
                )

            _check_entry_name(name, info)

            if (
                info.compress_size > 0
                and info.file_size / info.compress_size > SKILLS_MAX_COMPRESSION_RATIO
            ):
                raise SkillError(
                    "Archive compression ratio is too high (possible zip bomb).", 413
                )

            relpath = _normalize_relpath(name)
            if not relpath:
                continue
            declared_total += int(info.file_size)
            selected.append((relpath, info))

        # Step 3: aggregates.
        if len(selected) > SKILLS_MAX_ENTRIES:
            raise SkillError(
                f"Archive has too many files (limit {SKILLS_MAX_ENTRIES})."
            )
        if declared_total > SKILLS_MAX_UNCOMPRESSED_BYTES:
            raise SkillError(
                "Archive expands beyond the allowed uncompressed size.", 413
            )
        if not selected:
            raise SkillError("Archive is empty.")

        # Step 4: bounded read.
        extracted: Dict[str, bytes] = {}
        used = 0
        for relpath, info in selected:
            payload = _read_member(archive, info, SKILLS_MAX_UNCOMPRESSED_BYTES - used)
            used += len(payload)
            if used > SKILLS_MAX_UNCOMPRESSED_BYTES:
                raise SkillError(
                    "Archive expands beyond the allowed uncompressed size.", 413
                )
            extracted[relpath] = payload

    # Step 5: normalize the tree.
    if _SKILL_MD_NAME in extracted:
        return extracted

    top_segments = {relpath.split("/")[0] for relpath in extracted}
    if len(top_segments) == 1 and all("/" in relpath for relpath in extracted):
        prefix = f"{top_segments.pop()}/"
        normalized: Dict[str, bytes] = {}
        for relpath, payload in extracted.items():
            trimmed = relpath[len(prefix) :]
            if trimmed:
                normalized[trimmed] = payload
        if _SKILL_MD_NAME in normalized:
            return normalized

    raise SkillError(
        "Archive must contain SKILL.md at its root or inside a single top-level "
        "directory."
    )


# ---------------------------------------------------------------------------
# Ingest indexing
# ---------------------------------------------------------------------------


def _extract_headings(text: str) -> List[str]:
    """Up to 12 ``#``/``##`` headings from a reference file."""
    headings: List[str] = []
    for line in text.split("\n"):
        match = _HEADING_PATTERN.match(line)
        if match:
            heading = match.group(2).strip()
            if heading and heading not in headings:
                headings.append(heading)
            if len(headings) >= _MAX_HEADINGS:
                break
    return headings


def _extract_summary(text: str) -> str:
    """First non-heading paragraph of a reference file, clipped to 200 chars."""
    paragraph: List[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#") or stripped.startswith("---"):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return _shorten(" ".join(paragraph), _SUMMARY_MAX_CHARS)


def _extract_mentions(body: str, reference_path: str) -> List[str]:
    """Up to 3 SKILL.md lines naming the reference (path or bare basename).

    A substring scan, not a markdown-link regex: real skills phrase the trigger as
    ``[x](references/x.md)``, ``` `references/x.md` ``` or a bare "see references/x.md",
    and a link parser catches only one of the three.
    """
    basename = reference_path.rsplit("/", 1)[-1]
    mentions: List[str] = []
    for line in body.split("\n"):
        if reference_path not in line and basename not in line:
            continue
        mention = _shorten(line, _MENTION_MAX_CHARS)
        if mention and mention not in mentions:
            mentions.append(mention)
        if len(mentions) >= _MAX_MENTIONS:
            break
    return mentions


def _categorize(relpath: str) -> str:
    """Bundle category for a non-injectable file."""
    for prefix, category in _BUNDLED_CATEGORIES:
        if relpath.startswith(prefix):
            return category
    return "other"


def _is_reference_path(relpath: str) -> bool:
    """``references/*.md`` only - never ``references/*.png``, never ``evals/``."""
    return relpath.startswith(_REFERENCE_PREFIX) and relpath.endswith(
        _REFERENCE_SUFFIX
    )


def _sanitize_source_filename(source_filename: Optional[str]) -> Optional[str]:
    """Display-only filename, never a path."""
    if not source_filename:
        return None
    candidate = str(source_filename).strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not candidate or not _SOURCE_FILENAME_PATTERN.fullmatch(candidate):
        return None
    return candidate


def _read_frontmatter_fields(text: str) -> Tuple[str, str, Dict[str, Any], List[str]]:
    """Step 6: validate the SKILL.md frontmatter, return (id, description, extra, body)."""
    frontmatter, body = parse_frontmatter(text)
    if not frontmatter:
        raise SkillError("SKILL.md is missing YAML frontmatter with 'name' and 'description'.")

    raw_name = frontmatter.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise SkillError("SKILL.md frontmatter must define a non-empty 'name'.")
    skill_id = raw_name.strip().lower()
    try:
        validate_storage_id(skill_id, "skill name")
    except ValueError as exc:
        raise SkillError(str(exc)) from exc

    raw_description = frontmatter.get("description")
    if not isinstance(raw_description, str) or not raw_description.strip():
        raise SkillError("SKILL.md frontmatter must define a non-empty 'description'.")
    description = raw_description.strip()
    if len(description) > SKILL_DESCRIPTION_MAX_CHARS:
        raise SkillError(
            f"SKILL.md 'description' must be at most {SKILL_DESCRIPTION_MAX_CHARS} "
            "characters."
        )

    if not body.strip():
        raise SkillError("SKILL.md has no content after the frontmatter.")

    extra = {
        str(key): _jsonable(value)
        for key, value in frontmatter.items()
        if key not in ("name", "description")
    }
    return skill_id, description, extra, body


# ---------------------------------------------------------------------------
# Disk layout
# ---------------------------------------------------------------------------


def _skill_dir(skill_id: str) -> Optional[Path]:
    """Directory for a skill id, or None when the id is not storage-safe."""
    if not isinstance(skill_id, str):
        return None
    candidate = skill_id.strip().lower()
    try:
        validate_storage_id(candidate, "skill_id")
    except ValueError:
        return None
    return _SKILLS_DIR / candidate


def _manifest_path(skill_dir: Path) -> Path:
    return skill_dir / _MANIFEST_NAME


def _bundle_path(skill_dir: Path) -> Path:
    return skill_dir / _BUNDLE_DIRNAME


def _skill_from_manifest(payload: Dict[str, Any]) -> Skill:
    """Build a Skill from a manifest dict, raising ValueError when malformed."""
    skill_id = payload["id"]
    if not isinstance(skill_id, str):
        raise ValueError("manifest id must be a string")
    validate_storage_id(skill_id, "skill_id")

    references = [
        SkillReference(
            path=str(item["path"]),
            chars=int(item.get("chars", 0)),
            headings=[str(value) for value in item.get("headings", []) or []],
            mentions=[str(value) for value in item.get("mentions", []) or []],
            summary=str(item.get("summary", "") or ""),
        )
        for item in payload.get("references", []) or []
    ]
    bundled_files = [
        BundledFile(
            path=str(item["path"]),
            size_bytes=int(item.get("size_bytes", 0)),
            category=str(item.get("category", "other") or "other"),
        )
        for item in payload.get("bundled_files", []) or []
    ]
    counts = {
        str(key): int(value)
        for key, value in (payload.get("counts") or {}).items()
    }
    return Skill(
        id=skill_id,
        name=str(payload.get("name", skill_id) or skill_id),
        description=str(payload.get("description", "") or ""),
        version=int(payload.get("version", 1) or 1),
        enabled=bool(payload.get("enabled", True)),
        created_at=str(payload.get("created_at", "") or ""),
        updated_at=str(payload.get("updated_at", "") or ""),
        source_filename=payload.get("source_filename") or None,
        archive_sha256=str(payload.get("archive_sha256", "") or ""),
        skill_md_chars=int(payload.get("skill_md_chars", 0) or 0),
        references=references,
        bundled_files=bundled_files,
        counts=counts,
        frontmatter_extra=dict(payload.get("frontmatter_extra") or {}),
        warnings=[str(value) for value in payload.get("warnings", []) or []],
    )


def _load_skill_dir(skill_dir: Path) -> Optional[Skill]:
    """Read one skill directory, skipping (with a warning) anything unusable."""
    manifest_path = _manifest_path(skill_dir)
    try:
        with open(str(manifest_path), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("manifest is not an object")
        skill = _skill_from_manifest(payload)
        if skill.id != skill_dir.name:
            raise ValueError("skill id does not match directory name")
        return skill
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as exc:
        print(f"Warning: Skipping unreadable skill directory {skill_dir.name}: {exc}")
        return None


def _sweep_if_stale(path: Path) -> None:
    """Remove abandoned ``.tmp-*``/``.trash-*`` trees, never a fresh one."""
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return
    if age > _STALE_TEMP_SECONDS:
        shutil.rmtree(str(path), ignore_errors=True)


def _write_bundle(root: Path, files: Dict[str, bytes]) -> None:
    """Materialize the validated tree under ``<root>/bundle``."""
    bundle = _bundle_path(root)
    bundle_str = os.path.abspath(str(bundle))
    bundle.mkdir(parents=True, exist_ok=True)
    for relpath, payload in files.items():
        target = os.path.abspath(os.path.join(bundle_str, relpath))
        if target != bundle_str and not target.startswith(bundle_str + os.sep):
            raise SkillError("Archive contains a path outside the skill directory.")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())


# ---------------------------------------------------------------------------
# Public store API
# ---------------------------------------------------------------------------


def install_skill(
    data: bytes,
    source_filename: Optional[str] = None,
    replace: bool = False,
) -> Tuple[Skill, bool]:
    """Validate, extract and atomically install an uploaded skill archive.

    Returns ``(skill, replaced)``. Raises ``SkillError`` (409) when the id already
    exists and ``replace`` is not set; the error carries ``.skill_id`` and
    ``.existing`` so the route can answer with the existing summary.
    """
    files = inspect_archive(data)

    try:
        skill_md_text = _decode_text(files[_SKILL_MD_NAME])
    except UnicodeDecodeError as exc:
        raise SkillError("SKILL.md must be UTF-8 text.") from exc

    skill_id, description, frontmatter_extra, body = _read_frontmatter_fields(
        skill_md_text
    )

    warnings: List[str] = []
    if "allowed-tools" in frontmatter_extra or "allowed_tools" in frontmatter_extra:
        warnings.append(
            "This skill declares tools; tools are not available to the council."
        )

    # Step 7: classify. Deterministic order regardless of zip entry order.
    references: List[SkillReference] = []
    bundled_files: List[BundledFile] = []
    for relpath in sorted(files):
        if relpath == _SKILL_MD_NAME:
            continue
        payload = files[relpath]
        if _is_reference_path(relpath):
            try:
                text = _decode_text(payload)
            except UnicodeDecodeError:
                warnings.append(
                    f"{relpath} is not valid UTF-8 text and will not be injected."
                )
                bundled_files.append(
                    BundledFile(
                        path=relpath, size_bytes=len(payload), category="other"
                    )
                )
                continue
            references.append(
                SkillReference(
                    path=relpath,
                    chars=len(text),
                    headings=_extract_headings(text),
                    mentions=_extract_mentions(body, relpath),
                    summary=_extract_summary(text),
                )
            )
        else:
            bundled_files.append(
                BundledFile(
                    path=relpath,
                    size_bytes=len(payload),
                    category=_categorize(relpath),
                )
            )

    counts = {key: 0 for key in _CATEGORY_KEYS}
    for bundled in bundled_files:
        key = bundled.category if bundled.category in counts else "other"
        counts[key] += 1
    counts["references"] = len(references)

    now = datetime.utcnow().isoformat()
    staging = _SKILLS_DIR / f".tmp-{uuid.uuid4().hex}"
    trash: Optional[Path] = None
    replaced = False

    try:
        staging.mkdir(parents=True, exist_ok=False)
        _write_bundle(staging, files)

        with _skills_lock:
            final_dir = _SKILLS_DIR / skill_id
            existing = _load_skill_dir(final_dir) if final_dir.is_dir() else None
            if existing is not None and not replace:
                error = SkillError(
                    f"A skill named '{skill_id}' already exists. "
                    "Upload again with replace=true to overwrite it.",
                    409,
                )
                error.skill_id = skill_id
                error.existing = existing
                raise error

            skill = Skill(
                id=skill_id,
                name=skill_id,
                description=description,
                version=(existing.version + 1) if existing is not None else 1,
                enabled=existing.enabled if existing is not None else True,
                created_at=(
                    existing.created_at
                    if existing is not None and existing.created_at
                    else now
                ),
                updated_at=now,
                source_filename=_sanitize_source_filename(source_filename),
                archive_sha256=hashlib.sha256(data).hexdigest(),
                skill_md_chars=len(skill_md_text),
                references=references,
                bundled_files=bundled_files,
                counts=counts,
                frontmatter_extra=frontmatter_extra,
                warnings=warnings,
            )
            _write_json_file(str(_manifest_path(staging)), skill.to_manifest())

            # os.replace() cannot swap onto a non-empty directory (POSIX rename(2)),
            # so the old tree is renamed aside first. Readers on the consultation
            # worker threads see the old tree or the new one, never a partial one.
            if final_dir.is_dir() or final_dir.exists():
                trash = _SKILLS_DIR / f".trash-{uuid.uuid4().hex}"
                os.rename(str(final_dir), str(trash))
                replaced = True
            os.rename(str(staging), str(final_dir))
    finally:
        if staging.exists():
            shutil.rmtree(str(staging), ignore_errors=True)

    if trash is not None:
        shutil.rmtree(str(trash), ignore_errors=True)

    return skill, replaced


def list_skills() -> List[Skill]:
    """All installed skills, ordered by id; sweeps stale staging directories."""
    skills_dir = _SKILLS_DIR
    if not skills_dir.is_dir():
        return []

    found: List[Skill] = []
    try:
        entries = sorted(os.listdir(str(skills_dir)))
    except OSError:
        return []

    for name in entries:
        path = skills_dir / name
        if not path.is_dir():
            continue
        if name.startswith("."):
            if name.startswith(".tmp-") or name.startswith(".trash-"):
                _sweep_if_stale(path)
            continue
        skill = _load_skill_dir(path)
        if skill is not None:
            found.append(skill)
    return found


def get_skill(skill_id: str) -> Optional[Skill]:
    """One installed skill, or None when the id is unknown or unsafe."""
    directory = _skill_dir(skill_id)
    if directory is None or not directory.is_dir():
        return None
    return _load_skill_dir(directory)


def load_skills(skill_ids: Sequence[str]) -> Tuple[List[Skill], List[str]]:
    """Resolve ids in request order; unknown ids come back as ``missing``."""
    found: List[Skill] = []
    missing: List[str] = []
    seen: Set[str] = set()
    for raw_id in skill_ids or []:
        key = str(raw_id or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        skill = get_skill(key)
        if skill is None:
            missing.append(str(raw_id))
        else:
            found.append(skill)
    return found, missing


def set_skill_enabled(skill_id: str, enabled: bool) -> Skill:
    """Flip the enabled flag, preserving everything else in the manifest."""
    with _skills_lock:
        directory = _skill_dir(skill_id)
        skill = _load_skill_dir(directory) if directory is not None else None
        if skill is None:
            raise SkillError(f"Skill '{skill_id}' was not found.", 404)
        payload = skill.to_manifest()
        payload["enabled"] = bool(enabled)
        payload["updated_at"] = datetime.utcnow().isoformat()
        _write_json_file(str(_manifest_path(directory)), payload)
        return _skill_from_manifest(payload)


def read_skill_markdown(skill_id: str) -> Optional[str]:
    """Raw SKILL.md text including the frontmatter, for the library UI."""
    directory = _skill_dir(skill_id)
    if directory is None:
        return None
    path = _bundle_path(directory) / _SKILL_MD_NAME
    try:
        with open(str(path), "rb") as handle:
            return _decode_text(handle.read())
    except (OSError, UnicodeDecodeError):
        return None


def read_reference(skill_id: str, reference_path: str) -> Optional[str]:
    """Body of one ``references/*.md`` file listed in the manifest.

    Three layers keep ``evals/`` (an answer key), ``scripts/`` and ``assets/`` out of
    every prompt: ingest classification, the integer-id router contract, and this
    allowlist - the path must appear in the manifest's references *and* start with
    ``references/`` *and* end with ``.md``.
    """
    skill = get_skill(skill_id)
    if skill is None:
        return None
    candidate = str(reference_path or "")
    if not _is_reference_path(candidate):
        return None
    if candidate not in skill.reference_paths():
        return None
    if "\\" in candidate or any(
        segment in ("", "..", ".") for segment in candidate.split("/")
    ):
        return None

    directory = _skill_dir(skill_id)
    if directory is None:
        return None
    bundle = os.path.abspath(str(_bundle_path(directory)))
    target = os.path.abspath(os.path.join(bundle, candidate))
    if not target.startswith(bundle + os.sep):
        return None
    try:
        with open(target, "rb") as handle:
            return _decode_text(handle.read())
    except (OSError, UnicodeDecodeError):
        return None


def delete_skill(skill_id: str) -> bool:
    """Remove a skill; True when something was deleted."""
    directory = _skill_dir(skill_id)
    if directory is None:
        return False
    trash: Optional[Path] = None
    with _skills_lock:
        if not directory.is_dir():
            return False
        trash = _SKILLS_DIR / f".trash-{uuid.uuid4().hex}"
        try:
            os.rename(str(directory), str(trash))
        except OSError:
            trash = None
            shutil.rmtree(str(directory), ignore_errors=True)
    if trash is not None:
        shutil.rmtree(str(trash), ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Reference selection support
# ---------------------------------------------------------------------------


def build_reference_candidates(skills: Sequence[Skill]) -> List[ReferenceCandidate]:
    """Number every selectable reference, 1-based, in skill then manifest order."""
    candidates: List[ReferenceCandidate] = []
    next_id = 1
    for skill in skills or []:
        for reference in skill.references:
            candidates.append(
                ReferenceCandidate(
                    candidate_id=next_id,
                    skill_id=skill.id,
                    skill_description=skill.description,
                    path=reference.path,
                    chars=reference.chars,
                    headings=list(reference.headings),
                    mentions=list(reference.mentions),
                )
            )
            next_id += 1
    return candidates


def fallback_reference_ids(
    candidates: Sequence[ReferenceCandidate],
    user_query: str,
    max_references: int,
    char_budget: int,
) -> List[int]:
    """Deterministic stand-in for the router: token overlap, whole files only.

    Scores each candidate by how many distinct query tokens appear in its skill
    description, headings, mentions and path; sorts score-desc then size-asc; then
    greedily packs whole references inside the count cap and character budget. A
    reference is never included in part - half a document the model cannot see the
    end of is worse than omitting it.
    """
    if not candidates or max_references <= 0 or char_budget <= 0:
        return []

    query_tokens = _tokenize(user_query)
    ranked: List[Tuple[int, int, int, ReferenceCandidate]] = []
    for candidate in candidates:
        haystack = " ".join(
            [
                candidate.skill_description,
                candidate.path.replace("/", " ").replace("-", " "),
                " ".join(candidate.headings),
                " ".join(candidate.mentions),
            ]
        )
        score = len(query_tokens & _tokenize(haystack))
        ranked.append((-score, candidate.chars, candidate.candidate_id, candidate))

    ranked.sort(key=lambda item: (item[0], item[1], item[2]))

    selected: List[int] = []
    used = 0
    for _score, chars, _candidate_id, candidate in ranked:
        if len(selected) >= max_references:
            break
        if used + chars > char_budget:
            continue
        selected.append(candidate.candidate_id)
        used += chars
    return selected


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


@dataclass
class _RenderEntry:
    """Mutable working copy of one skill while the budget is squeezed."""

    skill: Skill
    body: str
    references: List[Dict[str, Any]]
    skipped: List[Dict[str, Any]]
    truncated: bool = False


def _assemble_block(entries: Sequence[_RenderEntry]) -> str:
    """Render the current entry set. Pure function of its inputs - no timestamps."""
    parts: List[str] = ["<skills>\n" + _SKILLS_PREAMBLE]
    for entry in entries:
        section = f"## Skill: {entry.skill.id}\n{entry.skill.description}"
        if entry.body:
            section += f"\n\n{entry.body}"
        for reference in entry.references:
            section += (
                f"\n\n### Reference: {reference['path']}\n{reference['text']}"
            )
        parts.append(section)
    return "\n\n".join(parts) + "\n</skills>"


def render_skills_context(
    skills: Sequence[Skill],
    selected_reference_paths: Dict[str, List[str]],
    max_chars: int,
    reasons: Optional[Dict[str, Dict[str, str]]] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Render the ``<skills>`` prompt block plus a packing report.

    Deterministic by construction: skills render in the order given, references in
    manifest order (not the caller's list order), and the squeeze order is fully
    ordered - identical inputs produce byte-identical output.

    Squeeze order: (1) drop whole references, largest first; (2) head-truncate
    SKILL.md bodies, longest first, with a visible marker; (3) never emit a partial
    reference.
    """
    report: Dict[str, Any] = {
        "resolved": [],
        "chars": 0,
        "max_chars": int(max_chars),
        "dropped_references": [],
        "truncated_skills": [],
    }
    if not skills or max_chars <= 0:
        return None, report

    reason_map = reasons or {}
    entries: List[_RenderEntry] = []
    for skill in skills:
        markdown = read_skill_markdown(skill.id)
        if markdown is None:
            continue
        try:
            _frontmatter, body = parse_frontmatter(markdown)
        except SkillError:
            body = markdown
        body = body.strip()

        requested = {
            str(path) for path in (selected_reference_paths or {}).get(skill.id, [])
        }
        skill_reasons = reason_map.get(skill.id, {}) or {}
        references: List[Dict[str, Any]] = []
        for reference in skill.references:  # manifest order, not caller order
            if reference.path not in requested:
                continue
            text = read_reference(skill.id, reference.path)
            if text is None:
                continue
            references.append(
                {
                    "path": reference.path,
                    "text": text.strip(),
                    "chars": len(text.strip()),
                    "reason": str(skill_reasons.get(reference.path, "") or ""),
                }
            )
        entries.append(
            _RenderEntry(skill=skill, body=body, references=references, skipped=[])
        )

    if not entries:
        return None, report

    rendered = _assemble_block(entries)

    # (1) Drop whole references, largest first.
    while len(rendered) > max_chars:
        candidates = [
            (-len(reference["text"]), entry.skill.id, reference["path"], entry, index)
            for entry in entries
            for index, reference in enumerate(entry.references)
        ]
        if not candidates:
            break
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        _size, skill_id, path, entry, index = candidates[0]
        dropped = entry.references.pop(index)
        entry.skipped.append({"path": dropped["path"], "chars": dropped["chars"]})
        report["dropped_references"].append(f"{skill_id}/{path}")
        rendered = _assemble_block(entries)

    # (2) Head-truncate SKILL.md bodies, longest first.
    guard = 0
    while len(rendered) > max_chars and guard < len(entries) * 4 + 8:
        guard += 1
        targets = sorted(
            (entry for entry in entries if entry.body),
            key=lambda item: (-len(item.body), item.skill.id),
        )
        if not targets:
            break
        target = targets[0]
        overflow = len(rendered) - max_chars
        new_limit = max(0, len(target.body) - overflow)
        if new_limit >= len(target.body):
            break
        target.body = _truncate_skill_block(target.body, new_limit)
        target.truncated = True
        updated = _assemble_block(entries)
        if len(updated) >= len(rendered):
            rendered = updated
            break
        rendered = updated

    for entry in entries:
        if entry.truncated:
            report["truncated_skills"].append(entry.skill.id)
        report["resolved"].append(
            {
                "id": entry.skill.id,
                "version": entry.skill.version,
                "skill_md_chars": len(entry.body),
                "truncated": entry.truncated,
                "references": [
                    {
                        "path": reference["path"],
                        "chars": reference["chars"],
                        "reason": reference["reason"],
                    }
                    for reference in entry.references
                ],
                "references_skipped": list(entry.skipped),
                "bundled_unused": entry.skill.bundled_unused(),
            }
        )

    report["chars"] = len(rendered)
    return rendered, report
