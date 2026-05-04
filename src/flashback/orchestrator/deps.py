"""Dependency bundle for the Turn Orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.intent_classifier.classifier import IntentClassifier
from flashback.phase_gate.gate import PhaseGate
from flashback.queues.extraction import ExtractionQueueProducer
from flashback.queues.producers_per_session import ProducersPerSessionQueueProducer
from flashback.queues.profile_summary import ProfileSummaryQueueProducer
from flashback.queues.trait_synthesizer import TraitSynthesizerQueueProducer
from flashback.response_generator.generator import ResponseGenerator
from flashback.retrieval.service import RetrievalService
from flashback.segment_detector.detector import SegmentDetector
from flashback.session_summary.generator import SessionSummaryGenerator
from flashback.working_memory.client import WorkingMemory


@dataclass(frozen=True, slots=True)
class OrchestratorDeps:
    """All dependencies the orchestrator needs.

    Constructed once at startup in ``flashback.http.app`` and passed
    into the Orchestrator constructor.
    """

    db_pool: AsyncConnectionPool
    working_memory: WorkingMemory
    intent_classifier: IntentClassifier | None
    retrieval: RetrievalService | None
    phase_gate: PhaseGate | None
    response_generator: ResponseGenerator | None
    segment_detector: SegmentDetector | None = None
    extraction_queue: ExtractionQueueProducer | None = None
    session_summary_generator: SessionSummaryGenerator | None = None
    trait_synthesizer_queue: TraitSynthesizerQueueProducer | None = None
    profile_summary_queue: ProfileSummaryQueueProducer | None = None
    producers_per_session_queue: ProducersPerSessionQueueProducer | None = None
    settings: HttpConfig | None = None
