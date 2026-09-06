"""The model registry: names you choose, mapped to models Rosetta can drive.

Rosetta never hosts a model. Every command that needs one shells out to
``opencode run --model <id>``, so "bringing your own model" means telling
Rosetta which id to pass and under what name you want to see it in a report.
That is the whole abstraction, and it is deliberately thin: a registry that
did more would be a second place for a model id to be wrong.

    rosetta model add local-qwen ollama/qwen2.5-coder:32b
    rosetta model list
    rosetta bench run --model local-qwen

Registered names and raw ids are interchangeable everywhere ``--model`` is
accepted -- :func:`resolve` passes an unknown value through untouched, so an
air-gapped operator who has not registered anything is never blocked by this
file.

The registry holds no credentials. OpenCode owns those, which keeps secrets
out of the repository and out of every trace Rosetta writes.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO_ROOT / "data" / "models.json"

# A name we are willing to print in a published report: no whitespace, no
# slashes, nothing that could be mistaken for a provider id.
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")

__all__ = [
    "Model",
    "RegistryError",
    "add",
    "load",
    "remove",
    "resolve",
    "save",
]


class RegistryError(RuntimeError):
    """The registry cannot answer, and guessing would corrupt a result."""


@dataclass(frozen=True)
class Model:
    """One model Rosetta can be pointed at.

    ``name`` is yours and appears in reports. ``model`` is the id OpenCode
    understands, in ``provider/model`` form.
    """

    name: str
    model: str
    notes: str = ""
    added: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def load(path: Path | None = None) -> list[Model]:
    """Every registered model, in the order they were added.

    A missing file is an empty registry, not an error: nothing in Rosetta
    requires a registered model.
    """
    p = path or DEFAULT_PATH
    if not p.exists():
        return []
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"{p} is not readable JSON: {exc}") from exc
    entries = doc.get("models") if isinstance(doc, dict) else doc
    if not isinstance(entries, list):
        raise RegistryError(f"{p} has no 'models' list")
    out: list[Model] = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("name") or not e.get("model"):
            raise RegistryError(f"{p} contains an entry without name and model: {e!r}")
        out.append(
            Model(
                name=str(e["name"]),
                model=str(e["model"]),
                notes=str(e.get("notes", "")),
                added=str(e.get("added", "")),
            )
        )
    return out


def save(models: Iterable[Model], path: Path | None = None) -> Path:
    p = path or DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"models": [m.to_dict() for m in models]}
    p.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return p


def add(
    name: str,
    model: str,
    notes: str = "",
    *,
    path: Path | None = None,
    replace: bool = False,
) -> Model:
    """Register ``name`` -> ``model``. Refuses to shadow an existing name.

    Silently overwriting would let two benchmark runs report the same name for
    two different models, which makes both numbers meaningless.
    """
    if not _NAME.match(name):
        raise RegistryError(
            f"{name!r} is not a usable name: lowercase letters, digits, "
            "'.', '_' and '-' only, up to 40 characters"
        )
    if "/" not in model:
        raise RegistryError(
            f"{model!r} does not look like an OpenCode model id; expected "
            "'provider/model', for example 'anthropic/claude-opus-5' or "
            "'ollama/qwen2.5-coder:32b'"
        )
    current = load(path)
    existing = {m.name for m in current}
    if name in existing and not replace:
        was = next(m.model for m in current if m.name == name)
        raise RegistryError(
            f"{name!r} is already registered as {was!r}; pass --replace to "
            "change it, or pick another name"
        )
    entry = Model(
        name=name,
        model=model,
        notes=notes,
        added=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    kept = [m for m in current if m.name != name]
    save([*kept, entry], path)
    return entry


def remove(name: str, *, path: Path | None = None) -> Model:
    current = load(path)
    hit = next((m for m in current if m.name == name), None)
    if hit is None:
        known = ", ".join(m.name for m in current) or "(none registered)"
        raise RegistryError(f"{name!r} is not registered. Known: {known}")
    save([m for m in current if m.name != name], path)
    return hit


def resolve(value: str | None, *, path: Path | None = None) -> str | None:
    """Registered name -> its model id. Anything else passes through.

    Pass-through is the point: ``--model ollama/qwen2.5-coder:32b`` has to work
    on a machine with no registry file at all.
    """
    if value is None:
        return None
    for m in load(path):
        if m.name == value:
            return m.model
    return value
