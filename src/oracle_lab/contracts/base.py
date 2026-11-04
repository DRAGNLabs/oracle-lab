"""Building blocks every persisted contract shares.

`ArtifactModel` is for files Oracle Lab writes or a container reports;
`StrictModel` is for what a model wrote, and refuses coercion too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from oracle_lab.io import write_json

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CommitId = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class ArtifactModel(BaseModel):
    """A file a run saves. Rejects fields it does not know, tolerates JSON numbers."""

    model_config = ConfigDict(extra="forbid")


class StrictModel(BaseModel):
    """A model-written contract: rejects undeclared fields and coerced types."""

    model_config = ConfigDict(extra="forbid", strict=True)


def write_result(path: Path, result: ArtifactModel) -> None:
    write_json(path, result.model_dump(mode="json"))
