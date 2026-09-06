"""Read-only container source access, kept inside the runtime boundary."""

from __future__ import annotations

import os
import subprocess
import io
import shlex
import tarfile
from pathlib import Path


def container_files(container: str, args: list[str], timeout_s: float = 20.0) -> str:
    """List or read files in an explicitly selected corpus container."""
    if not container or container.startswith("-"):
        raise ValueError("a valid explicit container name is required")
    if not args or args[0] not in {"ls", "cat"}:
        raise ValueError("source access only supports ls and cat")
    proc = subprocess.run(
        [os.environ.get("ROSETTA_DOCKER", "docker"), "exec", container, *args],
        capture_output=True, text=True, timeout=timeout_s,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip()[:300] or "container source read failed")
    return proc.stdout


def extract_sources(dest: Path, container: str, routine_dir: str) -> int:
    """Export regular .m files from an explicit container without extracting paths."""
    if not container or container.startswith("-") or not routine_dir.startswith("/"):
        raise ValueError("an explicit container and absolute routine directory are required")
    command = (
        f"cd {shlex.quote(routine_dir)} && "
        "find . -maxdepth 1 -name '*.m' -type f -printf '%f\\0' | tar --null -T - -cf -"
    )
    archive = subprocess.run(
        [os.environ.get("ROSETTA_DOCKER", "docker"), "exec", container,
         "bash", "-o", "pipefail", "-c", command],
        capture_output=True, check=True, timeout=180,
    ).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
        for member in bundle.getmembers():
            if not member.isfile() or Path(member.name).name != member.name or not member.name.endswith(".m"):
                raise ValueError(f"unexpected source archive member: {member.name!r}")
            target = dest / member.name
            if target.is_symlink():
                raise ValueError(f"refusing to overwrite symlink: {target}")
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError(f"missing source archive member: {member.name!r}")
            target.write_bytes(stream.read())
    return len(list(dest.glob("*.m")))
