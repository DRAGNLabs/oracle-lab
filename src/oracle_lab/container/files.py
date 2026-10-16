"""File transfers over the Docker SDK's tar interface, scrubbing secrets on the way out."""

from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from docker.models.containers import Container


class RedactedOutput:
    def __init__(self, path: Path, secrets: tuple[str, ...] = (), *, append: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("ab" if append else "wb")
        self.secrets = tuple(value.encode() for value in secrets if value)
        self.pending = b""

    def write(self, chunk: bytes) -> None:
        # Keep a possible secret prefix across SDK chunks. Replacing each chunk
        # independently would leak credentials split across network frames.
        data = self.pending + chunk
        for secret in self.secrets:
            data = data.replace(secret, b"[REDACTED]")
        keep = 0
        for secret in self.secrets:
            for size in range(1, min(len(secret), len(data) + 1)):
                if data.endswith(secret[:size]):
                    keep = max(keep, size)
        self.pending = data[-keep:] if keep else b""
        self.stream.write(data[:-keep] if keep else data)
        self.stream.flush()

    def close(self) -> None:
        self.stream.write(self.pending)
        self.stream.close()

    def __enter__(self) -> RedactedOutput:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def upload(container: Container, source: Path, destination: str, *, contents: bool = False) -> None:
    target = PurePosixPath(destination)
    with tempfile.TemporaryFile() as buffer:
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            if contents:
                for child in sorted(source.iterdir()):
                    archive.add(child, arcname=child.name)
                parent = str(target)
            else:
                archive.add(source, arcname=target.name)
                parent = str(target.parent)
        buffer.seek(0)
        container.put_archive(parent, buffer)


def download(
    container: Container,
    source: str,
    destination: Path,
    *,
    contents: bool = False,
    secrets: tuple[str, ...] = (),
) -> None:
    """Export regular files only; never let container paths choose host targets."""
    chunks, metadata = container.get_archive(source)
    # Docker puts the archive's root directory name in here. Only used when
    # downloading a whole directory, to reject entries from anywhere else.
    archive_root = metadata["name"] if contents and metadata else None
    with tempfile.TemporaryFile() as buffer:
        for chunk in chunks:
            buffer.write(chunk)
        buffer.seek(0)
        with tarfile.open(fileobj=buffer) as archive:
            members = archive.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError(f"Unsafe container archive entry: {member.name}")
                if contents and (not path.parts or path.parts[0] != archive_root):
                    raise ValueError(f"Unexpected container archive root: {member.name}")
            files = [member for member in members if member.isfile()]
            if not contents and (len(files) != 1 or len(members) != 1):
                raise ValueError("Expected exactly one regular file in container archive")
            for member in files:
                # Docker archives include the requested directory as their root.
                relative = PurePosixPath(member.name).parts[1:]
                target = destination.joinpath(*relative) if contents else destination
                if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
                    raise ValueError(f"Refusing to overwrite a host symlink: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"Unreadable container archive entry: {member.name}")
                with stream, RedactedOutput(target, secrets) as out:
                    while chunk := stream.read(65536):
                        out.write(chunk)
                target.chmod(member.mode & 0o777)
