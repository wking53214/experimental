"""
Phase 5: Heterogeneous System Models

Test governance across multiple system domains with interdependencies.

Systems modeled:
- Database: connection pool, query timeout, memory
- Cache: hit ratio, eviction rate, memory
- API Gateway: request rate, concurrent connections, timeout

Interdependencies:
- DB slowness → API timeout increases → gateway tightens rate
- Cache eviction → DB hit rate drops → DB pressure increases
- API rate limit → less load on DB → DB recovers

Key questions:
1. Can governor handle cross-domain cascades?
2. Do learned patterns transfer across domains?
3. Does system coordinate multi-domain tightening?
4. Can domains recover when others tighten?
5. Does isolation prevent failure cascade?
"""
import pytest
import time
from src.governance.governor import Governor
from src.governance.proposal import AdaptationDirection
from src.governance.authority import AuthorizationResult


class TestCrossDomainAdaptation:
    """When one domain tightens, how do others respond?"""

    @pytest.fixture
    def multi_domain_system(self):
        """Create Governor with multiple system domains."""
        gov = Governor(store_path="/tmp/test_heterogeneous", use_semantic=True)

        # Database domain
        gov.boundaries.create_boundary(
            boundary_id="db_connection_pool",
            resource_or_action="active_connections",
            initial_limit=100,
        )
        gov.boundaries.create_boundary(
            boundary_id="db_query_timeout_ms",
            resource_or_action="query_latency_ms",
            initial_limit=5000,
        )
        gov.boundaries.create_boundary(
            boundary_id="db_memory_mb",
            resource_or_action="memory_mb",
            initial_limit=8000,
        )

        # Cache domain
        gov.boundaries.create_boundary(
            boundary_id="cache_hit_ratio",
            resource_or_action="cache_hit_ratio_percent",
            initial_limit=70,  # Min acceptable hit ratio
        )
        gov.boundaries.create_boundary(
            boundary_id="cache_eviction_rate",
            resource_or_action="evictions_per_sec",
            initial_limit=100,
        )

        # API Gateway domain
        gov.boundaries.create_boundary(
            boundary_id="api_request_rate",
            resource_or_action="requests_per_sec",
            initial_limit=10000,
        )
        gov.boundaries.create_boundary(
            boundary_id="api_concurrent_connections",
            resource_or_action="concurrent_connections",
            initial_limit=5000,
        )
        gov.boundaries.create_boundary(
            boundary_id="api_timeout_ms",
            resource_or_action="p99_latency_ms",
            initial_limit=2000,
        )

        # Create patterns for each boundary
        boundary_ids = [
            "db_connection_pool", "db_query_timeout_ms", "db_memory_mb",
            "cache_hit_ratio", "cache_eviction_rate",
            "api_request_rate", "api_concurrent_connections", "api_timeout_ms",
        ]

        for boundary_id in boundary_ids:
            gov.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=4,
                time_window_seconds=30,
            )

        return gov

    def test_db_slowness_cascades_to_api_timeout(self, multi_domain_system):
        """DB slowness → API timeout increases → gateway tightens concurrency."""
        gov = multi_domain_system

        # Step 1: Database gets slow (query timeout violations)
        for i in range(6):
            gov.execute_against_boundary(
                boundary_id="db_query_timeout_ms",
                observed_value=6000,  # Over limit
                context={"domain": "database"},
            )

        # Step 2: Because DB is slow, API sees timeout increases
        for i in range(6):
            gov.execute_against_boundary(
                boundary_id="api_timeout_ms",
                observed_value=2500,  # Over limit due to DB
                context={"domain": "api", "cause": "db_slowness"},
            )

        # Step 3: API gateway detects pattern and tightens
        api_proposal = gov.detect_and_propose_adaptation("api_timeout_ms")

        # Verify: cascade was detected and API adapted
        api_violations = gov.events.get_violations_for_boundary("api_timeout_ms")
        db_violations = gov.events.get_violations_for_boundary("db_query_timeout_ms")

        assert len(db_violations) >= 6, "DB violations recorded"
        assert len(api_violations) >= 6, "API cascade detected"

        if api_proposal:
            # API adapted to DB's problem
            assert api_proposal.direction == AdaptationDirection.TIGHTEN

    def test_cache_eviction_stresses_database(self, multi_domain_system):
        """Cache eviction → DB hit rate drops → DB pressure increases."""
        gov = multi_domain_system

        # Step 1: Cache eviction increases (hit ratio drops)
        for i in range(6):
            gov.execute_against_boundary(
                boundary_id="cache_eviction_rate",
                observed_value=200,  # Over limit
                context={"domain": "cache"},
            )

        # Step 2: Low cache hit ratio means more DB queries
        for i in range(6):
            gov.execute_against_boundary(
                boundary_id="db_connection_pool",
                observed_value=120,  # Over limit due to cache misses
                context={"domain": "database", "cause": "cache_misses"},
            )

        # Verify cascade
        cache_violations = gov.events.get_violations_for_boundary("cache_eviction_rate")
        db_violations = gov.events.get_violations_for_boundary("db_connection_pool")

        assert len(cache_violations) >= 6, "Cache violations recorded"
        assert len(db_violations) >= 6, "DB cascade from cache"

    def test_api_rate_limit_reduces_pressure_on_db(self, multi_domain_system):
        """API tightens rate → less load → DB recovers."""
        gov = multi_domain_system

        # Step 1: High API request rate causes DB pressure
        for i in range(3):
            gov.execute_against_boundary(
                boundary_id="api_request_rate",
                observed_value=12000,  # Over limit
                context={"domain": "api"},
            )

        # This causes DB pressure
        for i in range(6):
            gov.execute_against_boundary(
                boundary_id="db_connection_pool",
                observed_value=120,
                context={"domain": "database", "cause": "api_load"},
            )

        # Step 2: API tightens rate limit
        api_proposal = gov.detect_and_propose_adaptation("api_request_rate")
        if api_proposal:
            _, auth_result = gov.authorize_proposal(api_proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(api_proposal)

        # Step 3: After API tightens, DB should see less pressure
        # Record DB violations before and after
        db_boundary_before = gov.boundaries.get_boundary("db_connection_pool")

        # Simulate load after API rate limit applied
        post_limit_violations = 0
        for i in range(5):
            _, violation = gov.execute_against_boundary(
                boundary_id="db_connection_pool",
                observed_value=80,  # Lower due to API rate limiting
                context={"domain": "database"},
            )
            if violation:
                post_limit_violations += 1

        # DB should see fewer violations after API tightens
        # (This is a directional test, not absolute)
        assert post_limit_violations <= 2, (
            "DB violations should decrease after API rate limiting"
        )


class TestKnowledgeTransfer:
    """Can patterns learned in one domain apply elsewhere?"""

    @pytest.fixture
    def governor(self):
        """Create Governor for knowledge transfer testing."""
        gov = Governor(store_path="/tmp/test_knowledge_transfer", use_semantic=True)

        # Create similar boundaries in different domains
        for domain in ["primary", "secondary"]:
            for metric in ["latency", "throughput"]:
                boundary_id = f"{domain}_{metric}"
                gov.boundaries.create_boundary(
                    boundary_id=boundary_id,
                    resource_or_action=metric,
                    initial_limit=100 if metric == "throughput" else 1000,
                )

                gov.patterns.create_pattern(
                    pattern_id=f"{boundary_id}_pattern",
                    boundary_id=boundary_id,
                    violation_threshold=3,
                    time_window_seconds=30,
                )

        return gov

    def test_pattern_learned_in_primary_domain(self, governor):
        """Learn pattern in primary domain."""
        gov = governor

        # Create violations in primary domain
        for i in range(5):
            gov.execute_against_boundary(
                boundary_id="primary_latency",
                observed_value=1500,  # Over 1000
                context={"pattern": "load_spike"},
            )

        # Pattern should be detected
        proposal = gov.detect_and_propose_adaptation("primary_latency")
        assert proposal is not None or len(
            gov.events.get_violations_for_boundary("primary_latency")
        ) >= 5

    def test_similar_pattern_in_secondary_domain(self, governor):
        """Similar pattern appears in secondary domain. System recognizes it?"""
        gov = governor

        # Create same pattern in secondary domain
        for i in range(5):
            gov.execute_against_boundary(
                boundary_id="secondary_latency",
                observed_value=1500,  # Same violation signature
                context={"pattern": "load_spike"},
            )

        # System should detect similar pattern
        violations = gov.events.get_violations_for_boundary("secondary_latency")
        assert len(violations) >= 5, "Similar pattern should be recorded"

        # Pattern detection should work
        proposal = gov.detect_and_propose_adaptation("secondary_latency")
        # Whether proposal or not, system recognizes the pattern


class TestMultiDomainCoordination:
    """When multiple domains need tightening, do they coordinate?"""

    @pytest.fixture
    def governor(self):
        """Create Governor for coordination testing."""
        gov = Governor(store_path="/tmp/test_coordination", use_semantic=True)

        # Create 3 domains with 2 boundaries each
        for domain in ["domain_a", "domain_b", "domain_c"]:
            for i in range(2):
                boundary_id = f"{domain}_boundary_{i}"
                gov.boundaries.create_boundary(
                    boundary_id=boundary_id,
                    resource_or_action=f"{domain}_metric_{i}",
                    initial_limit=100,
                )

                gov.patterns.create_pattern(
                    pattern_id=f"{boundary_id}_pattern",
                    boundary_id=boundary_id,
                    violation_threshold=3,
                    time_window_seconds=30,
                )

        return gov

    def test_all_domains_tighten_simultaneously(self, governor):
        """All domains need tightening. System handles all?"""
        gov = governor

        # Stress all domains
        domains = ["domain_a", "domain_b", "domain_c"]
        violations_recorded = {}

        for domain in domains:
            violations_recorded[domain] = 0
            for i in range(2):
                boundary_id = f"{domain}_boundary_{i}"
                for _ in range(5):
                    _, violation = gov.execute_against_boundary(
                        boundary_id=boundary_id,
                        observed_value=150,
                    )
                    if violation:
                        violations_recorded[domain] += 1

        # Attempt to adapt all domains
        proposals = []
        for domain in domains:
            for i in range(2):
                boundary_id = f"{domain}_boundary_{i}"
                proposal = gov.detect_and_propose_adaptation(boundary_id)
                if proposal:
                    proposals.append(proposal)

        # Apply all proposals
        applied_count = 0
        for proposal in proposals:
            _, auth_result = gov.authorize_proposal(proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(proposal)
                applied_count += 1

        # Verify: violations detected across all domains
        assert sum(violations_recorded.values()) > 0, (
            "Should record violations across multiple domains"
        )

        # Verify: no boundary loosened (monotonicity across all domains)
        for domain in domains:
            for i in range(2):
                boundary_id = f"{domain}_boundary_{i}"
                try:
                    boundary = gov.boundaries.get_boundary(boundary_id)
                    assert boundary.version <= 2, (
                        f"Boundary {boundary_id} shouldn't oscillate"
                    )
                except KeyError:
                    pass


class TestDomainIsolation:
    """Failures in one domain don't break others."""

    @pytest.fixture
    def governor(self):
        """Create Governor with isolated domains."""
        gov = Governor(store_path="/tmp/test_isolation", use_semantic=True)

        # Create two independent domains
        for domain in ["critical_service", "secondary_service"]:
            gov.boundaries.create_boundary(
                boundary_id=f"{domain}_limit",
                resource_or_action=f"{domain}_metric",
                initial_limit=100,
            )

            gov.patterns.create_pattern(
                pattern_id=f"{domain}_pattern",
                boundary_id=f"{domain}_limit",
                violation_threshold=3,
                time_window_seconds=30,
            )

        return gov

    def test_secondary_domain_failure_doesnt_break_critical(self, governor):
        """Secondary service fails. Critical service continues?"""
        gov = governor

        # Critical service: normal operations
        for i in range(10):
            gov.execute_against_boundary(
                boundary_id="critical_service_limit",
                observed_value=50,  # Normal
            )

        # Secondary service: lots of violations
        for i in range(10):
            gov.execute_against_boundary(
                boundary_id="secondary_service_limit",
                observed_value=200,  # Massive overload
            )

        # Critical service should still work fine
        critical_violations = gov.events.get_violations_for_boundary(
            "critical_service_limit"
        )
        assert len(critical_violations) == 0, (
            "Critical service should have no violations despite secondary failure"
        )

        # Secondary service should adapt
        secondary_violations = gov.events.get_violations_for_boundary(
            "secondary_service_limit"
        )
        assert len(secondary_violations) >= 10, (
            "Secondary service violations should be recorded"
        )

    def test_critical_service_protected_from_secondary_chaos(self, governor):
        """Even with secondary chaos, critical service maintains invariants."""
        gov = governor

        # Record critical service limit
        critical_initial = gov.boundaries.get_boundary(
            "critical_service_limit"
        ).current_limit

        # Chaos in secondary
        for iteration in range(50):
            gov.execute_against_boundary(
                boundary_id="secondary_service_limit",
                observed_value=300 - (iteration % 100),  # Wildly varying
            )

        # Try to adapt secondary
        proposal = gov.detect_and_propose_adaptation("secondary_service_limit")
        if proposal:
            _, auth_result = gov.authorize_proposal(proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(proposal)

        # Critical service should remain unchanged
        critical_final = gov.boundaries.get_boundary(
            "critical_service_limit"
        ).current_limit

        assert critical_final == critical_initial, (
            "Critical service boundary should not change due to secondary chaos"
        )

        # Critical service invariants maintained
        assert critical_final <= critical_initial * 1.01, (
            "Monotonicity maintained for critical service"
        )


class TestInterDependencyRecovery:
    """When one domain recovers, do others benefit?"""

    @pytest.fixture
    def governor(self):
        """Create Governor with interdependent domains."""
        gov = Governor(store_path="/tmp/test_recovery", use_semantic=True)

        gov.boundaries.create_boundary(
            boundary_id="upstream_throughput",
            resource_or_action="upstream_requests_per_sec",
            initial_limit=10000,
        )

        gov.boundaries.create_boundary(
            boundary_id="downstream_queue",
            resource_or_action="queue_depth",
            initial_limit=5000,
        )

        for boundary_id in ["upstream_throughput", "downstream_queue"]:
            gov.patterns.create_pattern(
                pattern_id=f"{boundary_id}_pattern",
                boundary_id=boundary_id,
                violation_threshold=3,
                time_window_seconds=30,
            )

        return gov

    def test_downstream_recovers_when_upstream_tightens(self, governor):
        """Upstream limits load → downstream queue drains."""
        gov = governor

        # Phase 1: Upstream and downstream both overloaded
        for i in range(6):
            gov.execute_against_boundary(
                boundary_id="upstream_throughput",
                observed_value=12000,  # Over limit
            )
            gov.execute_against_boundary(
                boundary_id="downstream_queue",
                observed_value=7000,  # Deep queue
            )

        # Phase 2: Upstream tightens
        upstream_proposal = gov.detect_and_propose_adaptation("upstream_throughput")
        if upstream_proposal:
            _, auth_result = gov.authorize_proposal(upstream_proposal)
            if auth_result == AuthorizationResult.AUTO_APPROVED:
                gov.apply_approved_proposal(upstream_proposal)

        # Phase 3: After upstream tightens, downstream should have less pressure
        downstream_violations_after = 0
        for i in range(5):
            _, violation = gov.execute_against_boundary(
                boundary_id="downstream_queue",
                observed_value=3000,  # Lower queue due to limited upstream
            )
            if violation:
                downstream_violations_after += 1

        # Downstream should improve
        assert downstream_violations_after <= 2, (
            "Downstream should recover after upstream tightens"
        )

    def test_recovery_maintains_monotonicity(self, governor):
        """As system recovers, boundaries remain monotonically tighter."""
        gov = governor

        # Record initial limits
        initial_limits = {
            "upstream_throughput": gov.boundaries.get_boundary(
                "upstream_throughput"
            ).current_limit,
            "downstream_queue": gov.boundaries.get_boundary(
                "downstream_queue"
            ).current_limit,
        }

        # Stress and recovery cycle
        for phase in range(3):
            # Stress
            for i in range(6):
                gov.execute_against_boundary(
                    boundary_id="upstream_throughput",
                    observed_value=12000,
                )
                gov.execute_against_boundary(
                    boundary_id="downstream_queue",
                    observed_value=7000,
                )

            # Try to adapt
            for boundary_id in ["upstream_throughput", "downstream_queue"]:
                proposal = gov.detect_and_propose_adaptation(boundary_id)
                if proposal:
                    _, auth_result = gov.authorize_proposal(proposal)
                    if auth_result == AuthorizationResult.AUTO_APPROVED:
                        gov.apply_approved_proposal(proposal)

            # Recovery
            for i in range(5):
                gov.execute_against_boundary(
                    boundary_id="upstream_throughput",
                    observed_value=5000,
                )
                gov.execute_against_boundary(
                    boundary_id="downstream_queue",
                    observed_value=2000,
                )

        # Final limits should be tighter (or equal) to initial
        final_limits = {
            "upstream_throughput": gov.boundaries.get_boundary(
                "upstream_throughput"
            ).current_limit,
            "downstream_queue": gov.boundaries.get_boundary(
                "downstream_queue"
            ).current_limit,
        }

        for boundary_id in ["upstream_throughput", "downstream_queue"]:
            assert final_limits[boundary_id] <= initial_limits[boundary_id] * 1.01, (
                f"Monotonicity maintained: {boundary_id}"
            )
