"""Optional observation-format interoperability; no SDK or cloud startup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Validate external observations and use a supervised prepare host.", no_args_is_help=True)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


@app.command("plan")
def plan(
    actions: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    bindings: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    facts: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    observation: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="Saved host bridge response JSON.")],
    url: Annotated[str, typer.Option(help="Exact application page URL.")],
) -> None:
    """Read-only check. Printed plan omits values and is not execution authority."""
    from applypilot.apply.observation_adapter import build_prepare_plan, observation_payload

    try:
        result = build_prepare_plan(_read(actions), _read(bindings), observation_payload(_read(observation))["form_state"],
                                    _read(facts), page_url=url)
    except (OSError, ValueError, TypeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps({"fields": result["review"], "submit_authority": False, "live_revalidation_required": True}))


@app.command("execute")
def execute(
    actions: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    bindings: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    facts: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    bridge_dir: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    url: Annotated[str, typer.Option(help="Exact application page URL.")],
    timeout: Annotated[float, typer.Option(min=1, max=120)] = 45,
) -> None:
    """Re-observe and fill at most four routine fields through the existing host."""
    from applypilot.apply.observation_adapter import execute_prepare
    from applypilot.apply.visual_bridge import VisualBridgeError

    try:
        result = execute_prepare(bridge_dir, _read(actions), _read(bindings), _read(facts),
                                 page_url=url, timeout_seconds=timeout)
    except VisualBridgeError as exc:
        typer.echo(json.dumps({"status": exc.code, "outcome": exc.outcome, "submit_authority": False,
                              "automatic_retry": False}))
        raise typer.Exit(1) from exc
    except (OSError, ValueError, TypeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result))
    if result["status"] != "prepared":
        raise typer.Exit(1)
