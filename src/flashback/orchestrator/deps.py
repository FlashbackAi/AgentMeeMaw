"""Dependency bundle for the Turn Orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from psycopg_pool import AsyncConnectionPool

from flashback.config import HttpConfig
from flashback.intent_classifier.classifier import IntentClassifier
from flashback.phase_gate.gate import PhaseGate
from flashback.queues.extraction import ExtractionQueueProducer
from flashback.response_generator.generator import ResponseGenerator
from flashback.retrieval.service import RetrievalService
from flashback.segment_detector.detector import SegmentDetector
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
    settings: HttpConfig | None = None
