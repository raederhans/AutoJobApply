"""Execution body for the ApplyPilot doctor command."""

from __future__ import annotations

from types import ModuleType


def run_doctor(runtime: ModuleType) -> None:
    """Check your setup and diagnose missing requirements."""
    console = runtime.console
    os = runtime.os

    import importlib.metadata
    import shutil
    import sqlite3

    from applypilot.config import (
        APP_DIR,
        DB_PATH,
        PROFILE_PATH,
        RESUME_PATH,
        RESUME_PDF_PATH,
        SEARCH_CONFIG_PATH,
        get_chrome_path,
        load_env,
    )
    from applypilot.llm import TASK_TIERS, TIERS, _provider_credentials, resolve_model_route

    load_env()

    ok_mark = "[green]OK[/green]"
    fail_mark = "[red]MISSING[/red]"
    warn_mark = "[yellow]WARN[/yellow]"

    results: list[tuple[str, str, str]] = []  # (check, status, note)
    results.append(("Active workspace", ok_mark, str(APP_DIR)))

    # --- Tier 1 checks ---
    # Profile
    if PROFILE_PATH.exists():
        results.append(("profile.json", ok_mark, str(PROFILE_PATH)))
    else:
        results.append(("profile.json", fail_mark, "Run 'applypilot init' to create"))

    # Resume
    if RESUME_PATH.exists():
        results.append(("resume.txt", ok_mark, str(RESUME_PATH)))
    elif RESUME_PDF_PATH.exists():
        results.append(("resume.txt", warn_mark, "Only PDF found — plain-text needed for AI stages"))
    else:
        results.append(("resume.txt", fail_mark, "Run 'applypilot init' to add your resume"))

    # Search config
    if SEARCH_CONFIG_PATH.exists():
        results.append(("searches.yaml", ok_mark, str(SEARCH_CONFIG_PATH)))
    else:
        results.append(("searches.yaml", warn_mark, "Will use example config — run 'applypilot init'"))

    # Local database (read-only; never initializes, migrates, or repairs it).
    if not DB_PATH.is_file():
        results.append(("job database", warn_mark, "Not created yet — collect opportunities first"))
    else:
        from applypilot.view import collect_dashboard_data

        try:
            dashboard_data = collect_dashboard_data()
        except (OSError, sqlite3.DatabaseError) as exc:
            detail = f"{type(exc).__name__}: {exc}"[:120]
            results.append((
                "job database",
                fail_mark,
                f"{detail}; restore a known-good backup (automatic repair is disabled)",
            ))
        else:
            total = dashboard_data["stats"]["total"]
            results.append(("job database", ok_mark, f"Readable; {total} eligible roles"))

    # JobSpy is an optional capability rather than a core import dependency.
    try:
        from applypilot.optional_dependencies import require_jobboards

        require_jobboards()
        results.append(("python-jobspy", ok_mark, "Job board scraping available"))
    except RuntimeError as exc:
        results.append(("python-jobspy", warn_mark, str(exc)))

    # --- Tier 2 checks ---
    try:
        provider, base_url, _api_key = _provider_credentials(os.environ)
    except RuntimeError:
        results.append(("LLM API key", fail_mark,
                        "Set GEMINI_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY in the local .env"))
    else:
        labels = {"gemini": "Gemini", "openai": "OpenAI", "deepseek": "DeepSeek"}
        results.append(("LLM API key", ok_mark, labels.get(provider) or f"Local: {base_url}"))
        # Group tasks that resolve to the same chain so overrides stand out.
        groups: dict[tuple[str, tuple[str, ...], str], list[str]] = {}
        for task in sorted(TASK_TIERS, key=lambda name: (TIERS.index(TASK_TIERS[name]), name)):
            route = resolve_model_route(task)
            groups.setdefault((route.tier, route.models, route.source), []).append(task)
        for (tier, models, source), tasks in groups.items():
            results.append((f"LLM {tier} ({', '.join(tasks)})", ok_mark,
                            f"{' -> '.join(models)} (from {source})"))

    # --- Tier 3 checks ---
    # Selected browser-agent CLI (Codex by default)
    from applypilot.config import get_apply_backend, get_apply_backend_binary

    apply_backend = get_apply_backend()
    apply_backend_bin = get_apply_backend_binary()
    if apply_backend_bin:
        results.append((f"{apply_backend.title()} apply CLI", ok_mark, apply_backend_bin))
    else:
        results.append((
            f"{apply_backend.title()} apply CLI",
            fail_mark,
            f"Install or add {apply_backend} to PATH (needed for auto-apply)",
        ))

    # Chromium browser
    try:
        chrome_path = get_chrome_path()
        results.append(("Edge/Chrome/Chromium", ok_mark, chrome_path))
    except FileNotFoundError:
        results.append(("Edge/Chrome/Chromium", fail_mark,
                        "Install Edge/Chrome or set CHROME_PATH (needed for auto-apply)"))

    try:
        cloak_version = importlib.metadata.version("cloakbrowser")
        results.append((
            "CloakBrowser backend",
            ok_mark,
            f"wrapper {cloak_version}; binary is verified/resolved only when selected",
        ))
    except importlib.metadata.PackageNotFoundError:
        results.append((
            "CloakBrowser backend",
            "[dim]optional[/dim]",
            "Install applypilot-local[stealth] for the explicit anti-detection backend",
        ))

    # Node.js / npx (for Playwright MCP)
    npx_bin = shutil.which("npx")
    if npx_bin:
        results.append(("Node.js (npx)", ok_mark, npx_bin))
    else:
        results.append(("Node.js (npx)", fail_mark,
                        "Install Node.js 18+ from nodejs.org (needed for auto-apply)"))

    # External CAPTCHA solver configuration is intentionally inactive.
    capsolver = os.environ.get("CAPSOLVER_API_KEY")
    if capsolver:
        results.append((
            "External CAPTCHA solver",
            "[yellow]inactive[/yellow]",
            "CAPSOLVER_API_KEY is present but Job Apply Pilot does not call solvers or inject tokens",
        ))
    else:
        results.append((
            "External CAPTCHA solver",
            "[dim]disabled[/dim]",
            "Transient challenges are re-observed; persistent visible gates require the applicant",
        ))

    # --- Render results ---
    console.print()
    console.print("[bold]Job Apply Pilot Doctor[/bold]\n")

    col_w = max(len(r[0]) for r in results) + 2
    for check, status, note in results:
        pad = " " * (col_w - len(check))
        console.print(f"  {check}{pad}{status}  [dim]{note}[/dim]")

    console.print()
    # Tier summary
    from applypilot.config import TIER_LABELS, get_apply_backend, get_tier
    tier = get_tier()
    console.print(f"[bold]Current tier: Tier {tier} — {TIER_LABELS[tier]}[/bold]")

    if tier == 1:
        console.print("[dim]  → Tier 2 unlocks: scoring, tailoring, cover letters (needs LLM API key)[/dim]")
        console.print(
            f"[dim]  → Tier 3 unlocks: auto-apply (needs {get_apply_backend()} CLI + "
            "Chrome + Node.js)[/dim]"
        )
    elif tier == 2:
        console.print(
            f"[dim]  → Tier 3 unlocks: auto-apply (needs {get_apply_backend()} CLI + "
            "Chrome + Node.js)[/dim]"
        )

    console.print()
