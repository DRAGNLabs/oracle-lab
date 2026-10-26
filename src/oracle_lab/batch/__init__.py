"""Sequential, resumable orchestration of explicit run configurations."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Callable

import yaml
from pydantic import Field, model_validator

from oracle_lab.batch.summary import write_summary
from oracle_lab.config import ConfigModel, NonBlank, RunConfig, load_config
from oracle_lab.container.lifecycle import preserve_failure
from oracle_lab.contracts.run import read_status
from oracle_lab.io import digest, read_json, write_json
from oracle_lab.paths import BatchPaths, OutputPaths, RunPaths
from oracle_lab.run import run


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

    @model_validator(mode="after")
    def unique_configs(self) -> BatchConfig:
        configs = [job.config for job in self.jobs]
        if len(configs) != len(set(configs)):
            raise ValueError("Batch job config paths must be unique")
        return self


def load_batch_config(path: Path) -> tuple[BatchConfig, list[Path], OutputPaths]:
    """The batch, its job config paths, and the output root they all share.

    The batch directory is saved beside its runs, so every job must name the same
    output root.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Batch configuration must be a YAML mapping")
    config = BatchConfig.model_validate(raw)
    base = path.resolve().parent
    configs = [(base / job.config).resolve() for job in config.jobs]
    # Validate every job before the first paid model call.
    outputs = {load_config(job_path).output for job_path in configs}
    if len(outputs) != 1:
        raise ValueError(f"Batch jobs must share one output root, not {sorted(outputs)}")
    return config, configs, OutputPaths(Path(outputs.pop()))


def _write_manifest(batch: BatchPaths, config: BatchConfig, jobs: list[dict]) -> None:
    write_json(
        batch.manifest,
        {"name": config.name, "execution": config.execution.model_dump(), "jobs": jobs},
    )


def _new_batch(path: Path) -> tuple[BatchPaths, BatchConfig, list[dict]]:
    config, paths, output = load_batch_config(path)
    batch = output.new_batch()
    shutil.copyfile(path, batch.input)
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
    _write_manifest(batch, config, jobs)
    return batch, config, jobs


def _resume_batch(batch_dir: Path) -> tuple[BatchPaths, BatchConfig, list[dict]]:
    batch = BatchPaths(batch_dir.resolve())
    resolved = read_json(batch.manifest)
    config = BatchConfig(
        name=resolved["name"],
        jobs=[BatchJob(config=job["config"]) for job in resolved["jobs"]],
        execution=resolved["execution"],
    )
    jobs = resolved["jobs"]
    for job in jobs:
        path = Path(job["config"])
        if digest(path.read_bytes()) != job["config_sha256"]:
            raise ValueError(f"Cannot resume: job config changed: {path}")
    return batch, config, jobs


def run_batch(
    path: Path,
    *,
    resume: bool = False,
    run_one: Callable[[RunConfig], Path] = run,
) -> Path:
    """Run pending jobs in order, persisting progress after every transition."""
    batch, config, jobs = _resume_batch(path) if resume else _new_batch(path)
    write_json(batch.status, {"state": "running"})
    try:
        for job in jobs:
            if job["state"] in {"completed", "completed_with_errors"}:
                continue
            job.update(state="running", error=None)
            _write_manifest(batch, config, jobs)
            try:
                run_dir = run_one(load_config(Path(job["config"])))
                job["run_dir"] = str(run_dir)
                job["state"] = read_status(RunPaths(run_dir).status).state
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
                    _write_manifest(batch, config, jobs)
                    write_summary(batch, config.name, jobs)
    except KeyboardInterrupt:
        write_json(batch.status, {"state": "interrupted"})
        raise
    except Exception:
        write_json(batch.status, {"state": "failed"})
        raise

    final_state = (
        "completed" if all(job["state"] == "completed" for job in jobs) else "completed_with_errors"
    )
    write_json(batch.status, {"state": final_state})
    return batch.root
