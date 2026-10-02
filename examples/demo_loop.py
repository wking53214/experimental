"""
Full end-to-end governance loop demonstration.

This demonstrates the complete loop:

    GOVERNANCE PRINCIPLE
            ↓
       BOUNDARY
            ↓
       EXECUTION
            ↓
      VIOLATION EVENT
            ↓
         PATTERN
            ↓
      ADAPTATION PROPOSAL
            ↓
       AUTHORITY CHECK
            ↓
      TIGHTEN / HUMAN REVIEW
            ↓
        NEW BOUNDARY
            ↓
         VALIDATION
"""
import sys
import time
sys.path.insert(0, '/home/user/experimental')

from src.governance.governor import Governor
from src.governance.validation import ValidationOutcome


def demo():
    """Run a complete governance loop demonstration."""
    print("=" * 70)
    print("SELF-HARDENING GOVERNANCE ARCHITECTURE — PHASE 1 DEMO")
    print("=" * 70)

    # Initialize governor
    governor = Governor(store_path="/tmp/demo_governor")

    print("\n[1] GOVERNANCE PRINCIPLES")
    print("-" * 70)
    principles = governor.principles.list_principles()
    print(f"System invariants in place: {len(principles)}")
    for p in principles[:2]:
        print(f"  • {p.name}: {p.statement}")

    # Verify governance integrity at start
    intact, checks = governor.verify_governance_integrity()
    print(f"\nGovernance integrity: {intact}")

    print("\n[2] CREATING ADAPTIVE BOUNDARIES")
    print("-" * 70)

    # Create a boundary
    cpu_limit = governor.boundaries.create_boundary(
        boundary_id="cpu_usage",
        resource_or_action="cpu_percent",
        initial_limit=80,
    )
    print(f"Created boundary: cpu_usage")
    print(f"  Initial limit: {cpu_limit.current_limit}%")
    print(f"  Version: {cpu_limit.version}")

    print("\n[3] SETTING UP PATTERN DETECTOR")
    print("-" * 70)

    # Create pattern detector
    pattern = governor.patterns.create_pattern(
        pattern_id="cpu_violations",
        boundary_id="cpu_usage",
        violation_threshold=3,
        time_window_seconds=30,
    )
    print(f"Pattern: {pattern.violation_threshold} violations in {pattern.time_window_seconds}s")

    print("\n[4] EXECUTING AGAINST BOUNDARY — PHASE A: COMPLIANT")
    print("-" * 70)

    # Compliant executions
    for i in range(3):
        execution, violation = governor.execute_against_boundary(
            boundary_id="cpu_usage",
            observed_value=60 + (i * 5),  # Compliant values
        )
        print(f"  Execution {i+1}: CPU={60 + (i*5)}% — COMPLIANT")

    print("\nNo violations detected → No adaptation proposed")

    print("\n[5] EXECUTING AGAINST BOUNDARY — PHASE B: VIOLATIONS")
    print("-" * 70)

    # Violate boundary
    print("Initiating high-load scenario...")
    for i in range(3):
        execution, violation = governor.execute_against_boundary(
            boundary_id="cpu_usage",
            observed_value=95,  # Exceeds limit
            context={"scenario": "spike", "reason": "legitimate_workload"}
        )
        if violation:
            print(f"  Execution {i+1}: CPU=95% — VIOLATION (v{violation.boundary_version})")
        time.sleep(0.2)

    violations = governor.events.get_violations_for_boundary("cpu_usage")
    print(f"\nViolations recorded: {len(violations)}")

    print("\n[6] PATTERN DETECTION")
    print("-" * 70)

    detected_pattern = governor.patterns.detect_pattern(
        boundary_id="cpu_usage",
        recent_violations=violations,
    )

    if detected_pattern:
        print(f"✓ Pattern detected: {len(violations)} violations in window")
        print(f"  → Automatic adaptation proposal will be generated")
    else:
        print("✗ Pattern not detected")

    print("\n[7] ADAPTATION PROPOSAL")
    print("-" * 70)

    proposal = governor.detect_and_propose_adaptation("cpu_usage")
    if proposal:
        print(f"Proposal ID: {proposal.proposal_id[:8]}...")
        print(f"  Boundary: {proposal.boundary_id}")
        print(f"  Direction: {proposal.direction.value.upper()}")
        print(f"  Current limit: {proposal.current_value}%")
        print(f"  Proposed limit: {proposal.proposed_value:.1f}%")
        print(f"  Reason: {proposal.reason}")
        print(f"  Status: {proposal.status.value}")

    print("\n[8] AUTHORITY EVALUATION")
    print("-" * 70)

    if proposal:
        approved_proposal, auth_result = governor.authorize_proposal(proposal)
        print(f"Direction: {proposal.direction.value.upper()}")
        print(f"Result: {auth_result.value.upper()}")

        if auth_result.value == "auto_approved":
            print("✓ Automatically approved (TIGHTEN direction)")
        else:
            print("✗ Requires human review (LOOSEN/DISABLE direction)")

    print("\n[9] APPLYING APPROVED ADAPTATION")
    print("-" * 70)

    if proposal and approved_proposal.status.value == "approved":
        new_boundary = governor.apply_approved_proposal(approved_proposal)
        print(f"✓ Adaptation applied")
        print(f"  New version: {new_boundary.version}")
        print(f"  New limit: {new_boundary.current_limit:.1f}%")
        print(f"  Parent version: {new_boundary.parent_version}")

    print("\n[10] POST-ADAPTATION VALIDATION")
    print("-" * 70)

    if proposal and approved_proposal.status.value == "applied":
        # Register validator
        def cpu_validator(state):
            # Simulate: if violations decreased, IMPROVED
            return ValidationOutcome.IMPROVED

        governor.validators.register_validator("cpu_usage", cpu_validator)

        # Validate
        outcome = governor.validate_adaptation(
            proposal=approved_proposal,
            observed_state={"cpu_violations": 0, "throughput": "normal"}
        )
        print(f"Validation outcome: {outcome.value.upper()}")

    print("\n[11] HISTORICAL IMMUTABILITY CHECK")
    print("-" * 70)

    # Check that historical violations still reference v1
    violations_after = governor.events.get_violations_for_boundary("cpu_usage")
    v1_refs = sum(1 for v in violations_after if v.boundary_version == 1)
    print(f"Violations referencing v1: {v1_refs}/{len(violations_after)}")
    print("✓ Historical events unchanged" if v1_refs == len(violations_after) else "✗ History corrupted")

    print("\n[12] BOUNDARY HISTORY")
    print("-" * 70)

    history = governor.boundaries.get_boundary_history("cpu_usage")
    print(f"Boundary versions: {len(history.versions)}")
    for v_num, version in sorted(history.versions.items()):
        print(f"  v{v_num}: limit={version.current_limit:.1f}, status={version.status.value}")

    print("\n[13] SYSTEM STATUS")
    print("-" * 70)

    status = governor.get_status()
    for key, value in status.items():
        print(f"  {key}: {value}")

    print("\n[14] FINAL INTEGRITY VERIFICATION")
    print("-" * 70)

    final_intact, final_checks = governor.verify_governance_integrity()
    print(f"Governance integrity: {'✓ PASS' if final_intact else '✗ FAIL'}")
    for check_name, check_result in final_checks:
        status_str = "✓" if check_result else "✗"
        print(f"  {status_str} {check_name}")

    print("\n" + "=" * 70)
    print("DEMO COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    demo()
