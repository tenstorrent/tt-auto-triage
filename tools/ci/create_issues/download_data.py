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
        "run",
        "list",
        f"--workflow={WORKFLOW_FILE}",
        f"--repo={target_repo}",
        "--status=completed",
        "--limit=100",
        "--json=databaseId,conclusion",
    )
    runs = json.loads(raw)
    if not runs:
        raise RuntimeError("No completed aggregate-workflow-data runs found")
    successful_run = None
    # gh returns runs newest-first; use the first successful run whose artifact is still available.
    for run in runs:
        if run.get("conclusion") != "success":
            continue
        run_id = run["databaseId"]
        try:
            artifacts = json.loads(
                gh("api", f"repos/{target_repo}/actions/runs/{run_id}/artifacts?per_page=100")
            ).get("artifacts", [])
        except RuntimeError as exc:
            log(f"  Could not inspect artifacts for run {run_id}: {exc}")
            continue
        if any(
            artifact.get("name") == ARTIFACT_NAME and not artifact.get("expired")
            for artifact in artifacts
        ):
            successful_run = run
            break
        log(f"  Skipping successful run {run_id}: {ARTIFACT_NAME} is unavailable")
    if successful_run is None:
        raise RuntimeError("No successful aggregate-workflow-data run has an available artifact")
    run_id = successful_run["databaseId"]
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
