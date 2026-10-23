"""ATS application context and relay admission for one apply attempt.

Decides whether credential/identity relays and the runtime route are allowed,
derives the bound ATS target URLs, and builds the redacted context consumed by
the ATS tools MCP server, including answer-provenance and plan state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlparse

from applypilot.apply import answer_provenance as answer_provenance_mod
from applypilot.apply import application_plan as application_plan_mod
from applypilot.apply import application_plan_runtime as application_plan_runtime_mod
from applypilot.apply import ats as ats_mod
from applypilot.apply import submission_surfaces as submission_surfaces_mod
from applypilot.apply import successfactors_binding as successfactors_binding_mod
from applypilot.apply.answer_policy import field_risk
from applypilot.apply.application_facts import (
    current_profile_facts,
    current_visible_fact_mappings,
    resolve_application_fact_ref,
)
from applypilot.apply.authentication_policy import authentication_capability


def _credential_relay_allowed(profile: dict, job: dict) -> bool:
    return bool(
        authentication_capability(profile, "credential_relay_authorized")
        and job.get("_browser_backend") != "cloak"
    )


def _runtime_application_route(
    job: Mapping[str, object],
    *,
    submission_phase: str,
    direct_email_send_authorized: bool,
) -> str:
    """Keep email-only preparation off the browser tool surface."""
    if submission_phase == "receipt":
        return "receipt_mailbox"
    if direct_email_send_authorized:
        return "direct_email"
    if (
        submission_phase == "prepare"
        and submission_surfaces_mod.classify_submission_surface(job)
        == "official_direct_email"
    ):
        return "direct_email"
    return "browser"


def _identity_relay_allowed(profile: dict, job: dict) -> bool:
    identity = profile.get("identity_materials", {})
    fin_policy = identity.get("fin", {}) if isinstance(identity, Mapping) else {}
    return bool(
        isinstance(fin_policy, Mapping)
        and fin_policy.get("secure_relay_authorized") is True
        and job.get("_browser_backend") != "cloak"
    )


def _safe_ats_target_url(job: Mapping[str, object]) -> str:
    """Return a query-free application target suitable for Agent context."""
    raw = str(job.get("application_url") or job.get("url") or "")
    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").casefold()
    if not hostname:
        return ""
    port = f":{parsed.port}" if parsed.port else ""
    scheme = parsed.scheme if parsed.scheme in {"http", "https"} else "https"
    return f"{scheme}://{hostname}{port}{parsed.path or '/'}"


def _credential_application_id(job: Mapping[str, object]) -> str:
    """Derive an opaque, stable identity for this exact selected posting."""
    payload = {
        "url": str(job.get("url") or ""),
        "application_url": str(job.get("application_url") or ""),
        "canonical_job_url": str(job.get("canonical_job_url") or ""),
        "platform_job_id": str(job.get("platform_job_id") or ""),
        "provider_application_id": str(job.get("provider_application_id") or ""),
        "requisition_id": str(job.get("requisition_id") or ""),
        "company": str(job.get("company_name") or ""),
        "title": str(job.get("title") or ""),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _credential_target_urls(
    job: Mapping[str, object], *, attempt_id: str
) -> list[str]:
    """Return only launcher-selected exact application routes for this attempt."""
    primary_target = job.get("application_url") or job.get("url")
    candidates: list[object] = [primary_target, job.get("_discovered_application_url")]
    runtime_binding = job.get("_linkedin_runtime_route_binding")
    if (
        isinstance(runtime_binding, Mapping)
        and str(runtime_binding.get("attempt_id") or "") == attempt_id
        and runtime_binding.get("lineage_verified") is True
    ):
        candidates.append(runtime_binding.get("target_application_url"))
    provider_binding = job.get("_ats_application_binding")
    if successfactors_binding_mod.successfactors_binding_is_resolved(
        provider_binding
    ):
        candidates.append(
            provider_binding.get("job_application_url")
        )
    routes: set[str] = set()
    identity_query_keys = {
        "career_job_req_id",
        "career_ns",
        "company",
        "gh_jid",
        "job",
        "job_id",
        "jobid",
        "posting_id",
        "postingid",
        "reqid",
        "requisition_id",
        "requisitionid",
    }
    for candidate in candidates:
        raw = str(candidate or "").strip()
        if not raw:
            continue
        safe = _safe_ats_target_url({"application_url": raw})
        if safe:
            parsed = urlparse(raw)
            identity_query = sorted(
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.casefold() in identity_query_keys
            )
            routes.add(
                f"{safe}?{urlencode(identity_query)}" if identity_query else safe
            )
    return sorted(routes)


def _available_semantic_fact_names(profile: Mapping[str, object], job: Mapping[str, object]) -> list[str]:
    """List confirmed semantic source names without copying applicant values."""
    facts: set[str] = set()
    personal = profile.get("personal")
    personal = personal if isinstance(personal, Mapping) else {}
    if personal.get("full_name"):
        facts.update({"full_name", "first_name", "last_name"})
    semantic_personal = {
        "email": ("email",),
        "phone": ("phone", "phone_number"),
        "linkedin": ("linkedin", "linkedin_url"),
        "website": ("website", "portfolio", "github", "github_url"),
        "location": ("location", "city", "country", "address"),
    }
    for semantic, keys in semantic_personal.items():
        if any(personal.get(key) for key in keys):
            facts.add(semantic)
    if job.get("tailored_resume_path") or job.get("_staged_resume_path"):
        facts.add("resume")
    if job.get("cover_letter_path") or job.get("_staged_cover_letter_path"):
        facts.add("cover_letter")
    if isinstance(profile.get("work_authorization"), Mapping):
        facts.update({"work_authorization", "sponsorship"})
    application_facts = current_visible_fact_mappings(profile)
    if isinstance(application_facts, list):
        for fact in application_facts:
            if not isinstance(fact, Mapping):
                continue
            key = str(fact.get("key") or "").strip().casefold()
            if key and len(key) <= 120 and re.fullmatch(r"[a-z0-9_.-]+", key):
                facts.add(key)
    return sorted(facts)


def _install_answer_provenance_context(job: dict) -> dict[str, object]:
    """Install one host-owned, non-authorizing provenance binding."""

    binding = answer_provenance_mod.build_host_provenance_binding(job)
    job["_answer_provenance_binding"] = binding
    return answer_provenance_mod.public_provenance_context(binding)


def _install_application_plan_context(
    job: dict,
    profile: Mapping[str, object],
    *,
    runtime_route: str,
) -> application_plan_mod.ApplicationPlan:
    """Install one host-built, ref-only plan without adding effect authority."""

    current = job.get("_application_plan")
    if (
        isinstance(current, application_plan_mod.ApplicationPlan)
        and not current.plan_id.startswith("host-application-plan:")
    ):
        # Preserve the established injected ref-only contract used by callers
        # and shadow compatibility tests. Production jobs do not carry one;
        # only plans created here use the reserved host-owned prefix.
        return current
    previous = (
        current if isinstance(current, application_plan_mod.ApplicationPlan) else None
    )
    plan = application_plan_runtime_mod.build_host_application_plan(
        job,
        profile,
        runtime_route=runtime_route,
        previous=previous,
    )
    if previous is None:
        job.pop("_previous_application_plan", None)
    elif plan.digest != previous.digest:
        job["_previous_application_plan"] = previous
    job["_application_plan"] = plan
    return plan


def _host_staged_field_risk(field: Mapping[str, object]) -> str:
    """Derive risk from host-observed field state, never caller metadata."""

    label = str(field.get("label") or field.get("text") or "")
    semantic = str(field.get("semantic") or "unknown")
    control = str(field.get("control") or "").casefold()
    declaration = control == "checkbox" or bool(
        re.search(
            r"\b(?:declare|declaration|attest|attestation|certify|certification|"
            r"acknowledge|consent|terms and conditions)\b",
            label,
            re.IGNORECASE,
        )
    )
    staged_adapter_risk = field.get("risk")
    adapter_risk = (
        staged_adapter_risk
        if staged_adapter_risk in {"low", "medium", "high"}
        else None
    )
    return field_risk(
        f"{label} {semantic}",
        adapter_risk=adapter_risk,
        direct_impact=field.get("direct_impact") is True,
        declaration=declaration,
    )


def _build_ats_application_context(
    job: Mapping[str, object],
    profile: Mapping[str, object],
    *,
    attempt_id: str = "",
) -> dict[str, object]:
    """Build the bounded read/proposal-only context shared with ATS tools."""
    target_url = _safe_ats_target_url(job)
    adapter = ats_mod.detect_ats_site(target_url)
    context: dict[str, object] = {
        "schema_version": ats_mod.ATS_SCHEMA_VERSION,
        "adapter": adapter,
        "target_url": target_url,
        "guidance": list(ats_mod.adapter_prompt_guidance(target_url)),
        "available_fact_names": _available_semantic_fact_names(profile, job),
        "side_effect": "proposal-only",
    }
    provenance_binding = job.get("_answer_provenance_binding")
    if isinstance(provenance_binding, Mapping):
        recomputed = answer_provenance_mod.build_host_provenance_binding(job)
        if dict(provenance_binding) != recomputed:
            raise ValueError("answer provenance binding drifted before context staging")
        context["answer_provenance"] = answer_provenance_mod.public_provenance_context(
            recomputed
        )
        context["_trusted_fact_scopes"] = list(recomputed["fact_scopes"])
        trusted_scopes = set(recomputed["fact_scopes"])
        available_fact_refs: list[dict[str, str]] = []
        for fact in current_profile_facts(profile):
            if fact.scope not in trusted_scopes:
                continue
            resolution = resolve_application_fact_ref(
                current_profile_facts(profile),
                fact_ref=fact.fact_ref,
                scope=str(fact.scope),
                minimum_sensitivity=fact.sensitivity,
            )
            if not resolution.production_ready:
                continue
            available_fact_refs.append(
                {
                    "key": fact.key[:120],
                    "fact_ref": fact.fact_ref[:160],
                    "sensitivity": fact.sensitivity,
                }
            )
            if len(available_fact_refs) >= 200:
                break
        context["available_fact_refs"] = available_fact_refs
    if attempt_id:
        provider_binding = job.get("_ats_application_binding")
        context["credential_binding"] = {
            "schema_version": "1",
            "attempt_id": attempt_id,
            "application_id": _credential_application_id(job),
            "target_urls": _credential_target_urls(job, attempt_id=attempt_id),
            "provider_binding": (
                dict(provider_binding)
                if isinstance(provider_binding, Mapping)
                else {}
            ),
        }
    observation = job.get("_browser_observation")
    if isinstance(observation, Mapping):
        form_context = observation.get("ats_adapter_context")
        if isinstance(form_context, Mapping):
            safe_form = dict(form_context)
            raw_fields = safe_form.get("fields")
            if isinstance(raw_fields, list):
                safe_fields: list[dict[str, object]] = []
                for raw_field in raw_fields:
                    if not isinstance(raw_field, Mapping):
                        continue
                    safe_field = {
                        key: value
                        for key, value in raw_field.items()
                        if key not in {"options", "options_truncated"}
                    }
                    safe_field["risk"] = _host_staged_field_risk(raw_field)
                    full_digest = raw_field.get("options_full_sha256")
                    source_count = raw_field.get("options_source_count")
                    source_truncated = raw_field.get("options_source_truncated")
                    if not isinstance(full_digest, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", full_digest
                    ):
                        legacy_options = raw_field.get("options")
                        declared_count = raw_field.get("option_count")
                        count_matches = declared_count is None or (
                            isinstance(declared_count, int)
                            and not isinstance(declared_count, bool)
                            and isinstance(legacy_options, list)
                            and declared_count == len(legacy_options)
                        )
                        if (
                            isinstance(legacy_options, list)
                            and raw_field.get("options_truncated") is False
                            and count_matches
                        ):
                            full_digest = hashlib.sha256(
                                json.dumps(
                                    legacy_options,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest()
                            source_count = len(legacy_options)
                            source_truncated = False
                        else:
                            full_digest = None
                            source_count = declared_count
                            source_truncated = True
                    safe_field["options_sha256"] = full_digest
                    safe_field["options_source_count"] = source_count
                    safe_field["options_source_truncated"] = source_truncated
                    safe_fields.append(safe_field)
                safe_form["fields"] = safe_fields
            context["observed_form"] = safe_form
            context["adapter"] = str(form_context.get("adapter") or adapter)
        provenance_observation = observation.get("answer_provenance")
        provenance_context = context.get("answer_provenance")
        if isinstance(provenance_observation, Mapping) and isinstance(
            provenance_context, dict
        ):
            snapshot_digest = str(
                provenance_observation.get("snapshot_digest") or ""
            )
            if re.fullmatch(r"[0-9a-f]{64}", snapshot_digest):
                provenance_context["expected_snapshot_digest"] = snapshot_digest
        workday_context = observation.get("workday_state")
        if isinstance(workday_context, Mapping):
            context["workday_state"] = dict(workday_context)
    fill_plan = job.get("_ats_fill_plan_context")
    if isinstance(fill_plan, Mapping):
        context["fill_plan"] = dict(fill_plan)
    return context
