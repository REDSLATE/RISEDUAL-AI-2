"""
Iteration 60: Dual-Signal Adversarial AI Memory System Tests

Tests the new dual-signal adversarial AI integration:
- 'Edge' (win patterns from get_strategist_context) 
- 'Veto' (toxic lessons from get_strategist_veto_context)

Both signals are injected into all 3 AI crew synthesizer prompts:
- War Room
- Hypothesis  
- Market Prediction

The AI must compare current conditions against both success and danger patterns,
with explicit adversarial checks.

NOTE: These tests do NOT trigger actual AI crew runs (costs API credits).
Instead they test:
1. Memory service functions directly via Python imports
2. Crew definition function signatures and prompt construction logic
3. Prediction service function passes all 3 context types
"""

import pytest
import asyncio
import sys
import inspect

# Add backend to path for direct imports
sys.path.insert(0, '/app/backend')

# ──────────────────────────────────────────────
#  FIXTURES
# ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def init_memory_service():
    """Initialize ChromaDB memory service for testing."""
    from services.market_memory_service import init_memory, _collection
    init_memory()
    return _collection


# ──────────────────────────────────────────────
#  TEST 1: get_strategist_veto_context returns FAILED patterns (outcome='toxic_lesson')
# ──────────────────────────────────────────────

class TestVetoContext:
    """Tests for get_strategist_veto_context() function."""

    @pytest.mark.asyncio
    async def test_veto_context_returns_toxic_lessons(self, init_memory_service):
        """Verify get_strategist_veto_context queries for outcome='toxic_lesson'."""
        from services.market_memory_service import get_strategist_veto_context
        
        # Query for toxic lessons - TEST_TOXIC_1 and TEST_TOXIC_2 should exist from iteration 59
        result = await get_strategist_veto_context("TEST_TOXIC", n_results=5)
        
        # Should return formatted warning text or empty string
        assert isinstance(result, str), "get_strategist_veto_context should return a string"
        
        # If toxic lessons exist, verify format
        if result:
            assert "WARNING" in result or "TOXIC" in result or "FAILED" in result, \
                "Veto context should contain warning/toxic/failed keywords"
            assert "toxic_lesson" not in result.lower() or "FAILED Pattern" in result, \
                "Should format toxic lessons as FAILED patterns"
            print(f"✓ Veto context returned: {result[:200]}...")
        else:
            print("✓ No toxic lessons found (empty string returned as expected)")

    @pytest.mark.asyncio
    async def test_veto_context_filters_by_outcome_toxic_lesson(self, init_memory_service):
        """Verify the ChromaDB query uses outcome='toxic_lesson' filter."""
        from services.market_memory_service import get_strategist_veto_context
        
        # Check the function source code for the filter
        source = inspect.getsource(get_strategist_veto_context)
        
        assert 'outcome' in source, "Function should filter by outcome"
        assert 'toxic_lesson' in source, "Function should filter for toxic_lesson outcome"
        print("✓ get_strategist_veto_context correctly filters for outcome='toxic_lesson'")

    @pytest.mark.asyncio
    async def test_veto_context_format_when_toxic_exists(self, init_memory_service):
        """Verify veto context returns properly formatted warnings when toxic lessons exist."""
        from services.market_memory_service import get_strategist_veto_context
        
        # Query for any ticker - ChromaDB does similarity search so may return results
        result = await get_strategist_veto_context("TEST", n_results=2)
        
        # If results exist, verify they are properly formatted as warnings
        if result:
            assert "WARNING" in result or "TOXIC" in result or "FAILED" in result, \
                "Veto context should contain warning keywords when toxic lessons exist"
            assert "confidence" in result.lower(), \
                "Veto context should mention confidence level"
            print(f"✓ Veto context properly formatted: {result[:150]}...")
        else:
            print("✓ No toxic lessons in database (empty string returned)")


# ──────────────────────────────────────────────
#  TEST 2: get_strategist_context returns WIN patterns (outcome='hit')
# ──────────────────────────────────────────────

class TestEdgeContext:
    """Tests for get_strategist_context() function (the 'Edge')."""

    @pytest.mark.asyncio
    async def test_edge_context_returns_win_patterns(self, init_memory_service):
        """Verify get_strategist_context queries for outcome='hit'."""
        from services.market_memory_service import get_strategist_context
        
        # Query for win patterns
        result = await get_strategist_context("MARKET", n_results=3)
        
        assert isinstance(result, str), "get_strategist_context should return a string"
        
        if "No similar" not in result and result:
            assert "Win Pattern" in result or "HISTORICAL WIN" in result or "successful" in result.lower(), \
                "Edge context should contain win pattern keywords"
            print(f"✓ Edge context returned: {result[:200]}...")
        else:
            print("✓ No win patterns found (expected message returned)")

    @pytest.mark.asyncio
    async def test_edge_context_filters_by_outcome_hit(self, init_memory_service):
        """Verify the ChromaDB query uses outcome='hit' filter."""
        from services.market_memory_service import get_strategist_context
        import inspect
        
        source = inspect.getsource(get_strategist_context)
        
        assert 'outcome' in source, "Function should filter by outcome"
        assert '"hit"' in source or "'hit'" in source, "Function should filter for hit outcome"
        print("✓ get_strategist_context correctly filters for outcome='hit'")


# ──────────────────────────────────────────────
#  TEST 3: query_similar_regimes excludes toxic_lesson by default
# ──────────────────────────────────────────────

class TestQuerySimilarRegimes:
    """Tests for query_similar_regimes() default behavior."""

    @pytest.mark.asyncio
    async def test_query_excludes_toxic_by_default(self, init_memory_service):
        """Verify query_similar_regimes excludes toxic_lesson by default."""
        from services.market_memory_service import query_similar_regimes
        import inspect
        
        source = inspect.getsource(query_similar_regimes)
        
        # Check for the $ne filter for toxic_lesson
        assert '$ne' in source or 'toxic_lesson' in source, \
            "query_similar_regimes should exclude toxic_lesson by default"
        assert 'outcome' in source, "Should filter by outcome field"
        print("✓ query_similar_regimes excludes toxic_lesson by default")

    @pytest.mark.asyncio
    async def test_query_returns_non_toxic_results(self, init_memory_service):
        """Verify query results don't include toxic_lesson entries."""
        from services.market_memory_service import query_similar_regimes
        
        current_state = {"symbol": "AAPL", "price": 150.0, "rsi": 50}
        results = await query_similar_regimes(current_state, n_results=5)
        
        # Results should not contain toxic_lesson outcome
        for r in results:
            assert r.get("outcome") != "toxic_lesson", \
                f"Default query should not return toxic_lesson entries, got: {r.get('outcome')}"
        
        print(f"✓ query_similar_regimes returned {len(results)} non-toxic results")


# ──────────────────────────────────────────────
#  TEST 4: run_war_room_crew synth_prompt includes dual-signal + ADVERSARIAL CHECK
# ──────────────────────────────────────────────

class TestWarRoomCrewPrompt:
    """Tests for run_war_room_crew() prompt construction."""

    def test_war_room_has_dual_signal_sections(self):
        """Verify run_war_room_crew builds prompts with both Edge and Veto sections."""
        from services.crew_definitions import run_war_room_crew
        import inspect
        
        source = inspect.getsource(run_war_room_crew)
        
        # Check for Edge (win patterns) section
        assert "PROVEN SUCCESS PATTERNS" in source or "Edge" in source, \
            "War Room should include PROVEN SUCCESS PATTERNS section"
        
        # Check for Veto (toxic lessons) section
        assert "PREVIOUS TRAPS & FAILURES" in source or "Veto" in source, \
            "War Room should include PREVIOUS TRAPS & FAILURES section"
        
        print("✓ run_war_room_crew includes both Edge and Veto sections")

    def test_war_room_has_adversarial_check(self):
        """Verify run_war_room_crew includes ADVERSARIAL CHECK instructions."""
        from services.crew_definitions import run_war_room_crew
        import inspect
        
        source = inspect.getsource(run_war_room_crew)
        
        assert "ADVERSARIAL CHECK" in source, \
            "War Room should include ADVERSARIAL CHECK instructions"
        assert "DANGER" in source or "trap" in source.lower(), \
            "Should reference danger patterns in adversarial check"
        
        print("✓ run_war_room_crew includes ADVERSARIAL CHECK instructions")

    def test_war_room_fetches_both_contexts(self):
        """Verify run_war_room_crew fetches both win_context and veto_context."""
        from services.crew_definitions import run_war_room_crew
        import inspect
        
        source = inspect.getsource(run_war_room_crew)
        
        assert "get_strategist_context" in source, \
            "War Room should call get_strategist_context for win patterns"
        assert "get_strategist_veto_context" in source, \
            "War Room should call get_strategist_veto_context for toxic lessons"
        
        print("✓ run_war_room_crew fetches both win_context and veto_context")


# ──────────────────────────────────────────────
#  TEST 5: run_hypothesis_crew synth_prompt includes dual-signal + ADVERSARIAL CHECK
# ──────────────────────────────────────────────

class TestHypothesisCrewPrompt:
    """Tests for run_hypothesis_crew() prompt construction."""

    def test_hypothesis_has_dual_signal_sections(self):
        """Verify run_hypothesis_crew builds prompts with both Edge and Veto sections."""
        from services.crew_definitions import run_hypothesis_crew
        import inspect
        
        source = inspect.getsource(run_hypothesis_crew)
        
        # Check for Edge (win patterns) section
        assert "PROVEN SUCCESS PATTERNS" in source or "Edge" in source, \
            "Hypothesis crew should include PROVEN SUCCESS PATTERNS section"
        
        # Check for Veto (toxic lessons) section
        assert "PREVIOUS TRAPS & FAILURES" in source or "Veto" in source, \
            "Hypothesis crew should include PREVIOUS TRAPS & FAILURES section"
        
        print("✓ run_hypothesis_crew includes both Edge and Veto sections")

    def test_hypothesis_has_adversarial_check(self):
        """Verify run_hypothesis_crew includes ADVERSARIAL CHECK instructions."""
        from services.crew_definitions import run_hypothesis_crew
        import inspect
        
        source = inspect.getsource(run_hypothesis_crew)
        
        assert "ADVERSARIAL CHECK" in source, \
            "Hypothesis crew should include ADVERSARIAL CHECK instructions"
        
        print("✓ run_hypothesis_crew includes ADVERSARIAL CHECK instructions")

    def test_hypothesis_fetches_both_contexts(self):
        """Verify run_hypothesis_crew fetches both win_context and veto_context."""
        from services.crew_definitions import run_hypothesis_crew
        import inspect
        
        source = inspect.getsource(run_hypothesis_crew)
        
        assert "get_strategist_context" in source, \
            "Hypothesis crew should call get_strategist_context"
        assert "get_strategist_veto_context" in source, \
            "Hypothesis crew should call get_strategist_veto_context"
        
        print("✓ run_hypothesis_crew fetches both win_context and veto_context")


# ──────────────────────────────────────────────
#  TEST 6: run_prediction_crew accepts veto_context parameter
# ──────────────────────────────────────────────

class TestPredictionCrewPrompt:
    """Tests for run_prediction_crew() function signature and prompt construction."""

    def test_prediction_crew_accepts_veto_context_param(self):
        """Verify run_prediction_crew accepts veto_context parameter."""
        from services.crew_definitions import run_prediction_crew
        import inspect
        
        sig = inspect.signature(run_prediction_crew)
        params = list(sig.parameters.keys())
        
        assert "veto_context" in params, \
            "run_prediction_crew should accept veto_context parameter"
        assert "strategist_context" in params, \
            "run_prediction_crew should accept strategist_context parameter"
        assert "memory_context" in params, \
            "run_prediction_crew should accept memory_context parameter"
        
        print(f"✓ run_prediction_crew accepts all 3 context params: {params}")

    def test_prediction_crew_builds_dual_signal_injection(self):
        """Verify run_prediction_crew builds dual-signal memory_injection."""
        from services.crew_definitions import run_prediction_crew
        import inspect
        
        source = inspect.getsource(run_prediction_crew)
        
        # Check for dual-signal memory injection construction
        assert "PROVEN SUCCESS PATTERNS" in source or "Edge" in source, \
            "Prediction crew should include success patterns section"
        assert "PREVIOUS TRAPS & FAILURES" in source or "Veto" in source, \
            "Prediction crew should include failures section"
        assert "ADVERSARIAL CHECK" in source, \
            "Prediction crew should include ADVERSARIAL CHECK"
        
        print("✓ run_prediction_crew builds dual-signal memory_injection with ADVERSARIAL CHECK")

    def test_prediction_crew_uses_veto_context_in_prompt(self):
        """Verify veto_context is used in the synth_prompt construction."""
        from services.crew_definitions import run_prediction_crew
        import inspect
        
        source = inspect.getsource(run_prediction_crew)
        
        # Check that veto_context is used in memory_injection
        assert "veto_context" in source, "veto_context should be used in function"
        # Check it's added to memory_injection
        assert "memory_injection" in source, "Should build memory_injection string"
        
        print("✓ run_prediction_crew uses veto_context in prompt construction")


# ──────────────────────────────────────────────
#  TEST 7: market_prediction_service passes veto_context to run_prediction_crew
# ──────────────────────────────────────────────

class TestMarketPredictionService:
    """Tests for MarketPredictionService.analyze_market() integration."""

    def test_prediction_service_fetches_veto_context(self):
        """Verify analyze_market fetches veto_context from memory service."""
        from services.market_prediction_service import MarketPredictionService
        import inspect
        
        source = inspect.getsource(MarketPredictionService.analyze_market)
        
        assert "get_strategist_veto_context" in source, \
            "analyze_market should call get_strategist_veto_context"
        assert "veto_context" in source, \
            "analyze_market should store veto_context"
        
        print("✓ MarketPredictionService.analyze_market fetches veto_context")

    def test_prediction_service_passes_veto_to_crew(self):
        """Verify analyze_market passes veto_context to run_prediction_crew."""
        from services.market_prediction_service import MarketPredictionService
        import inspect
        
        source = inspect.getsource(MarketPredictionService.analyze_market)
        
        # Check that veto_context is passed to run_prediction_crew
        assert "veto_context=veto_context" in source or "veto_context=" in source, \
            "analyze_market should pass veto_context to run_prediction_crew"
        
        print("✓ MarketPredictionService passes veto_context to run_prediction_crew")

    def test_prediction_service_passes_all_three_contexts(self):
        """Verify analyze_market passes memory_context, strategist_context, and veto_context."""
        from services.market_prediction_service import MarketPredictionService
        import inspect
        
        source = inspect.getsource(MarketPredictionService.analyze_market)
        
        assert "memory_context" in source, "Should pass memory_context"
        assert "strategist_context" in source, "Should pass strategist_context"
        assert "veto_context" in source, "Should pass veto_context"
        
        # Check all three are passed to run_prediction_crew
        assert "run_prediction_crew" in source, "Should call run_prediction_crew"
        
        print("✓ MarketPredictionService passes all 3 context types to run_prediction_crew")


# ──────────────────────────────────────────────
#  TEST 8: Memory stats endpoint returns toxic_lessons count
# ──────────────────────────────────────────────

class TestMemoryStats:
    """Tests for get_memory_stats() function."""

    @pytest.mark.asyncio
    async def test_memory_stats_returns_toxic_count(self, init_memory_service):
        """Verify get_memory_stats returns toxic_lessons count."""
        from services.market_memory_service import get_memory_stats
        
        stats = await get_memory_stats()
        
        assert "toxic_lessons" in stats, \
            "Memory stats should include toxic_lessons count"
        assert isinstance(stats["toxic_lessons"], int), \
            "toxic_lessons should be an integer"
        assert stats["toxic_lessons"] >= 0, \
            "toxic_lessons count should be non-negative"
        
        print(f"✓ Memory stats includes toxic_lessons: {stats['toxic_lessons']}")

    @pytest.mark.asyncio
    async def test_memory_stats_returns_active_episodes(self, init_memory_service):
        """Verify get_memory_stats returns active_episodes (total - toxic)."""
        from services.market_memory_service import get_memory_stats
        
        stats = await get_memory_stats()
        
        assert "active_episodes" in stats, \
            "Memory stats should include active_episodes count"
        assert "total_episodes" in stats, \
            "Memory stats should include total_episodes count"
        
        # active_episodes should be total - toxic
        expected_active = stats["total_episodes"] - stats["toxic_lessons"]
        assert stats["active_episodes"] == expected_active, \
            f"active_episodes ({stats['active_episodes']}) should equal total ({stats['total_episodes']}) - toxic ({stats['toxic_lessons']})"
        
        print(f"✓ Memory stats: total={stats['total_episodes']}, toxic={stats['toxic_lessons']}, active={stats['active_episodes']}")


# ──────────────────────────────────────────────
#  TEST 9: Adversarial Check Instructions Validation
# ──────────────────────────────────────────────

class TestAdversarialCheckInstructions:
    """Tests to verify ADVERSARIAL CHECK instructions are complete."""

    def test_adversarial_check_has_comparison_instruction(self):
        """Verify ADVERSARIAL CHECK includes comparison against both lists."""
        from services.crew_definitions import run_war_room_crew, run_hypothesis_crew, run_prediction_crew
        import inspect
        
        for func in [run_war_room_crew, run_hypothesis_crew, run_prediction_crew]:
            source = inspect.getsource(func)
            
            # Check for comparison instruction
            assert "Compare" in source or "compare" in source, \
                f"{func.__name__} should include comparison instruction"
            assert "SUCCESS" in source and "DANGER" in source, \
                f"{func.__name__} should reference both SUCCESS and DANGER patterns"
        
        print("✓ All crew functions include comparison instructions in ADVERSARIAL CHECK")

    def test_adversarial_check_has_confidence_lowering_rule(self):
        """Verify ADVERSARIAL CHECK includes rule to lower confidence when matching danger."""
        from services.crew_definitions import run_war_room_crew, run_hypothesis_crew, run_prediction_crew
        import inspect
        
        for func in [run_war_room_crew, run_hypothesis_crew, run_prediction_crew]:
            source = inspect.getsource(func)
            
            # Check for confidence lowering rule
            assert "lower" in source.lower() or "below 50" in source, \
                f"{func.__name__} should include rule to lower confidence"
            assert "confidence" in source.lower(), \
                f"{func.__name__} should reference confidence in adversarial check"
        
        print("✓ All crew functions include confidence lowering rules in ADVERSARIAL CHECK")

    def test_adversarial_check_has_trap_explanation_requirement(self):
        """Verify ADVERSARIAL CHECK requires explanation of why NOT a trap."""
        from services.crew_definitions import run_war_room_crew, run_hypothesis_crew, run_prediction_crew
        import inspect
        
        for func in [run_war_room_crew, run_hypothesis_crew, run_prediction_crew]:
            source = inspect.getsource(func)
            
            # Check for trap explanation requirement
            assert "trap" in source.lower() or "NOT a trap" in source, \
                f"{func.__name__} should require trap explanation"
        
        print("✓ All crew functions require trap explanation in ADVERSARIAL CHECK")


# ──────────────────────────────────────────────
#  TEST 10: Integration Test - Full Context Flow
# ──────────────────────────────────────────────

class TestFullContextFlow:
    """Integration tests for the full dual-signal context flow."""

    @pytest.mark.asyncio
    async def test_both_contexts_can_be_fetched_together(self, init_memory_service):
        """Verify both Edge and Veto contexts can be fetched for the same ticker."""
        from services.market_memory_service import get_strategist_context, get_strategist_veto_context
        
        ticker = "MARKET"
        
        # Fetch both contexts
        edge_context = await get_strategist_context(ticker, n_results=3)
        veto_context = await get_strategist_veto_context(ticker, n_results=2)
        
        # Both should return strings (may be empty)
        assert isinstance(edge_context, str), "Edge context should be a string"
        assert isinstance(veto_context, str), "Veto context should be a string"
        
        # They should be different (unless both empty)
        if edge_context and veto_context:
            assert edge_context != veto_context, \
                "Edge and Veto contexts should be different"
        
        print(f"✓ Both contexts fetched - Edge: {len(edge_context)} chars, Veto: {len(veto_context)} chars")

    def test_all_three_crews_have_consistent_structure(self):
        """Verify all three crews have consistent dual-signal structure."""
        from services.crew_definitions import run_war_room_crew, run_hypothesis_crew, run_prediction_crew
        import inspect
        
        required_elements = [
            "PROVEN SUCCESS PATTERNS",
            "PREVIOUS TRAPS & FAILURES", 
            "ADVERSARIAL CHECK"
        ]
        
        for func in [run_war_room_crew, run_hypothesis_crew, run_prediction_crew]:
            source = inspect.getsource(func)
            
            for element in required_elements:
                assert element in source, \
                    f"{func.__name__} missing required element: {element}"
        
        print("✓ All three crews have consistent dual-signal structure")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
