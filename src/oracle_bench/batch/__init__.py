"""Sequential, resumable orchestration of explicit run configurations."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Callable

import yaml
from pydantic import Field, model_validator

from oracle_bench.batch.summary import write_summary
from oracle_bench.config import ConfigModel, NonBlank, RunConfig, load_config
from oracle_bench.container.lifecycle import preserve_failure
from oracle_bench.io import digest, read_json, write_json
from oracle_bench.run import run


class BatchJob(ConfigModel):
    """A reference to one independently runnable experiment configuration."""

    config: str


class BatchExecution(ConfigModel):
    """Jobs always run one at a time; this only says whether a failure stops the batch."""

    continue_on_error: Annotated[bool, Field(strict=True)] = True


class BatchConfig(ConfigModel):
    name: NonBlank
    jobs: Annotated[list[BatchJob], Field(min_length=1)]
    execution: BatchExecution = Field(default_factory=BatchExecution)
    output: str = "batches"

    @model_validator(mode="after")
    def unique_configs(self) -> BatchConfig:
        configs = [job.config for job in self.jobs]
        if len(configs) != len(set(configs)):
            raise ValueError("Batch job config paths must be unique")
        return self


def load_batch_config(path: Path) -> tuple[BatchConfig, list[Path]]:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Batch configuration must be a YAML mapping")
    config = BatchConfig.model_validate(raw)
    base = path.resolve().parent
    configs = [(base / job.config).resolve() for job in config.jobs]
    for job_path in configs:
        # Validate every job before the first paid model call.
        load_config(job_path)
    config.output = str((base / config.output).resolve())
    return config, configs


def _write_manifest(batch_dir: Path, config: BatchConfig, jobs: list[dict]) -> None:
    write_json(
        batch_dir / "batch.resolved.json",
        {"name": config.name, "execution": config.execution.model_dump(), "jobs": jobs},
    )


def _new_batch(path: Path) -> tuple[Path, BatchConfig, list[dict]]:
    config, paths = load_batch_config(path)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    batch_dir = Path(config.output) / batch_id
    batch_dir.mkdir(parents=True)
    shutil.copyfile(path, batch_dir / "batch.input.yaml")
    jobs = [
        {
            "index": index,
            "config": str(job_path),
            "config_sha256": digest(job_path.read_bytes()),
            "state": "pending",
            "run_dir": None,
            "error": None,
        }
        for index, job_path in enumerate(paths, start=1)
    ]
    _write_manifest(batch_dir, config, jobs)
    return batch_dir, config, jobs


def _resume_batch(batch_dir: Path) -> tuple[Path, BatchConfig, list[dict]]:
    batch_dir = batch_dir.resolve()
    resolved = read_json(batch_dir / "batch.resolved.json")
    config = BatchConfig(
        name=resolved["name"],
        jobs=[BatchJob(config=job["config"]) for job in resolved["jobs"]],
        execution=resolved["execution"],
        output=str(batch_dir.parent),
    )
    jobs = resolved["jobs"]
    for job in jobs:
        path = Path(job["config"])
        if digest(path.read_bytes()) != job["config_sha256"]:
            raise ValueError(f"Cannot resume: job config changed: {path}")
    return batch_dir, config, jobs


def run_batch(
    path: Path,
    *,
    resume: bool = False,
    run_one: Callable[[RunConfig], Path] = run,
) -> Path:
    """Run pending jobs in order, persisting progress after every transition."""
    batch_dir, config, jobs = _resume_batch(path) if resume else _new_batch(path)
    write_json(batch_dir / "status.json", {"state": "running"})
    try:
        for job in jobs:
            if job["state"] in {"completed", "completed_with_errors"}:
                continue
            job.update(state="running", error=None)
            _write_manifest(batch_dir, config, jobs)
            try:
                run_dir = run_one(load_config(Path(job["config"])))
                job["run_dir"] = str(run_dir)
                job["state"] = read_json(run_dir / "status.json")["state"]
            except KeyboardInterrupt:
                job["state"] = "interrupted"
                raise
            except Exception as exc:
                job.update(state="failed", error=str(exc))
                if not config.execution.continue_on_error:
                    raise
            finally:
                # A summary that cannot be written must not hide why the job stopped.
                with preserve_failure("The batch summary could not be updated."):
                    _write_manifest(batch_dir, config, jobs)
                    write_summary(batch_dir, config.name, jobs)
    except KeyboardInterrupt:
        write_json(batch_dir / "status.json", {"state": "interrupted"})
        raise
    except Exception:
        write_json(batch_dir / "status.json", {"state": "failed"})
        raise

    final_state = (
        "completed" if all(job["state"] == "completed" for job in jobs) else "completed_with_errors"
    )
    write_json(batch_dir / "status.json", {"state": final_state})
    return batch_dir
