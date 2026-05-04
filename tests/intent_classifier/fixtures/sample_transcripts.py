from __future__ import annotations

from datetime import datetime, timezone

from flashback.working_memory import Turn

T0 = datetime(2026, 5, 4, tzinfo=timezone.utc)

SAMPLE_TRANSCRIPTS: dict[str, list[Turn]] = {
    "clarify": [
        Turn(role="assistant", content="What did she keep on the shelf?", timestamp=T0),
        Turn(role="user", content="She always loved that one.", timestamp=T0),
    ],
    "recall": [
        Turn(role="user", content="We used to visit a cabin in winter.", timestamp=T0),
        Turn(role="assistant", content="What stayed with you about it?", timestamp=T0),
        Turn(role="user", content="What was that thing I said about the cabin?", timestamp=T0),
    ],
    "deepen": [
        Turn(role="assistant", content="What do you miss most?", timestamp=T0),
        Turn(role="user", content="I never got to say goodbye.", timestamp=T0),
    ],
    "story": [
        Turn(role="assistant", content="What happened next?", timestamp=T0),
        Turn(role="user", content="So we drove all the way up there, and the snow was already coming down.", timestamp=T0),
    ],
    "switch": [
        Turn(role="assistant", content="What else happened at the school?", timestamp=T0),
        Turn(role="user", content="I don't really remember much else about that. What else?", timestamp=T0),
    ],
}
