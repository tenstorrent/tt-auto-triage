from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .helpers import gh, log

WORKFLOW_FILE = "aggregate-workflow-data.yaml"
ARTIFACT_NAME = "workflow-data"


def download_workflow_data(target_repo: str) -> list[list[Any]]:
    log("Finding latest aggregate-workflow-data run...")
    raw = gh(
        "api",
        f"repos/{target_repo}/actions/workflows/{WORKFLOW_FILE}/runs?status=success&per_page=100",
    )
    runs = json.loads(raw).get("workflow_runs", [])
    if not runs:
        raise RuntimeError("No successful aggregate-workflow-data runs found")
    runs.sort(key=lambda run: run.get("created_at", ""), reverse=True)
    successful_run = None
    for run in runs:
        if run.get("conclusion") != "success":
            continue
        run_id = run["id"]
        try:
            artifacts = json.loads(
                gh("api", f"repos/{target_repo}/actions/runs/{run_id}/artifacts?per_page=100")
            ).get("artifacts", [])
        except RuntimeError as exc:
            log(f"  Could not inspect artifacts for run {run_id}: {exc}")
            continue
        if any(
            artifact.get("name") == ARTIFACT_NAME and artifact.get("expired") is False
            for artifact in artifacts
        ):
            successful_run = run
            break
        log(f"  Skipping successful run {run_id}: {ARTIFACT_NAME} is unavailable")
    if successful_run is None:
        raise RuntimeError("No successful aggregate-workflow-data run has an available artifact")
    run_id = successful_run["id"]
    log(f"  Latest successful run: {run_id}")

    with tempfile.TemporaryDirectory() as tmpdir:
        gh(
            "run",
            "download",
            str(run_id),
            f"--repo={target_repo}",
            "-n",
            ARTIFACT_NAME,
            "-D",
            tmpdir,
            timeout=120,
        )
        json_file = Path(tmpdir) / "workflow-data.json"
        if not json_file.exists():
            candidates = list(Path(tmpdir).rglob("*.json"))
            if not candidates:
                raise RuntimeError("No JSON files in downloaded artifact")
            json_file = candidates[0]
        log(f"  Loaded workflow data ({json_file.stat().st_size / 1_000_000:.1f} MB)")
        return json.loads(json_file.read_text())
