"""Upload an exported dataset directory to a private Hugging Face dataset repository.

The token comes only from `HF_TOKEN` in the environment.
"""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import CommitOperationDelete, HfApi

TOKEN_ENV = "HF_TOKEN"
# Files the Hub manages itself, which an export never holds.
_HUB_FILES = {".gitattributes"}


def require_token() -> None:
    """Fail before any work when no Hugging Face token is set."""
    if not os.environ.get(TOKEN_ENV):
        raise RuntimeError(f"Set {TOKEN_ENV} to a Hugging Face write token to upload")


def upload_dataset(directory: Path, repo: str, api: HfApi | None = None) -> str:
    """Make the repository hold exactly `directory`, and return its URL.

    The repository is created private. Files from an earlier export that this
    one no longer holds are deleted; the repository's history keeps them.
    """
    require_token()
    api = api or HfApi()
    api.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)
    api.upload_large_folder(repo_id=repo, repo_type="dataset", folder_path=directory)

    local = {path.relative_to(directory).as_posix() for path in directory.rglob("*")}
    stale = [
        path
        for path in api.list_repo_files(repo, repo_type="dataset")
        if path not in local and path not in _HUB_FILES
    ]
    if stale:
        api.create_commit(
            repo,
            [CommitOperationDelete(path_in_repo=path) for path in stale],
            commit_message="Remove files the latest export no longer holds",
            repo_type="dataset",
        )
    return f"https://huggingface.co/datasets/{repo}"
