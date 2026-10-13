"""Local comparison command body, loaded after the workspace callback."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path


def run(*, job_ids: Sequence[str], output_dir: Path | None, list_jobs: bool, query: str, limit: int) -> dict:
    from applypilot.job_comparison import export_comparison, list_comparison_jobs

    configured = os.environ.get("APPLYPILOT_DIR")
    workspace = Path(configured) if configured else Path.home() / ".applypilot"
    if list_jobs:
        if job_ids or output_dir is not None:
            raise ValueError("Use --list separately from --job-id and --output-dir")
        return list_comparison_jobs(workspace, query=query, limit=limit)
    if output_dir is None or not job_ids:
        raise ValueError("Use --list to select IDs, then provide repeated --job-id and --output-dir")
    if query:
        raise ValueError("query is a list-only option; comparison selects exact job IDs")
    return export_comparison(workspace, output_dir, job_ids=job_ids)
