"""Opt-in, read-only discovery of installed Codex and Claude skills."""

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

from . import skills
from .storage import _write_json_file

ROOTS = {name: Path.home() / f".{name}" / "skills" for name in ("codex", "claude")}


def settings() -> dict[str, bool]:
    try:
        payload = json.loads((_settings_path()).read_text())
        return {name: payload.get(name) is True for name in ROOTS}
    except (OSError, ValueError, AttributeError):
        return dict.fromkeys(ROOTS, False)


def _settings_path() -> Path:
    return skills._SKILLS_DIR / ".sources.json"


def configure(codex: bool, claude: bool) -> dict[str, bool]:
    with skills._skills_lock:
        _write_json_file(str(_settings_path()), {"codex": codex, "claude": claude})
        refresh()
        return settings()


def is_available(skill: skills.Skill) -> bool:
    return skill.source == "uploaded" or (
        settings().get(skill.source, False)
        and (Path(skill.source_path) / "SKILL.md").is_file()
    )


def archive_directory(root: Path) -> bytes:
    """Follow the installed skill's root symlink, never links inside its bundle."""
    root = root.resolve(strict=True)
    files = [root / "SKILL.md"]
    references = root / "references"
    if references.is_dir() and not references.is_symlink():
        for directory, directories, names in os.walk(references, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not (Path(directory) / name).is_symlink()
            ]
            files.extend(
                Path(directory) / name for name in names if name.endswith(".md")
            )
            if len(files) > skills.SKILLS_MAX_ENTRIES:
                raise skills.SkillError("Local skill has too many files.")
    archive = io.BytesIO()
    used = 0
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in sorted(files):
            if path.is_symlink() or not path.is_file():
                raise skills.SkillError(
                    "Local skill contains a linked or special file."
                )
            with path.open("rb") as handle:
                data = handle.read(skills.SKILLS_MAX_UNCOMPRESSED_BYTES - used + 1)
            used += len(data)
            if used > skills.SKILLS_MAX_UNCOMPRESSED_BYTES:
                raise skills.SkillError("Local skill is too large.")
            # Fixed ZIP timestamps make unchanged local bundles cheap to detect.
            bundle.writestr(zipfile.ZipInfo(path.relative_to(root).as_posix()), data)
    return archive.getvalue()


def refresh() -> None:
    """Cache prompt-readable files; uploaded names win, then Codex, then Claude."""
    enabled = settings()
    if not any(enabled.values()):
        return
    with skills._skills_lock:
        seen: set[str] = set()
        for source, root in ROOTS.items():
            if not enabled[source]:
                continue
            try:
                directories = sorted(root.iterdir())
            except OSError:
                continue
            for directory in directories:
                if (
                    directory.name.startswith(".")
                    or not (directory / "SKILL.md").is_file()
                ):
                    continue
                try:
                    data = archive_directory(directory)
                    files = skills.inspect_archive(data)
                    skill_id, *_ = skills._read_frontmatter_fields(
                        skills._decode_text(files["SKILL.md"])
                    )
                    if skill_id in seen:
                        continue
                    seen.add(skill_id)
                    existing = skills._load_skill_dir(skills._SKILLS_DIR / skill_id)
                    if existing is not None and (
                        existing.source == "uploaded"
                        or (
                            existing.source == source
                            and existing.source_path == str(directory)
                            and existing.archive_sha256
                            == hashlib.sha256(data).hexdigest()
                        )
                    ):
                        continue
                    skills.install_skill(
                        data, replace=True, _source=source, _origin=str(directory)
                    )
                except (OSError, ValueError, RuntimeError):
                    # One broken installation must not hide the rest of the library.
                    continue
