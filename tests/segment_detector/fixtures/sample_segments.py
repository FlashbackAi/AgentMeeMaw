from __future__ import annotations

from datetime import datetime, timezone

from flashback.working_memory import Turn

T0 = datetime(2026, 5, 4, tzinfo=timezone.utc)

SAMPLE_SEGMENT = [
    Turn(role="user", content="She made pasta every Sunday.", timestamp=T0),
    Turn(role="assistant", content="What do you remember about the kitchen?", timestamp=T0),
    Turn(role="user", content="It smelled like basil and flour.", timestamp=T0),
    Turn(role="assistant", content="That sounds vivid and warm.", timestamp=T0),
]
