"""Relationship-tailored archetype questions for onboarding.

The config intentionally lives in Python rather than YAML so the service
does not need another runtime dependency. ``implies`` is server-only:
the GET endpoint strips it before returning questions to Node/frontend.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

CoverageDimension = str

COVERAGE_DIMENSIONS: tuple[CoverageDimension, ...] = (
    "sensory",
    "voice",
    "place",
    "relation",
    "era",
)
ENTITY_KINDS: tuple[str, ...] = ("person", "place", "object", "organization")


def _entity(kind: str, name: str, description: str | None = None) -> dict[str, Any]:
    return {"type": kind, "name": name, "description": description}


def _option(
    option_id: str,
    label: str,
    *,
    coverage: list[str],
    entities: list[dict[str, Any]] | None = None,
    life_period_estimate: str | None = None,
) -> dict[str, Any]:
    implies: dict[str, Any] = {
        "entities": entities or [],
        "coverage": coverage,
    }
    if life_period_estimate:
        implies["life_period_estimate"] = life_period_estimate
    return {"id": option_id, "label": label, "implies": implies}


ARCHETYPES: dict[str, list[dict[str, Any]]] = {
    "friend": [
        {
            "id": "friend_meet",
            "text": "How did you two first meet?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "school",
                    "At school or college",
                    coverage=["place", "era", "relation"],
                    entities=[_entity("place", "school or college", "place of meeting")],
                    life_period_estimate="school or college years",
                ),
                _option(
                    "work",
                    "At work",
                    coverage=["place", "era", "relation"],
                    entities=[_entity("organization", "workplace", "shared workplace")],
                    life_period_estimate="working years",
                ),
                _option(
                    "through_friends",
                    "Through mutual friends",
                    coverage=["relation"],
                    entities=[_entity("person", "mutual friends", "shared friends")],
                ),
                _option(
                    "neighborhood",
                    "In the neighborhood",
                    coverage=["place", "relation"],
                    entities=[_entity("place", "neighborhood", "shared neighborhood")],
                ),
            ],
        },
        {
            "id": "friend_first_impression",
            "text": "What do you remember noticing first about them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("kindness", "They seemed kind", coverage=["voice"]),
                _option("confidence", "They seemed confident", coverage=["voice"]),
                _option("humor", "Their sense of humor", coverage=["voice", "relation"]),
                _option("quiet", "They were quiet at first", coverage=["voice"]),
            ],
        },
        {
            "id": "friend_shared_place",
            "text": "Where did you usually spend time together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "campus",
                    "Around campus",
                    coverage=["place", "era"],
                    entities=[_entity("place", "campus", "place they spent time together")],
                    life_period_estimate="school or college years",
                ),
                _option(
                    "workplace",
                    "At the workplace",
                    coverage=["place", "era"],
                    entities=[_entity("organization", "workplace", "shared workplace")],
                    life_period_estimate="working years",
                ),
                _option(
                    "home",
                    "At someone's home",
                    coverage=["place", "relation"],
                    entities=[_entity("place", "home", "place they spent time together")],
                ),
                _option(
                    "calls",
                    "Mostly on calls or messages",
                    coverage=["voice", "relation"],
                ),
            ],
        },
        {
            "id": "friend_what_drew_you",
            "text": "What kept the two of you coming back to each other?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("humor", "Their sense of humor", coverage=["voice", "relation"]),
                _option("ease", "How easy it felt", coverage=["voice", "relation"]),
                _option("shared_interests", "Shared interests", coverage=["voice", "relation"]),
                _option("loyalty", "Knowing they would show up", coverage=["voice", "relation"]),
            ],
        },
    ],
    "parent": [
        {
            "id": "parent_early_scene",
            "text": "What is an early scene with them that comes back to you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "home",
                    "At home",
                    coverage=["place", "relation"],
                    entities=[_entity("place", "home", "early family setting")],
                    life_period_estimate="childhood",
                ),
                _option(
                    "school",
                    "Around school",
                    coverage=["place", "era", "relation"],
                    entities=[_entity("place", "school", "school-related setting")],
                    life_period_estimate="childhood",
                ),
                _option(
                    "festival",
                    "During a festival or holiday",
                    coverage=["sensory", "era", "relation"],
                    life_period_estimate="childhood",
                ),
                _option("ordinary_day", "Just an ordinary day", coverage=["sensory", "relation"]),
            ],
        },
        {
            "id": "parent_everyday_ritual",
            "text": "What did they often do around the home or family?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("cooking", "Cooked or served food", coverage=["sensory", "relation"]),
                _option("work", "Worked hard for everyone", coverage=["era", "relation"]),
                _option("advice", "Gave advice", coverage=["voice", "relation"]),
                _option("quiet_care", "Helped quietly", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "parent_picture",
            "text": "Where do you most picture them being themselves?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "kitchen",
                    "In the kitchen",
                    coverage=["place", "sensory"],
                    entities=[_entity("place", "kitchen", "place they are pictured")],
                ),
                _option(
                    "work",
                    "At work",
                    coverage=["place", "era"],
                    entities=[_entity("organization", "workplace", "place they are pictured")],
                    life_period_estimate="working years",
                ),
                _option(
                    "prayer",
                    "In prayer or reflection",
                    coverage=["voice", "era"],
                    life_period_estimate="adult life",
                ),
                _option("with_family", "With family around", coverage=["relation"]),
            ],
        },
        {
            "id": "parent_taught",
            "text": "What did they teach you without sitting you down to teach?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("skill", "A practical skill", coverage=["voice", "relation"]),
                _option("value", "A value or principle", coverage=["voice", "relation"]),
                _option("habit", "A habit you still keep", coverage=["voice", "relation"]),
                _option("way_of_seeing", "A way of seeing the world", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "parent_voice_at_home",
            "text": "What's a phrase or saying of theirs you still hear?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("phrase", "A short phrase", coverage=["voice"]),
                _option("advice", "Advice they gave often", coverage=["voice", "relation"]),
                _option("joke", "A joke or teasing line", coverage=["voice", "relation"]),
                _option("scolding", "Something they said when upset", coverage=["voice", "relation"]),
            ],
        },
    ],
    "grandparent": [
        {
            "id": "grandparent_strong_memory",
            "text": "What is the strongest memory you have with them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "visit",
                    "Visiting their home",
                    coverage=["place", "relation", "sensory"],
                    entities=[_entity("place", "their home", "grandparent's home")],
                ),
                _option("food", "Food they made or shared", coverage=["sensory", "relation"]),
                _option("story", "A story they told", coverage=["voice", "relation"]),
                _option("festival", "A family gathering", coverage=["relation", "era"]),
            ],
        },
        {
            "id": "grandparent_visiting",
            "text": "What did visiting them feel like?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("warm", "Warm and busy", coverage=["sensory", "relation"]),
                _option("quiet", "Quiet and calm", coverage=["sensory", "voice"]),
                _option("crowded", "Full of people", coverage=["relation", "place"]),
                _option("special", "Like a special occasion", coverage=["sensory", "era"]),
            ],
        },
        {
            "id": "grandparent_family_story",
            "text": "What story about them comes up in your family?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("childhood", "A childhood story", coverage=["era", "relation"]),
                _option("work", "A work or responsibility story", coverage=["era"]),
                _option("kindness", "A kindness story", coverage=["voice", "relation"]),
                _option("saying", "Something they used to say", coverage=["voice"]),
            ],
        },
        {
            "id": "grandparent_keepsake",
            "text": "What of theirs do you remember holding or seeing?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "photo",
                    "A photo",
                    coverage=["sensory", "era"],
                    entities=[_entity("object", "photograph", "keepsake from the grandparent")],
                ),
                _option(
                    "jewelry",
                    "Jewelry or a small heirloom",
                    coverage=["sensory"],
                    entities=[_entity("object", "heirloom", "keepsake from the grandparent")],
                ),
                _option(
                    "kitchen",
                    "A kitchen tool or recipe",
                    coverage=["sensory", "place"],
                    entities=[_entity("object", "recipe", "kitchen keepsake")],
                ),
                _option(
                    "clothing",
                    "A piece of clothing",
                    coverage=["sensory"],
                    entities=[_entity("object", "garment", "keepsake from the grandparent")],
                ),
            ],
        },
    ],
    "spouse": [
        {
            "id": "spouse_meet",
            "text": "Where did your story together begin?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "school",
                    "School or college",
                    coverage=["place", "era", "relation"],
                    entities=[_entity("place", "school or college", "where the relationship began")],
                    life_period_estimate="young adulthood",
                ),
                _option(
                    "work",
                    "Work",
                    coverage=["place", "era", "relation"],
                    entities=[_entity("organization", "workplace", "where the relationship began")],
                    life_period_estimate="working years",
                ),
                _option("family", "Through family", coverage=["relation"]),
                _option("friends", "Through friends", coverage=["relation"]),
            ],
        },
        {
            "id": "spouse_first_scene",
            "text": "What is one early moment with them that still feels clear?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("first_conversation", "A first conversation", coverage=["voice", "relation"]),
                _option("first_outing", "A first outing", coverage=["place", "relation"]),
                _option("family_meeting", "Meeting family", coverage=["relation", "era"]),
                _option("ordinary", "Something ordinary", coverage=["sensory", "relation"]),
            ],
        },
        {
            "id": "spouse_shared_rhythm",
            "text": "What ordinary rhythm belonged to the two of you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("morning", "Mornings", coverage=["sensory", "relation"]),
                _option("meals", "Meals together", coverage=["sensory", "relation"]),
                _option("walks", "Walks or drives", coverage=["place", "relation"]),
                _option("evenings", "Evenings at home", coverage=["place", "sensory", "relation"]),
            ],
        },
        {
            "id": "spouse_inside_world",
            "text": "What's a thing only the two of you understood?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("phrase", "A phrase or word", coverage=["voice", "relation"]),
                _option("look", "A look or gesture", coverage=["voice", "relation"]),
                _option("ritual", "A small ritual", coverage=["sensory", "relation"]),
                _option("place", "A place that was yours", coverage=["place", "relation"]),
            ],
        },
        {
            "id": "spouse_in_hard_times",
            "text": "When something hard happened, what did they do?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("practical", "Took care of practical things", coverage=["voice", "relation"]),
                _option("talked", "Talked it through", coverage=["voice", "relation"]),
                _option("quiet", "Stayed quiet and close", coverage=["voice", "relation"]),
                _option("leaned_in", "Leaned in harder than usual", coverage=["voice", "relation"]),
            ],
        },
    ],
    "sibling": [
        {
            "id": "sibling_childhood_scene",
            "text": "What childhood scene with them comes back first?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("home", "At home", coverage=["place", "relation"], life_period_estimate="childhood"),
                _option("school", "At school", coverage=["place", "era", "relation"], life_period_estimate="childhood"),
                _option("play", "Playing together", coverage=["sensory", "relation"], life_period_estimate="childhood"),
                _option("fight", "A fight or argument", coverage=["voice", "relation"], life_period_estimate="childhood"),
            ],
        },
        {
            "id": "sibling_shared_mischief",
            "text": "What did the two of you get into together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("games", "Games or sports", coverage=["sensory", "relation"]),
                _option("chores", "Chores or errands", coverage=["era", "relation"]),
                _option("secrets", "Secrets or jokes", coverage=["voice", "relation"]),
                _option("trouble", "A little trouble", coverage=["relation", "era"]),
            ],
        },
        {
            "id": "sibling_family_role",
            "text": "What role did they usually have among the siblings?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("leader", "The leader", coverage=["voice", "relation"]),
                _option("peacekeeper", "The peacekeeper", coverage=["voice", "relation"]),
                _option("joker", "The joker", coverage=["voice", "relation"]),
                _option("protector", "The protector", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "sibling_what_they_brought",
            "text": "What did they bring into a room?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("laughter", "Laughter", coverage=["voice", "relation"]),
                _option("calm", "Calm", coverage=["voice", "relation"]),
                _option("energy", "Energy", coverage=["voice", "relation"]),
                _option("attention", "Attention for the youngest one there", coverage=["voice", "relation"]),
            ],
        },
    ],
    "child": [
        {
            "id": "child_energy_scene",
            "text": "What scene captures their energy best?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("playing", "Playing or exploring", coverage=["sensory", "relation"]),
                _option("learning", "Learning something", coverage=["voice", "era", "relation"]),
                _option("family", "With family", coverage=["relation"]),
                _option("outside", "Outside somewhere", coverage=["place", "sensory"]),
            ],
        },
        {
            "id": "child_voice",
            "text": "What is something they say or do that feels completely them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("phrase", "A phrase", coverage=["voice"]),
                _option("laugh", "Their laugh", coverage=["sensory", "voice"]),
                _option("habit", "A little habit", coverage=["sensory", "voice"]),
                _option("question", "The questions they ask", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "child_place",
            "text": "Where do you picture them being most themselves?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("home", "At home", coverage=["place", "relation"]),
                _option("school", "At school", coverage=["place", "era"]),
                _option("playground", "At a park or playground", coverage=["place", "sensory"]),
                _option("with_friends", "With friends", coverage=["relation"]),
            ],
        },
        {
            "id": "child_quirk",
            "text": "What's a quirk of theirs that feels completely them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("laugh", "Their laugh", coverage=["sensory", "voice"]),
                _option("gesture", "A gesture or face they make", coverage=["sensory", "voice"]),
                _option("taste", "A taste or preference", coverage=["sensory", "voice"]),
                _option("phrase", "A phrase they keep using", coverage=["voice"]),
            ],
        },
    ],
    "colleague": [
        {
            "id": "colleague_shared_work",
            "text": "Where did you work together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option(
                    "office",
                    "In an office",
                    coverage=["place", "era", "relation"],
                    entities=[_entity("organization", "office", "shared workplace")],
                    life_period_estimate="working years",
                ),
                _option(
                    "school",
                    "At a school or college",
                    coverage=["place", "era", "relation"],
                    entities=[_entity("organization", "school or college", "shared workplace")],
                    life_period_estimate="working years",
                ),
                _option("field", "Out in the field", coverage=["place", "era", "relation"]),
                _option("remote", "Mostly remote", coverage=["voice", "era", "relation"]),
            ],
        },
        {
            "id": "colleague_first_project",
            "text": "What is the first piece of work you remember doing with them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("project", "A project", coverage=["era", "relation"]),
                _option("training", "Training or onboarding", coverage=["era", "voice", "relation"]),
                _option("meeting", "A meeting", coverage=["voice", "relation"]),
                _option("deadline", "A deadline", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "colleague_work_style",
            "text": "What were they like in the middle of a workday?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("focused", "Focused", coverage=["voice"]),
                _option("helpful", "Helpful", coverage=["voice", "relation"]),
                _option("funny", "Funny", coverage=["voice", "relation"]),
                _option("calm", "Calm under pressure", coverage=["voice"]),
            ],
        },
        {
            "id": "colleague_carried_forward",
            "text": "What did you pick up from working with them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("skill", "A technical skill", coverage=["voice", "era"]),
                _option("habit", "A work habit", coverage=["voice", "era"]),
                _option("people", "A way of handling people", coverage=["voice", "relation"]),
                _option("perspective", "A perspective on the work", coverage=["voice", "era"]),
            ],
        },
    ],
    "mentor": [
        {
            "id": "mentor_first_guidance",
            "text": "What is the first thing you remember learning from them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("skill", "A practical skill", coverage=["era", "voice", "relation"]),
                _option("advice", "A piece of advice", coverage=["voice", "relation"]),
                _option("confidence", "Confidence", coverage=["voice", "relation"]),
                _option("discipline", "Discipline or standards", coverage=["voice", "era"]),
            ],
        },
        {
            "id": "mentor_setting",
            "text": "Where did their guidance usually happen?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("classroom", "In a classroom", coverage=["place", "era"], entities=[_entity("place", "classroom", "mentoring setting")]),
                _option("workplace", "At work", coverage=["place", "era"], entities=[_entity("organization", "workplace", "mentoring setting")]),
                _option("calls", "On calls or messages", coverage=["voice", "relation"]),
                _option("informal", "In informal conversations", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "mentor_method",
            "text": "How did they usually show you what mattered?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("example", "By example", coverage=["voice", "relation"]),
                _option("questions", "By asking questions", coverage=["voice", "relation"]),
                _option("correction", "By correcting carefully", coverage=["voice", "relation"]),
                _option("trust", "By trusting you", coverage=["relation", "voice"]),
            ],
        },
        {
            "id": "mentor_carried_forward",
            "text": "What do you carry from them in your own work now?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("habit", "A working habit", coverage=["voice", "era"]),
                _option("standard", "A standard you hold to", coverage=["voice", "era"]),
                _option("phrase", "Something they used to say", coverage=["voice"]),
                _option("asking", "A way of asking questions", coverage=["voice", "relation"]),
            ],
        },
    ],
    "ancestor_never_met": [
        {
            "id": "ancestor_family_story",
            "text": "What is the story you grew up hearing about them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("migration", "A migration or move", coverage=["place", "era", "relation"]),
                _option("work", "Their work or responsibility", coverage=["era"]),
                _option("courage", "A hard choice they made", coverage=["voice", "era"]),
                _option("family", "How they held family together", coverage=["relation", "voice"]),
            ],
        },
        {
            "id": "ancestor_story_source",
            "text": "Who in the family usually tells that story?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("parent", "A parent", coverage=["relation"], entities=[_entity("person", "parent", "family storyteller")]),
                _option("grandparent", "A grandparent", coverage=["relation"], entities=[_entity("person", "grandparent", "family storyteller")]),
                _option("aunt_uncle", "An aunt or uncle", coverage=["relation"], entities=[_entity("person", "aunt or uncle", "family storyteller")]),
                _option("many_people", "Several people", coverage=["relation"]),
            ],
        },
        {
            "id": "ancestor_anchor",
            "text": "What place, object, or phrase is tied to them in family memory?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("place", "A place", coverage=["place", "era"]),
                _option("object", "An object", coverage=["sensory"]),
                _option("photo", "A photo", coverage=["sensory", "era"]),
                _option("saying", "A saying", coverage=["voice"]),
            ],
        },
        {
            "id": "ancestor_in_you",
            "text": "What part of them shows up in your family today?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("trait", "A trait people still notice", coverage=["voice", "relation"]),
                _option("habit", "A habit handed down", coverage=["voice", "relation"]),
                _option("profession", "A line of work", coverage=["era", "relation"]),
                _option("name", "A name still in the family", coverage=["relation"]),
            ],
        },
    ],
    "generic": [
        {
            "id": "generic_first_picture",
            "text": "When you picture them, what comes up first?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("place", "A place", coverage=["place"]),
                _option("voice", "Their voice", coverage=["voice"]),
                _option("object", "An object", coverage=["sensory"]),
                _option("people", "People around them", coverage=["relation"]),
            ],
        },
        {
            "id": "generic_story_start",
            "text": "Where does their story start for you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("childhood", "Childhood", coverage=["era"]),
                _option("work", "Work", coverage=["era"]),
                _option("family", "Family", coverage=["relation"]),
                _option("place", "A particular place", coverage=["place"]),
            ],
        },
        {
            "id": "generic_people",
            "text": "Who else belongs in the first story about them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("family", "Family", coverage=["relation"]),
                _option("friends", "Friends", coverage=["relation"]),
                _option("colleagues", "Colleagues", coverage=["relation", "era"]),
                _option("neighbors", "Neighbors", coverage=["relation", "place"]),
            ],
        },
        {
            "id": "generic_hard_to_forget",
            "text": "What small detail about them is hard to forget?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("phrase", "A phrase", coverage=["voice"]),
                _option("object", "An object they carried", coverage=["sensory"]),
                _option("look", "A look on their face", coverage=["sensory", "voice"]),
                _option("action", "A small thing they always did", coverage=["voice", "relation"]),
            ],
        },
    ],
}


RELATIONSHIP_ALIASES: dict[str, str] = {
    "friend": "friend",
    "best friend": "friend",
    "close friend": "friend",
    "parent": "parent",
    "mother": "parent",
    "mom": "parent",
    "mum": "parent",
    "father": "parent",
    "dad": "parent",
    "grandparent": "grandparent",
    "grandmother": "grandparent",
    "grandma": "grandparent",
    "grandfather": "grandparent",
    "grandpa": "grandparent",
    "spouse": "spouse",
    "husband": "spouse",
    "wife": "spouse",
    "partner": "spouse",
    "sibling": "sibling",
    "brother": "sibling",
    "sister": "sibling",
    "child": "child",
    "son": "child",
    "daughter": "child",
    "colleague": "colleague",
    "coworker": "colleague",
    "co-worker": "colleague",
    "mentor": "mentor",
    "teacher": "mentor",
    "guide": "mentor",
    "ancestor": "ancestor_never_met",
    "ancestor never met": "ancestor_never_met",
    "never met": "ancestor_never_met",
}


def archetype_for_relationship(relationship: str | None) -> str:
    """Map a free-form relationship label to a configured archetype key."""

    value = (relationship or "").strip().lower()
    if not value:
        return "generic"
    value = value.replace("_", " ").replace("-", " ")
    compact = " ".join(value.split())

    if "never met" in compact or "ancestor" in compact:
        return "ancestor_never_met"
    if compact in RELATIONSHIP_ALIASES:
        return RELATIONSHIP_ALIASES[compact]
    for token, archetype in RELATIONSHIP_ALIASES.items():
        if token in compact:
            return archetype
    return "generic"


def public_questions_for_relationship(relationship: str | None) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(archetype, questions)`` with server-only implies removed."""

    archetype = archetype_for_relationship(relationship)
    questions = deepcopy(ARCHETYPES[archetype])
    for question in questions:
        for option in question.get("options", []):
            option.pop("implies", None)
    return archetype, questions


def questions_for_archetype(archetype: str) -> list[dict[str, Any]]:
    return ARCHETYPES.get(archetype, ARCHETYPES["generic"])


def resolve_answer(
    *,
    relationship: str | None,
    question_id: str,
    option_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve an answer against server-side config.

    Returns ``(question, option)``. ``option`` is ``None`` for free-text
    or skipped answers.
    """

    archetype = archetype_for_relationship(relationship)
    question = _find_question(questions_for_archetype(archetype), question_id)
    if question is None:
        raise ValueError(f"unknown archetype question_id {question_id!r}")
    if option_id is None:
        return question, None
    option = _find_option(question, option_id)
    if option is None:
        raise ValueError(
            f"unknown option_id {option_id!r} for question_id {question_id!r}"
        )
    return question, option


def expected_question_ids(relationship: str | None) -> set[str]:
    archetype = archetype_for_relationship(relationship)
    return {str(q["id"]) for q in questions_for_archetype(archetype)}


def sanitize_implies(value: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize static or LLM-produced implies into known dimensions/kinds."""

    value = value or {}
    coverage = [
        str(dim)
        for dim in value.get("coverage", [])
        if str(dim) in COVERAGE_DIMENSIONS
    ]
    entities: list[dict[str, Any]] = []
    for raw in value.get("entities", []) or []:
        kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
        name = str(raw.get("name") or "").strip()
        if kind not in ENTITY_KINDS or not name:
            continue
        entity: dict[str, Any] = {
            "type": kind,
            "name": name,
        }
        description = str(raw.get("description") or "").strip()
        if description:
            entity["description"] = description
        aliases = raw.get("aliases")
        if isinstance(aliases, list):
            entity["aliases"] = [str(a).strip() for a in aliases if str(a).strip()]
        attributes = raw.get("attributes")
        if isinstance(attributes, dict):
            entity["attributes"] = dict(attributes)
        entities.append(entity)

    out: dict[str, Any] = {"coverage": coverage, "entities": entities}
    life_period = str(value.get("life_period_estimate") or "").strip()
    if life_period:
        out["life_period_estimate"] = life_period
        if "era" not in out["coverage"]:
            out["coverage"].append("era")
    return out


def answer_with_label(
    *,
    question_id: str,
    option_id: str | None = None,
    label: str | None = None,
    free_text: str | None = None,
    skipped: bool = False,
) -> dict[str, Any]:
    if skipped:
        return {"question_id": question_id, "skipped": True}
    if free_text is not None:
        return {
            "question_id": question_id,
            "option_id": None,
            "free_text": free_text.strip(),
        }
    return {
        "question_id": question_id,
        "option_id": option_id,
        "label": label,
    }


def render_archetype_answers_natural_language(
    answers: list[dict[str, Any]] | None,
    relationship: str | None,
) -> str:
    """Render stored archetype answers for the starter opener prompt."""

    if not answers:
        return "No concrete onboarding details were captured."

    lines: list[str] = []
    for answer in answers:
        if answer.get("skipped"):
            continue
        question_id = str(answer.get("question_id") or "")
        try:
            question, option = resolve_answer(
                relationship=relationship,
                question_id=question_id,
                option_id=answer.get("option_id"),
            )
            question_text = str(question["text"])
        except ValueError:
            question_text = "Onboarding detail:"
            option = None
        if answer.get("free_text"):
            value = str(answer["free_text"]).strip()
        else:
            value = str(answer.get("label") or (option or {}).get("label") or "").strip()
        if not value:
            continue
        lines.append(f"- {question_text} {value}.")

    if not lines:
        return "No concrete onboarding details were captured."
    return "\n".join(lines)


def _find_question(
    questions: list[dict[str, Any]], question_id: str
) -> dict[str, Any] | None:
    for question in questions:
        if question.get("id") == question_id:
            return question
    return None


def _find_option(
    question: dict[str, Any], option_id: str
) -> dict[str, Any] | None:
    for option in question.get("options", []):
        if option.get("id") == option_id:
            return option
    return None
