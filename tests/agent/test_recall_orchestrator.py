from __future__ import annotations

import pytest

from agent.memory.local_memory import HermesLocalMemory
from agent.memory.recall_orchestrator import (
    AuthorityClass,
    ConflictStatus,
    RecallBudget,
    RecallCandidate,
    RecallOrchestrator,
    RecallSource,
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "profiles" / "recall"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _provider(*items):
    def provider(query, context):
        return list(items)

    return provider


def test_recall_pack_uses_deterministic_source_order_and_budgets(hermes_home):
    store = HermesLocalMemory()
    store.append_event("local hot recalls alpha", source_class="synthetic_test", origin_ref="fixture://hot", confidence=0.7)
    store.append_event("local warm recalls alpha", source_class="synthetic_test", origin_ref="fixture://warm", confidence=0.8)
    store.compact_hot(limit=1)

    orchestrator = RecallOrchestrator(
        local_memory=store,
        providers={
            RecallSource.MEM0G_SHARED: _provider({"claim": "mem0g canonical alpha", "source_ref": "mem0g://alpha", "confidence": 0.9}),
            RecallSource.SESSION_SEARCH: _provider({"claim": "session summary alpha", "source_ref": "session://alpha", "confidence": 0.6}),
        },
    )

    pack = orchestrator.recall(
        "alpha",
        context={"current_context": {"claim": "current context alpha", "source_ref": "current://alpha", "confidence": 1.0}},
        budgets=RecallBudget(max_items=3, max_chars=200, per_source_items={RecallSource.LOCAL_HOT: 0, RecallSource.LOCAL_WARM: 1}),
    )
    data = pack.to_json()

    assert data["source_order"] == [source.value for source in orchestrator.source_order]
    assert [item.source for item in pack.items] == [
        RecallSource.CURRENT_CONTEXT,
        RecallSource.MEM0G_SHARED,
        RecallSource.LOCAL_WARM,
    ]
    assert data["budget_used"]["items"] == 3
    assert any(item.suppressed_reason == "budget_exceeded" for item in pack.suppressed)


def test_mem0g_canonical_fact_suppresses_conflicting_local_memory(hermes_home):
    store = HermesLocalMemory()
    local = store.append_event(
        "Chip timezone is UTC",
        source_class="synthetic_test",
        origin_ref="fixture://local-conflict",
        confidence=0.99,
        policy_labels=["fact_key:chip.timezone", "canonical_value:UTC"],
    )

    orchestrator = RecallOrchestrator(
        local_memory=store,
        providers={
            RecallSource.MEM0G_SHARED: _provider(
                RecallCandidate(
                    claim="Chip timezone is MSK",
                    source=RecallSource.MEM0G_SHARED,
                    source_ref="mem0g://chip.timezone",
                    confidence=0.7,
                    fact_key="chip.timezone",
                    canonical_value="MSK",
                )
            )
        },
    )

    pack = orchestrator.recall("timezone", budgets=RecallBudget(max_items=5, max_chars=500))

    assert len(pack.conflicts) == 1
    assert pack.conflicts[0].winner_authority is AuthorityClass.MEM0G_CANONICAL
    assert pack.conflicts[0].suppressed_ref == f"local:hot:{local.id}"
    assert [item.claim for item in pack.items] == ["Chip timezone is MSK"]
    assert pack.suppressed[0].conflict_status is ConflictStatus.CONFLICT_SUPPRESSED
    assert pack.suppressed[0].suppressed_reason == "conflicts_with_higher_authority:mem0g://chip.timezone"


def test_live_evidence_outranks_mem0g_and_local_when_conflicting(hermes_home):
    store = HermesLocalMemory()
    store.append_event(
        "Service state is stopped",
        source_class="synthetic_test",
        origin_ref="fixture://local-service",
        confidence=0.95,
        policy_labels=["fact_key:service.mem0g", "canonical_value:stopped"],
    )
    orchestrator = RecallOrchestrator(
        local_memory=store,
        providers={
            RecallSource.MEM0G_SHARED: _provider(
                {"claim": "Service state is degraded", "source_ref": "mem0g://service", "fact_key": "service.mem0g", "canonical_value": "degraded", "confidence": 0.9}
            ),
            RecallSource.LIVE_EVIDENCE: _provider(
                {"claim": "Service state is active", "source_ref": "probe://service", "fact_key": "service.mem0g", "canonical_value": "active", "confidence": 0.8}
            ),
        },
    )

    pack = orchestrator.recall("service", budgets={"max_items": 5, "max_chars": 500})

    assert pack.items[0].source is RecallSource.LIVE_EVIDENCE
    assert pack.items[0].conflict_status is ConflictStatus.WINNER
    assert {conflict.suppressed_authority for conflict in pack.conflicts} == {AuthorityClass.MEM0G_CANONICAL, AuthorityClass.LOCAL_DERIVED}
    assert all(item.source is not RecallSource.LOCAL_HOT for item in pack.items)


def test_recall_eval_harness_twenty_synthetic_queries_merges_all_sources(hermes_home):
    store = HermesLocalMemory()
    for idx in range(20):
        store.append_event(
            f"local handoff query-{idx}: implementation note",
            source_class="synthetic_test",
            origin_ref=f"fixture://local/{idx}",
            confidence=0.6,
            policy_labels=[f"fact_key:query-{idx}", "canonical_value:local"],
        )

    def mem0g_provider(query, context):
        qid = context["qid"]
        return [
            {
                "claim": f"mem0g query-{qid}: canonical fact",
                "source_ref": f"mem0g://query-{qid}",
                "confidence": 0.9,
                "fact_key": f"query-{qid}",
                "canonical_value": "mem0g",
            }
        ]

    def session_provider(query, context):
        qid = context["qid"]
        return [{"claim": f"session query-{qid}: summary", "source_ref": f"session://query-{qid}", "confidence": 0.5}]

    orchestrator = RecallOrchestrator(
        local_memory=store,
        providers={RecallSource.MEM0G_SHARED: mem0g_provider, RecallSource.SESSION_SEARCH: session_provider},
    )

    packs = [
        orchestrator.recall(
            f"query-{idx}",
            context={"qid": idx, "current_context": {"claim": f"current query-{idx}: explicit context", "source_ref": f"current://query-{idx}", "confidence": 1.0}},
            budgets=RecallBudget(max_items=4, max_chars=500),
        )
        for idx in range(20)
    ]

    assert len(packs) == 20
    assert all(pack.items[0].source is RecallSource.CURRENT_CONTEXT for pack in packs)
    assert all(any(item.source is RecallSource.MEM0G_SHARED for item in pack.items) for pack in packs)
    assert all(any(item.source is RecallSource.SESSION_SEARCH for item in pack.items) for pack in packs)
    assert all(pack.conflicts and pack.conflicts[0].winner_authority is AuthorityClass.MEM0G_CANONICAL for pack in packs)
    assert all(any(item.source is RecallSource.LOCAL_HOT for item in pack.suppressed) for pack in packs)
