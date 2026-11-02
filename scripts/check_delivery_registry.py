#!/usr/bin/env python3
"""Validate the current-stage product delivery record."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
DEPARTMENTS = ["PAE", "DMI", "BAE", "FEX", "QMR", "QIR"]
STAGE_SEQUENCE = ["R0", "R0P", "R1", "R2", "R3", "R4", "R5", "R6"]
CURRENT_STAGE_STATUSES = {"in_progress", "completed_in_synthetic_environment"}
REQUIRED_DEFINITION = [
    "user_capability",
    "runnable_path",
    "acceptance_evidence",
    "non_goals",
]
REQUIRED_SAFEGUARDS = {
    "no_real_trading",
    "production_source_rights_fail_closed",
    "non_production_label_until_all_production_gates_pass",
    "evidence_id",
    "point_in_time_and_bitemporal",
    "idempotency",
    "stable_types_and_errors",
    "independent_final_head_review",
    "main_ff_only",
    "revert_or_forward_fix",
}
R1_SYNTHETIC_PROVEN_GATES = {
    "FULL_STACK_SYNTHETIC_E2E_VERIFIED",
    "PIT_CORRECTION_CONFLICT_IDEMPOTENCY_RECOVERY_VERIFIED",
    "EVIDENCE_DRILLDOWN_VERIFIED",
}
R1_SYNTHETIC_COMPLETE_GATES = R1_SYNTHETIC_PROVEN_GATES | {"FRESH_MAKE_VERIFY_VERIFIED"}
R1_SYNTHETIC_PENDING_GATES = ["FRESH_MAKE_VERIFY_PENDING"]
PRODUCTION_RELEASE_GATES = {
    "PRODUCTION_SOURCE_AUTHORIZATION_NOT_VERIFIED",
    "PRODUCTION_SHADOW_TRIAL_NOT_COMPLETED",
    "PRODUCTION_LIVE_E2E_NOT_VERIFIED",
    "PRODUCTION_MODEL_RUNTIME_NOT_QUALIFIED",
}
FORBIDDEN_FLOW = {
    "control_commit",
    "attempt_branch",
    "attempt_worktree",
    "quarantine_branch",
    "seal",
    "handoff",
    "package_tag",
    "ordinary_remote_branch",
    "multi_layer_receipt",
    "per_commit_full_verify",
}
OWNER_ALLOWED_ACTIONS = {
    "modify_in_scope_r2_product_code_tests_docs_and_non_authorization_delivery_state",
    "run_local_tests_and_independent_read_only_review",
    "commit_codex_ref_and_nonforce_push_matching_integration_ref",
    "aws_private_preview_exact_sha_build_deploy_migrate_and_debug",
    "inspect_nonsecret_aws_logs_and_database_diagnostics",
    "create_and_disable_minimum_authenticated_synthetic_canary_identities_and_jobs",
    "use_existing_managed_deepseek_and_tavily_secret_references_without_readback",
    "forward_fix_or_revert_private_preview_failures",
    "skip_waiting_for_github_actions_for_r2_private_preview_only",
}
OWNER_REQUIRED_DENIALS = {
    "local_or_remote_main_update",
    "formal_or_stage_tags",
    "production_or_commercial_readiness_claim",
    "r7_or_real_source_authorization",
    "real_trading_or_order_execution",
    "force_push_or_shared_history_rewrite",
    "destructive_data_stack_account_or_noncanary_identity_operations",
    "secret_value_readback_export_or_literal_persistence",
    "credential_reuse_from_prior_conversations",
    "robots_login_captcha_rate_limit_or_access_control_bypass",
    "disable_github_workflows_branch_protection_or_required_checks",
    "cross_project_or_cross_account_changes",
    "owner_authorization_agents_rules_or_checker_changes_without_fresh_current_task_owner_approval",
}
OWNER_REQUIRED_CONDITIONS = {
    "task_remains_within_current_r2_private_authenticated_synthetic_preview",
    "git_ref_is_allowed_and_descends_from_lineage_anchor",
    "exact_sha_and_artifact_identity_are_verified_before_external_write",
    "proportionate_local_checks_and_required_independent_review_pass",
    "github_actions_are_not_disabled_bypassed_or_reported_as_passing",
    "production_gates_and_dev_only_labels_remain_unchanged",
    "external_mutation_is_minimal_and_recoverable_by_forward_fix_or_revert",
    "secret_values_are_never_read_back_logged_or_persisted",
    "system_platform_and_account_confirmations_are_still_honored",
}
OWNER_REQUIRED_STOP_CONDITIONS = {
    "owner_revokes_authorization",
    "work_item_or_stage_environment_scope_changes",
    "head_is_not_a_clean_descendant_of_lineage_anchor",
    "diff_exceeds_registered_work_scope_or_overlaps_user_changes",
    "aws_region_stack_account_sha_digest_or_metadata_mismatch",
    "change_set_removes_or_replaces_protected_or_unknown_resources",
    "migration_is_not_append_only",
    "sensitive_value_could_enter_output_log_diff_or_review",
    "relevant_local_check_or_independent_review_blocks",
    "source_requires_robots_login_rate_limit_or_access_control_bypass",
    "production_gate_or_nonproduction_label_would_be_weakened",
    "managed_key_pool_is_exhausted_without_a_preprovisioned_compatible_version",
    "system_platform_provider_or_account_control_denies_the_action",
}
ACTIVE_STAGE_DOCS = (
    "README.md",
    "docs/product/requirements.md",
    "docs/product/experience-design.md",
    "docs/roadmap/implementation-plan.md",
    "docs/roadmap/workstreams/README.md",
    "docs/roadmap/workstreams/r2-multi-source-memory.md",
    "docs/roadmap/workstreams/r3-assistant-experience.md",
    "docs/roadmap/workstreams/r4-behavior-pressure.md",
    "docs/roadmap/workstreams/r5-local-llm-and-lora.md",
    "docs/engineering/README.md",
    "docs/engineering/departments/README.md",
    "docs/quality/acceptance-criteria.md",
)


def _load(root: Path) -> dict[str, object]:
    value = json.loads((root / "governance/delivery.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("governance/delivery.json root must be an object")
    return value


def _validate_owner_authorization(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["delivery.owner_authorization must be an object"]
    errors: list[str] = []
    scope = value.get("scope")
    if value.get("authorization_id") != ("user:2026-09-05:r2-private-synthetic-preview-release"):
        errors.append("owner authorization requires the recorded owner grant id")
    if value.get("schema_version") != "1.0" or value.get("status") != "active":
        errors.append("owner authorization must be active schema 1.0")
    if value.get("inherits_to") != [
        "new_project_conversations_loading_this_record",
        "spawned_agents_loading_this_record",
    ]:
        errors.append("owner authorization must name the two supported inheritance contexts")
    if value.get("host_approvals_inherited") is not False:
        errors.append("host and platform approvals cannot be inherited")
    if not isinstance(scope, dict):
        errors.append("owner authorization scope must be an object")
    else:
        if scope.get("repository") != "StockMarketAgent":
            errors.append("owner authorization is restricted to StockMarketAgent")
        if scope.get("work_id") != "r2-web-preview-release":
            errors.append("owner authorization is restricted to the R2 preview work item")
        if scope.get("stage") != "R2":
            errors.append("owner authorization is restricted to R2")
        if scope.get("environment") != "private_authenticated_synthetic_preview":
            errors.append("owner authorization is restricted to the private synthetic preview")
        if scope.get("lineage_anchor_sha") != "de8d03c659de48337d6b58e8daaf62ac2fec09a1":
            errors.append("owner authorization requires its recorded lineage anchor SHA")
        if scope.get("allowed_git_refs") != ["codex/**", "integration/**"]:
            errors.append("owner authorization cannot include main or other Git refs")
        if scope.get("aws_region") != "ap-northeast-1":
            errors.append("owner authorization is restricted to ap-northeast-1")
        if scope.get("runtime_stack") != "stockmarketagent-r2-preview":
            errors.append("owner authorization is restricted to the R2 preview stack")
        if scope.get("retained_web_stack") != "stockmarketagent-public-demo-tokyo":
            errors.append("owner authorization is restricted to the retained Web stack")
    actions = value.get("preauthorized_actions")
    if not isinstance(actions, list) or set(actions) != OWNER_ALLOWED_ACTIONS:
        errors.append("owner authorization preauthorized_actions must match its closed allowlist")
    denials = value.get("not_authorized")
    if not isinstance(denials, list) or not OWNER_REQUIRED_DENIALS.issubset(set(denials)):
        errors.append("owner authorization must retain every required denial")
    for field, required in (
        ("conditions", OWNER_REQUIRED_CONDITIONS),
        ("stop_conditions", OWNER_REQUIRED_STOP_CONDITIONS),
    ):
        entries = value.get(field)
        if (
            not isinstance(entries, list)
            or not entries
            or any(not isinstance(item, str) or not item for item in entries)
            or len(entries) != len(set(entries))
        ):
            errors.append(f"owner authorization {field} must be a non-empty unique string list")
        elif not required.issubset(set(entries)):
            errors.append(f"owner authorization {field} is missing a required safety boundary")
    if value.get("secret_policy") != (
        "existing_managed_secret_references_only_no_readback_or_literal_secret_persistence"
    ):
        errors.append("owner authorization cannot inherit or expose literal credentials")
    if value.get("platform_boundary") != (
        "repository_intent_record_does_not_override_system_platform_account_or_higher_level_policy"
    ):
        errors.append("owner authorization must preserve system and platform boundaries")
    if value.get("revocation") != (
        "owner_revocation_or_any_stage_environment_lineage_scope_or_safety_boundary_change"
    ):
        errors.append("owner authorization must stop on revocation or scope change")
    return errors


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        delivery = _load(root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [str(exc)]

    errors.extend(_validate_owner_authorization(delivery.get("owner_authorization")))

    current = delivery.get("current_stage")
    if delivery.get("stage_sequence") != STAGE_SEQUENCE:
        errors.append(
            "delivery.stage_sequence must be R0 -> R0P -> R1 -> R2 -> R3 -> R4 -> R5 -> R6"
        )
    if not isinstance(current, dict) or current.get("id") != "R2":
        errors.append("delivery.current_stage must identify unified R2")
    elif current.get("status") not in CURRENT_STAGE_STATUSES:
        errors.append(
            "delivery.current_stage.status must be in_progress or "
            "completed_in_synthetic_environment"
        )

    preview = delivery.get("private_preview")
    if not isinstance(preview, dict):
        errors.append("delivery.private_preview must be an object")
    else:
        product_sha = preview.get("product_sha")
        image_digest = preview.get("image_digest")
        if preview.get("status") not in {
            "deployed_and_canary_verified",
            "deployed_retest_blocked",
            "deployed_canary_incomplete",
        }:
            errors.append("private preview must report its deployed canary status")
        if preview.get("production_evidence") is not False:
            errors.append("private preview cannot be recorded as production evidence")
        if not isinstance(product_sha, str) or not SHA.fullmatch(product_sha):
            errors.append("private preview requires a full ProductSha")
        if not isinstance(image_digest, str) or not IMAGE_DIGEST.fullmatch(image_digest):
            errors.append("private preview requires an immutable image digest")
        if preview.get("main") != "unchanged" or preview.get("formal_tag") != "not_created":
            errors.append("private preview cannot claim main advancement or a formal tag")

    active = delivery.get("active_work")
    if not isinstance(active, list):
        errors.append("delivery.active_work must be a list")
    elif len(active) > 4:
        errors.append("delivery.active_work cannot exceed four writable items")
    else:
        authorization = delivery.get("owner_authorization")
        scope = authorization.get("scope") if isinstance(authorization, dict) else None
        work_id = scope.get("work_id") if isinstance(scope, dict) else None
        matching_work = [
            item for item in active if isinstance(item, dict) and item.get("id") == work_id
        ]
        if len(matching_work) != 1 or matching_work[0].get("status") != "in_progress":
            errors.append("owner authorization requires one matching in-progress active work item")

    serialized_delivery = json.dumps(delivery, ensure_ascii=False)
    for retired_stage in ("R2_CORE", "R2A", "R2B"):
        if retired_stage in serialized_delivery:
            errors.append(f"delivery cannot contain retired active stage {retired_stage}")
    milestones = delivery.get("milestones")
    if not isinstance(milestones, list):
        errors.append("delivery.milestones must be a list")
    else:
        milestone_ids = [item.get("id") for item in milestones if isinstance(item, dict)]
        if milestone_ids != ["R0", "R0P", "R1", "R2"]:
            errors.append("delivery milestones must end at the unified R2 stage")
    for relative_path in ACTIVE_STAGE_DOCS:
        text_value = (root / relative_path).read_text(encoding="utf-8")
        for retired_term in ("R2_CORE", "R2A", "R2B", "R2 Core", "R2a", "R2b"):
            if retired_term in text_value:
                errors.append(f"{relative_path} contains retired active-stage term {retired_term}")

    r1_exit = delivery.get("r1_exit_gates")
    if not isinstance(r1_exit, dict):
        errors.append("delivery.r1_exit_gates must be an object")
    else:
        satisfied = r1_exit.get("satisfied")
        blocking = r1_exit.get("blocking")
        gate_state = (
            frozenset(satisfied) if isinstance(satisfied, list) else frozenset(),
            tuple(blocking) if isinstance(blocking, list) else (),
        )
        if gate_state not in {
            (frozenset(R1_SYNTHETIC_PROVEN_GATES), tuple(R1_SYNTHETIC_PENDING_GATES)),
            (frozenset(R1_SYNTHETIC_COMPLETE_GATES), ()),
        }:
            errors.append("R1 synthetic gates must be coherently pending or fully verified")
        if "AUTHORIZED_SOURCE_NOT_VERIFIED" in json.dumps(r1_exit):
            errors.append("source authorization cannot block the R1 synthetic exit")
        if "REAL_INTEGRATION_AND_E2E_MISSING" in json.dumps(r1_exit):
            errors.append("the retired real-integration R1 gate cannot remain active")

    production = delivery.get("production_release_gates")
    if not isinstance(production, dict):
        errors.append("delivery.production_release_gates must be an object")
    else:
        gates = production.get("gates")
        if not isinstance(gates, list):
            errors.append("delivery.production_release_gates.gates must be a list")
        else:
            identifiers = {item.get("id") for item in gates if isinstance(item, dict)}
            statuses = {item.get("status") for item in gates if isinstance(item, dict)}
            if identifiers != PRODUCTION_RELEASE_GATES or statuses != {"blocking"}:
                errors.append("all four production release gates must remain blocking")

    candidate = delivery.get("integration_candidate")
    if not isinstance(candidate, dict):
        errors.append("delivery.integration_candidate must be an object")
    else:
        branch = candidate.get("branch")
        if not isinstance(branch, str) or not branch.startswith("integration/"):
            errors.append("integration candidate must use integration/**")
        baseline = candidate.get("baseline_sha")
        if not isinstance(baseline, str) or not SHA.fullmatch(baseline):
            errors.append("integration candidate baseline_sha must be a full Git SHA")
        source = candidate.get("source_sha")
        if not isinstance(source, str) or not SHA.fullmatch(source):
            errors.append("integration candidate source_sha must be an immutable full Git SHA")
        candidate_status = candidate.get("status")
        verification_sha = candidate.get("last_full_verification_sha")
        review_sha = candidate.get("final_review_target_sha")
        review_status = candidate.get("final_review_status")
        # The last verified candidate and the currently running release are independent
        # lifecycle facts; a new candidate must be verifiable before it is deployed.
        if verification_sha is not None and (
            not isinstance(verification_sha, str) or not SHA.fullmatch(verification_sha)
        ):
            errors.append("candidate requires an immutable verification SHA when present")
        if candidate_status == "verified_in_synthetic_environment":
            if verification_sha is None:
                errors.append("verified candidate requires an immutable verification SHA")
        elif candidate_status != "pending_final_verification":
            errors.append("integration candidate status is invalid")
        if review_status == "approved":
            if (
                not isinstance(review_sha, str)
                or not SHA.fullmatch(review_sha)
                or review_sha != source
            ):
                errors.append("approved review must identify the current source SHA")
        elif (
            review_status != "pending"
            or review_sha is not None
            or candidate_status != "pending_final_verification"
        ):
            errors.append("candidate requires an approved review or a pending unreviewed state")

    if delivery.get("departments") != DEPARTMENTS:
        errors.append("delivery.departments must contain exactly the six responsibility domains")

    ordinary = delivery.get("ordinary_delivery")
    if not isinstance(ordinary, dict):
        errors.append("delivery.ordinary_delivery must be an object")
    else:
        if ordinary.get("required_definition") != REQUIRED_DEFINITION:
            errors.append("ordinary delivery must require the four product-first fields")
        steps = ordinary.get("steps")
        if not isinstance(steps, list) or len(steps) > 5:
            errors.append("ordinary delivery must contain at most five implementation steps")
        if ordinary.get("executor_paths") != ["scripts/lightweight_agent.py"]:
            errors.append("ordinary delivery must have exactly one executor path")
        forbidden = ordinary.get("forbidden")
        if not isinstance(forbidden, list) or set(forbidden) != FORBIDDEN_FLOW:
            errors.append("ordinary delivery must forbid the retired heavy-flow states")

    release = delivery.get("release")
    if not isinstance(release, dict):
        errors.append("delivery.release must be an object")
    else:
        if release.get("main_update") != "ff_only":
            errors.append("main update must remain ff_only")
        if release.get("rollback") != "revert_or_forward_fix":
            errors.append("rollback must remain revert_or_forward_fix")
        if release.get("rewrite_shared_history") is not False:
            errors.append("shared history rewriting must remain disabled")

    safeguards = delivery.get("retained_safeguards")
    if not isinstance(safeguards, list) or set(safeguards) != REQUIRED_SAFEGUARDS:
        errors.append("delivery.retained_safeguards is incomplete")
    legacy = delivery.get("legacy")
    if not isinstance(legacy, dict) or legacy.get("status") != "git_history_only":
        errors.append("legacy delivery must be git_history_only")
    elif legacy.get("active_parser") is not False or legacy.get("active_executor") is not False:
        errors.append("legacy parser and executor must remain inactive")
    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("Delivery registry check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Delivery registry check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
