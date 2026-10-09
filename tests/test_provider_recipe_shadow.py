from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from time import perf_counter

import pytest

from applypilot.apply.browser_authority import BrowserAuthorityHandle
from applypilot.apply.browser_broker import BrowserBroker
from applypilot.apply.contracts import application_actor_id
from applypilot.apply.provider_recipe_shadow import (
    RECIPE_SHADOW_DIAGNOSTIC_COUNT_LIMIT,
    ProviderRecipeShadowObserver,
    _telemetry,
    observe_prepare_recipe_shadow,
)
from applypilot.apply.recipe_cache import ValueFreeRecipeCache
from applypilot.apply.semantic_batch import SemanticBatchDenied


def _snapshot(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "form_fields": [
            {
                "field_key": "email",
                "label": "Email address",
                "control": "email",
                "required": True,
                "disabled": False,
                "readonly": False,
                "autocomplete": "email",
                "placeholder": "",
                "protected_identifier": False,
                "options": [],
                "option_count": 0,
                "options_truncated": False,
            },
            {
                "field_key": "submit",
                "label": "Submit application",
                "control": "submit",
                "required": False,
                "disabled": False,
                "readonly": False,
                "autocomplete": "",
                "placeholder": "",
                "protected_identifier": False,
                "options": [],
                "option_count": 0,
                "options_truncated": False,
            },
        ],
        "submit_control_count": 1,
        "captcha_visible": False,
        "assessment_visible": False,
        "verification_visible": False,
        "resume_field_present": False,
        "file_fields": [],
        "sensitive_required_unknown": [],
    }
    value.update(changes)
    return value


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("greenhouse", "https://boards.greenhouse.io/acme/jobs/123"),
        (
            "workday",
            "https://acme.wd5.myworkdayjobs.com/en-US/jobs/job/Engineer_R123/apply",
        ),
        (
            "smartrecruiters",
            "https://jobs.smartrecruiters.com/Acme/744000012345678-engineer",
        ),
    ],
)
def test_each_provider_shadow_switch_is_independent_and_always_falls_back(
    provider: str,
    url: str,
) -> None:
    observer = ProviderRecipeShadowObserver()
    off = observer.observe(
        enabled_providers=(),
        application_target_url=url,
        page_url=url,
        surface_url=url,
        surface_is_main_frame=True,
        snapshot=_snapshot(),
        page_epoch=1,
        page_lease_id="lease-1",
        browser_generation=1,
    )
    miss = observer.observe(
        enabled_providers=(provider,),
        application_target_url=url,
        page_url=url,
        surface_url=url,
        surface_is_main_frame=True,
        snapshot=_snapshot(),
        page_epoch=1,
        page_lease_id="lease-1",
        browser_generation=1,
    )
    hit = observer.observe(
        enabled_providers=(provider,),
        application_target_url=url,
        page_url=url,
        surface_url=url,
        surface_is_main_frame=True,
        snapshot=_snapshot(),
        page_epoch=2,
        page_lease_id="lease-2",
        browser_generation=2,
    )

    assert off.outcome == "off"
    assert miss.outcome == "miss"
    assert hit.outcome == "hit"
    assert all(item.agent_fallback_required for item in (off, miss, hit))
    assert hit.as_dict()["browser_write_authority"] is False
    assert hit.as_dict()["file_upload_authority"] is False
    assert hit.as_dict()["submit_authority"] is False
    assert hit.as_dict()["throughput_admission_evidence"] is False


@pytest.mark.parametrize(
    ("snapshot_changes", "surface_url", "surface_is_main_frame", "reason"),
    [
        ({"captcha_visible": True}, None, True, "observation_not_recipe_safe"),
        ({"assessment_visible": True}, None, True, "observation_not_recipe_safe"),
        ({"verification_visible": True}, None, True, "observation_not_recipe_safe"),
        ({"resume_field_present": True}, None, True, "observation_not_recipe_safe"),
        ({"sensitive_required_unknown": ["Visa"]}, None, True, "observation_not_recipe_safe"),
        ({}, "https://forms.example.test/apply", True, "cross_origin_surface_not_admitted"),
        ({}, None, False, "framed_surface_not_admitted"),
    ],
)
def test_shadow_observation_fail_closes_manual_and_surface_boundaries(
    snapshot_changes: dict[str, object],
    surface_url: str | None,
    surface_is_main_frame: bool,
    reason: str,
) -> None:
    url = "https://boards.greenhouse.io/acme/jobs/123"
    decision = ProviderRecipeShadowObserver().observe(
        enabled_providers=("greenhouse",),
        application_target_url=url,
        page_url=url,
        surface_url=surface_url or url,
        surface_is_main_frame=surface_is_main_frame,
        snapshot=_snapshot(**snapshot_changes),
        page_epoch=1,
        page_lease_id="lease-1",
        browser_generation=1,
    )

    assert decision.outcome == "denied"
    assert decision.reason_code == reason
    assert decision.agent_fallback_required is True


@pytest.mark.parametrize("control", ["textarea", "combobox", "file", "checkbox", "radio", "date"])
def test_complex_and_file_controls_deny_the_entire_shadow_recipe(control: str) -> None:
    snapshot = _snapshot()
    fields = deepcopy(snapshot["form_fields"])
    assert isinstance(fields, list)
    fields.append(
        {
            "field_key": "unsafe",
            "label": "Additional information",
            "control": control,
            "required": False,
            "disabled": False,
            "readonly": False,
            "options": [],
            "option_count": 0,
            "options_truncated": False,
        }
    )
    snapshot["form_fields"] = fields
    url = "https://boards.greenhouse.io/acme/jobs/123"

    decision = ProviderRecipeShadowObserver().observe(
        enabled_providers=("greenhouse",),
        application_target_url=url,
        page_url=url,
        surface_url=url,
        surface_is_main_frame=True,
        snapshot=snapshot,
        page_epoch=1,
        page_lease_id="lease-1",
        browser_generation=1,
    )

    assert decision.outcome == "denied"
    assert decision.agent_fallback_required is True


def test_shadow_telemetry_never_records_structural_or_candidate_values() -> None:
    url = "https://boards.greenhouse.io/acme/jobs/123"
    snapshot = _snapshot()
    fields = deepcopy(snapshot["form_fields"])
    assert isinstance(fields, list)
    assert isinstance(fields[0], dict)
    fields[0]["label"] = "Candidate private@example.test email"
    snapshot["form_fields"] = fields

    payload = (
        ProviderRecipeShadowObserver()
        .observe(
            enabled_providers=("greenhouse",),
            application_target_url=url,
            page_url=url,
            surface_url=url,
            surface_is_main_frame=True,
            snapshot=snapshot,
            page_epoch=1,
            page_lease_id="lease-secret",
            browser_generation=1,
        )
        .as_dict()
    )

    assert "private@example.test" not in repr(payload)
    assert "lease-secret" not in repr(payload)
    assert url not in repr(payload)


def test_default_off_production_observation_does_not_parse_browser_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://boards.greenhouse.io/acme/jobs/123"

    def unexpected_rebuild(_job: object) -> None:
        raise AssertionError("disabled shadow observation must not parse authority")

    monkeypatch.setattr(
        "applypilot.apply.provider_recipe_shadow.BrowserAuthorityHandle.rebuild",
        unexpected_rebuild,
    )
    telemetry = observe_prepare_recipe_shadow(
        job={"application_url": url},
        page_url=url,
        surface_url=url,
        surface_is_main_frame=True,
        snapshot=_snapshot(),
        enabled_providers=(),
    )

    assert telemetry.outcome == "off"
    assert telemetry.agent_fallback_required is True


def test_enabled_production_observation_denies_without_fresh_browser_authority() -> None:
    url = "https://boards.greenhouse.io/acme/jobs/123"
    telemetry = observe_prepare_recipe_shadow(
        job={"application_url": url},
        page_url=url,
        surface_url=url,
        surface_is_main_frame=True,
        snapshot=_snapshot(),
        enabled_providers=("greenhouse",),
    )

    assert telemetry.outcome == "denied"
    assert telemetry.reason_code == "fresh_browser_authority_unavailable"


def test_enabled_production_observation_uses_current_read_only_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://boards.greenhouse.io/acme/jobs/987"
    attempt_id = "recipe-shadow-attempt"
    job: dict[str, object] = {"_attempt_id": attempt_id, "application_url": url}
    broker = BrowserBroker()
    handle = BrowserAuthorityHandle.create(
        job,
        broker=broker,
        browser_generation=3,
        application_session_id="recipe-shadow-session",
        actor_id=application_actor_id(attempt_id),
        attempt_id=attempt_id,
    )
    handle.acquire_or_continue(
        profile_id="edge:worker:shadow",
        page_id="application:recipe-shadow-attempt",
        scope_id="worker:shadow",
        runtime_id="test:edge:cdp:0",
        submit_started=False,
        resume_existing_page=False,
    )
    monkeypatch.setattr(
        "applypilot.apply.provider_recipe_shadow._PRODUCTION_SHADOW_OBSERVER",
        ProviderRecipeShadowObserver(),
    )
    telemetry = observe_prepare_recipe_shadow(
        job=job,
        page_url=url,
        surface_url=url,
        surface_is_main_frame=True,
        snapshot=_snapshot(),
        enabled_providers=("greenhouse",),
    )

    assert telemetry.outcome == "miss"
    assert telemetry.admission_enabled is True
    assert telemetry.agent_fallback_required is True


def _observe_snapshot(snapshot: dict[str, object], **kwargs):
    url = "https://boards.greenhouse.io/acme/jobs/123"
    observer = kwargs.pop("observer", None) or ProviderRecipeShadowObserver()
    arguments = {
        "enabled_providers": ("greenhouse",),
        "application_target_url": url,
        "page_url": url,
        "surface_url": url,
        "surface_is_main_frame": True,
        "snapshot": snapshot,
        "page_epoch": 1,
        "page_lease_id": "lease-private",
        "browser_generation": 1,
    }
    arguments.update(kwargs)
    return observer.observe(**arguments)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"captcha_visible": True}, "captcha"),
        ({"assessment_visible": True}, "assessment"),
        ({"verification_visible": True}, "verification"),
        ({"resume_field_present": True}, "file_upload"),
        ({"sensitive_required_unknown": ["private visa text"]}, "sensitive_or_legal"),
        ({"form_fields": ["private malformed control"]}, "invalid_control_structure"),
        ({"form_fields": None}, "invalid_control_structure"),
    ],
)
def test_denied_snapshot_diagnostics_explain_observed_gates(changes, code) -> None:
    decision = _observe_snapshot(_snapshot(**changes))

    assert decision.outcome == "denied"
    assert decision.reason_code == "observation_not_recipe_safe"
    assert decision.as_dict()["diagnostic_counts"][code] == 1
    assert "private" not in repr(decision.as_dict())


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"control": "combobox"}, "unsupported_control_kind"),
        ({"control": "file"}, "file_upload"),
        ({"options_truncated": True}, "truncated_options"),
        ({"option_count": "private invalid options"}, "invalid_option_structure"),
        ({"option_count": 2}, "invalid_option_structure"),
        ({"label": "Visa", "autocomplete": ""}, "sensitive_or_legal"),
        ({"protected_identifier": True}, "sensitive_or_legal"),
        ({"label": "School", "field_key": "school", "autocomplete": ""}, "unknown_semantic"),
        ({"disabled": True}, "nonwritable_control"),
        ({"readonly": True}, "nonwritable_control"),
    ],
)
def test_control_diagnostics_use_actual_recipe_constraints(changes, code) -> None:
    snapshot = _snapshot()
    field = deepcopy(snapshot["form_fields"][0])
    field.update(changes)
    snapshot["form_fields"] = [field]

    decision = _observe_snapshot(snapshot)

    assert decision.outcome == "denied"
    assert code in decision.diagnostic_codes
    assert dict(decision.diagnostic_counts)[code] == 1


def test_native_multiple_is_denied_before_any_recipe_is_cached(tmp_path) -> None:
    snapshot = _snapshot()
    snapshot["form_fields"].append({
        "control": "select", "label": "Country", "field_key": "country",
        "multiple": True, "options": ["Singapore", "China"], "option_count": 2,
    })
    cache = ValueFreeRecipeCache()
    path = tmp_path / "multiple-must-not-persist.db"
    observer = ProviderRecipeShadowObserver(cache=cache, experience_db_path=path)

    decision = _observe_snapshot(snapshot, observer=observer)

    assert decision.outcome == "denied"
    assert dict(decision.diagnostic_counts)["multi_select"] == 1
    assert len(cache) == 0
    assert not path.exists()
    assert decision.as_dict()["browser_write_authority"] is False


def test_native_single_select_keeps_existing_miss_then_hit_behavior(tmp_path) -> None:
    snapshot = _snapshot()
    snapshot["form_fields"].append({
        "control": "select", "label": "Country", "field_key": "country",
        "multiple": False, "options": ["Singapore", "China"], "option_count": 2,
    })
    observer = ProviderRecipeShadowObserver(experience_db_path=tmp_path / "experience.db")

    first = _observe_snapshot(snapshot, observer=observer)
    repeated = _observe_snapshot(snapshot, observer=observer)

    assert (first.outcome, repeated.outcome) == ("miss", "hit")
    assert repeated.routine_control_count == 2
    assert "multi_select" not in repeated.diagnostic_codes


def test_upload_summary_and_control_count_the_same_evidence_once() -> None:
    snapshot = _snapshot(resume_field_present=True, file_fields=["private resume"])
    snapshot["form_fields"].append({"control": "file", "label": "Resume"})

    decision = _observe_snapshot(snapshot)

    assert decision.outcome == "denied"
    assert dict(decision.diagnostic_counts)["file_upload"] == 1


def test_repeated_routine_semantics_have_bounded_control_counts() -> None:
    snapshot = _snapshot()
    first = snapshot["form_fields"][0]
    snapshot["form_fields"] = [deepcopy(first), deepcopy(first), deepcopy(first)]

    decision = _observe_snapshot(snapshot)

    assert decision.outcome == "denied"
    assert dict(decision.diagnostic_counts)["ambiguous_repeated_semantic"] == 3


def test_ignored_controls_do_not_change_existing_cache_admission(tmp_path) -> None:
    snapshot = _snapshot()
    snapshot["form_fields"].extend([
        {"control": "text", "label": "School", "field_key": "school"},
        {"control": "email", "label": "Email", "disabled": True},
    ])
    observer = ProviderRecipeShadowObserver(experience_db_path=tmp_path / "experience.db")

    miss = _observe_snapshot(snapshot, observer=observer)
    hit = _observe_snapshot(snapshot, observer=observer)

    assert (miss.outcome, hit.outcome) == ("miss", "hit")
    assert miss.routine_control_count == hit.routine_control_count == 1
    assert "unknown_semantic" in hit.diagnostic_codes
    assert "nonwritable_control" in hit.diagnostic_codes
    assert "ambiguous_repeated_semantic" not in hit.diagnostic_codes
    assert "cache_candidate_unavailable" in miss.diagnostic_codes
    assert "cache_candidate_unavailable" not in hit.diagnostic_codes


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"surface_is_main_frame": False}, "framed_surface"),
        ({"surface_url": "https://private.example.test/apply"}, "cross_origin_surface"),
    ],
)
def test_surface_diagnostics_do_not_export_urls(arguments, code) -> None:
    payload = _observe_snapshot(_snapshot(), **arguments).as_dict()

    assert payload["outcome"] == "denied"
    assert payload["diagnostic_counts"] == {code: 1}
    assert "private.example.test" not in repr(payload)


def test_registry_denial_exports_no_raw_exception_or_field_data(tmp_path) -> None:
    class DenyingRegistry:
        def normalize(self, observation):
            raise SemanticBatchDenied("private@example.test applicant value / selector #secret")

    observer = ProviderRecipeShadowObserver(
        registry=DenyingRegistry(), experience_db_path=tmp_path / "experience.db",
    )
    payload = _observe_snapshot(_snapshot(), observer=observer).as_dict()

    assert payload["reason_code"] == "observation_not_recipe_safe"
    assert payload["diagnostic_counts"] == {"registry_constraints_not_satisfied": 1}
    assert "private@example.test" not in repr(payload)
    assert "#secret" not in repr(payload)


def test_diagnostic_payload_has_a_closed_vocabulary_and_capped_counts() -> None:
    decision = _telemetry(
        perf_counter(), provider="greenhouse", outcome="denied", admission_enabled=True,
        reason_code="observation_not_recipe_safe",
        diagnostic_counts={"unsupported_control_kind": RECIPE_SHADOW_DIAGNOSTIC_COUNT_LIMIT + 1},
    )
    assert decision.as_dict()["diagnostic_counts"] == {
        "unsupported_control_kind": RECIPE_SHADOW_DIAGNOSTIC_COUNT_LIMIT,
    }
    for counts in [
        (("private@example.test", 1),),
        (("captcha", 0),),
        (("captcha", True),),
        (("captcha", RECIPE_SHADOW_DIAGNOSTIC_COUNT_LIMIT + 1),),
        (("captcha", 1), ("captcha", 2)),
    ]:
        with pytest.raises(ValueError):
            replace(decision, diagnostic_counts=counts)
