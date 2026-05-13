"""Relationship-tailored archetype questions for onboarding.

The config intentionally lives in Python rather than YAML so the service
does not need another runtime dependency. ``implies`` is server-only:
the GET endpoint strips it before returning questions to Node/frontend.
"""

from __future__ import annotations

from copy import deepcopy
import re
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
                _option("school", "Through school", coverage=["place", "era", "relation"], entities=[_entity("place", "school", "place of meeting")], life_period_estimate="school years"),
                _option("work", "Through work", coverage=["place", "era", "relation"], entities=[_entity("organization", "workplace", "place of meeting")], life_period_estimate="working years"),
                _option("family", "Through family", coverage=["relation"]),
                _option("mutual_friends", "Through mutual friends", coverage=["relation"], entities=[_entity("person", "mutual friends", "shared friends")]),
                _option("online", "Online", coverage=["voice", "relation"]),
                _option("by_chance", "By chance", coverage=["place", "relation"]),
            ],
        },
        {
            "id": "friend_shared_place",
            "text": "Where did you usually spend time together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("school", "At school", coverage=["place", "era"], entities=[_entity("place", "school", "place they spent time together")], life_period_estimate="school years"),
                _option("work", "At work", coverage=["place", "era"], entities=[_entity("organization", "workplace", "place they spent time together")], life_period_estimate="working years"),
                _option("homes", "At each other's homes", coverage=["place", "relation"], entities=[_entity("place", "home", "place they spent time together")]),
                _option("outside", "Outside or around town", coverage=["place", "relation"]),
                _option("calls", "On calls or messages", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "friend_usual_activity",
            "text": "What did you usually do together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("talk", "Talk for hours", coverage=["voice", "relation"]),
                _option("go_out", "Go out", coverage=["place", "relation"]),
                _option("study_work", "Study or work", coverage=["era", "relation"]),
                _option("games_sports", "Play games or sports", coverage=["sensory", "relation"]),
                _option("eat", "Eat together", coverage=["sensory", "relation"]),
                _option("just_be", "Just be around each other", coverage=["relation"]),
            ],
        },
        {
            "id": "friend_kind",
            "text": "What kind of friend were they to you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("funny", "The funny one", coverage=["voice", "relation"]),
                _option("honest", "The honest one", coverage=["voice", "relation"]),
                _option("dependable", "The dependable one", coverage=["voice", "relation"]),
                _option("adventurous", "The adventurous one", coverage=["voice", "relation"]),
                _option("easy_to_talk_to", "The person I could talk to", coverage=["voice", "relation"]),
                _option("changed_over_time", "It changed over time", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "friend_first_memory",
            "text": "What memory comes back first?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("laughed", "A time we laughed", coverage=["voice", "relation"]),
                _option("trip", "A trip or outing", coverage=["place", "era", "relation"]),
                _option("normal_day", "A normal day together", coverage=["sensory", "relation"]),
                _option("hard_time", "A hard time they helped me through", coverage=["voice", "relation"]),
                _option("small_habit", "Something small they always did", coverage=["sensory", "voice"]),
            ],
        },
    ],
    "parent": [
        {
            "id": "parent_home_picture",
            "text": "When you picture them at home, what comes back first?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("voice", "Their voice", coverage=["voice", "place"]),
                _option("face", "Their face", coverage=["sensory", "place"]),
                _option("cooking_working", "Them cooking or working", coverage=["sensory", "era", "relation"]),
                _option("usual_place", "Them sitting in a usual place", coverage=["place", "sensory"], entities=[_entity("place", "usual place at home", "where they are pictured")]),
                _option("house_feel", "The way the house felt with them there", coverage=["place", "sensory", "relation"]),
            ],
        },
        {
            "id": "parent_ordinary_day",
            "text": "What were they like on an ordinary day?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("calm", "Calm", coverage=["voice"]),
                _option("busy", "Busy", coverage=["era", "voice"]),
                _option("funny", "Funny", coverage=["voice", "relation"]),
                _option("strict", "Strict", coverage=["voice", "relation"]),
                _option("caring", "Caring", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "parent_care",
            "text": "What did they often do for you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("advice", "Gave advice", coverage=["voice", "relation"]),
                _option("practical", "Took care of practical things", coverage=["voice", "relation"]),
                _option("food", "Made food", coverage=["sensory", "relation"]),
                _option("checked_in", "Checked in on me", coverage=["voice", "relation"]),
                _option("protected", "Protected me", coverage=["voice", "relation"]),
                _option("quiet_love", "Showed love quietly", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "parent_taught",
            "text": "What is something they taught you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("strength", "How to be strong", coverage=["voice", "relation"]),
                _option("kindness", "How to be kind", coverage=["voice", "relation"]),
                _option("work_hard", "How to work hard", coverage=["era", "voice"]),
                _option("family_care", "How to care for family", coverage=["voice", "relation"]),
                _option("handle_life", "How to handle life", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "parent_voice_at_home",
            "text": "What do you remember them saying?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("advice", "Advice", coverage=["voice", "relation"]),
                _option("joke", "A joke", coverage=["voice", "relation"]),
                _option("warning", "A warning", coverage=["voice", "relation"]),
                _option("repeated_phrase", "A phrase they repeated", coverage=["voice"]),
                _option("comfort", "A blessing or comfort", coverage=["voice", "relation"]),
            ],
        },
    ],
    "grandparent": [
        {
            "id": "grandparent_visit_first",
            "text": "When you think of visiting them, what comes back first?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("home", "Their home", coverage=["place", "relation"], entities=[_entity("place", "their home", "grandparent's home")]),
                _option("voice", "Their voice", coverage=["voice", "relation"]),
                _option("food", "Food", coverage=["sensory", "relation"]),
                _option("smell_sound", "A smell or sound", coverage=["sensory"]),
                _option("greeting", "A hug or greeting", coverage=["sensory", "relation"]),
            ],
        },
        {
            "id": "grandparent_place_feel",
            "text": "What did their place feel like?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("warm", "Warm", coverage=["sensory", "place"]),
                _option("quiet", "Quiet", coverage=["sensory", "voice"]),
                _option("busy", "Busy", coverage=["sensory", "relation"]),
                _option("old_fashioned", "Old-fashioned", coverage=["era", "place"]),
                _option("safe", "Safe", coverage=["sensory", "relation"]),
            ],
        },
        {
            "id": "grandparent_family_story",
            "text": "What do people in the family say about them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("strong", "They were strong", coverage=["voice", "relation"]),
                _option("funny", "They were funny", coverage=["voice", "relation"]),
                _option("strict", "They were strict", coverage=["voice", "relation"]),
                _option("loving", "They were loving", coverage=["voice", "relation"]),
                _option("hard_life", "They had a hard life", coverage=["era", "voice"]),
            ],
        },
        {
            "id": "grandparent_reminder",
            "text": "What reminds you of them most?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("food", "Food", coverage=["sensory", "relation"]),
                _option("photo", "A photo", coverage=["sensory", "era"], entities=[_entity("object", "photograph", "keepsake from the grandparent")]),
                _option("place", "A place", coverage=["place", "sensory"]),
                _option("smell", "A smell", coverage=["sensory"]),
                _option("phrase", "A phrase", coverage=["voice"]),
                _option("tradition", "A tradition", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "grandparent_kind",
            "text": "What kind of grandparent were they to you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("gentle", "Gentle", coverage=["voice", "relation"]),
                _option("playful", "Playful", coverage=["voice", "relation"]),
                _option("protective", "Protective", coverage=["voice", "relation"]),
                _option("wise", "Wise", coverage=["voice", "relation"]),
                _option("distant_important", "Distant but important", coverage=["relation", "era"]),
            ],
        },
    ],
    "spouse": [
        {
            "id": "spouse_meet",
            "text": "How did the two of you meet?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("friends", "Through friends", coverage=["relation"]),
                _option("family", "Through family", coverage=["relation"]),
                _option("school", "At school", coverage=["place", "era", "relation"], entities=[_entity("place", "school", "where the relationship began")], life_period_estimate="school years"),
                _option("work", "At work", coverage=["place", "era", "relation"], entities=[_entity("organization", "workplace", "where the relationship began")], life_period_estimate="working years"),
                _option("online", "Online", coverage=["voice", "relation"]),
                _option("by_chance", "By chance", coverage=["place", "relation"]),
            ],
        },
        {
            "id": "spouse_beginning",
            "text": "What do you remember about the beginning?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("first_conversation", "A first conversation", coverage=["voice", "relation"]),
                _option("first_date", "A first date", coverage=["place", "relation"]),
                _option("nervous", "Feeling nervous", coverage=["voice", "relation"]),
                _option("laughing", "Laughing together", coverage=["voice", "relation"]),
                _option("different", "Knowing something was different", coverage=["voice", "relation"]),
                _option("grew_slowly", "It took time to grow", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "spouse_ordinary_life",
            "text": "What did ordinary life together feel like?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("comfortable", "Comfortable", coverage=["sensory", "relation"]),
                _option("busy", "Busy", coverage=["era", "relation"]),
                _option("playful", "Playful", coverage=["voice", "relation"]),
                _option("peaceful", "Peaceful", coverage=["sensory", "relation"]),
                _option("routines", "Full of little routines", coverage=["sensory", "relation"]),
                _option("changed", "Different at different times", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "spouse_love",
            "text": "How did they show love?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("words", "Through words", coverage=["voice", "relation"]),
                _option("actions", "Through actions", coverage=["voice", "relation"]),
                _option("care", "Through care", coverage=["voice", "relation"]),
                _option("humor", "Through humor", coverage=["voice", "relation"]),
                _option("showing_up", "By showing up", coverage=["voice", "relation"]),
                _option("quietly", "Quietly", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "spouse_small_moment",
            "text": "What small moment comes back most?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("voice", "Their voice", coverage=["voice"]),
                _option("laugh", "Their laugh", coverage=["sensory", "voice"]),
                _option("habits", "Their habits", coverage=["sensory", "voice"]),
                _option("talking", "Talking to them", coverage=["voice", "relation"]),
                _option("being_together", "Just being together", coverage=["sensory", "relation"]),
            ],
        },
    ],
    "sibling": [
        {
            "id": "sibling_childhood_memory",
            "text": "What kind of childhood memory comes back first?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("playing", "Playing together", coverage=["sensory", "relation"], life_period_estimate="childhood"),
                _option("fighting_teasing", "Fighting or teasing", coverage=["voice", "relation"], life_period_estimate="childhood"),
                _option("trouble", "Getting in trouble", coverage=["relation", "era"], life_period_estimate="childhood"),
                _option("shared_space", "Sharing a room or space", coverage=["place", "relation"], life_period_estimate="childhood"),
                _option("family_trip", "A family trip or gathering", coverage=["place", "era", "relation"]),
            ],
        },
        {
            "id": "sibling_usual_activity",
            "text": "What did you two usually do together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("play", "Play", coverage=["sensory", "relation"]),
                _option("talk", "Talk", coverage=["voice", "relation"]),
                _option("watch", "Watch things", coverage=["sensory", "relation"]),
                _option("help", "Help each other", coverage=["voice", "relation"]),
                _option("annoy", "Annoy each other", coverage=["voice", "relation"]),
                _option("not_always_close", "We were not always close", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "sibling_family_role",
            "text": "What were they like in the family?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("funny", "The funny one", coverage=["voice", "relation"]),
                _option("responsible", "The responsible one", coverage=["voice", "relation"]),
                _option("quiet", "The quiet one", coverage=["voice", "relation"]),
                _option("bold", "The bold one", coverage=["voice", "relation"]),
                _option("caring", "The caring one", coverage=["voice", "relation"]),
                _option("changed", "Their role changed over time", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "sibling_laugh_argue",
            "text": "What did you often laugh or argue about?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("daily_things", "Small daily things", coverage=["sensory", "relation"]),
                _option("family_rules", "Family rules", coverage=["era", "relation"]),
                _option("food_belongings", "Food or belongings", coverage=["sensory", "relation"]),
                _option("inside_jokes", "Jokes only we understood", coverage=["voice", "relation"]),
                _option("who_was_right", "Who was right", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "sibling_us",
            "text": "What feels most like the two of you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("shared_joke", "A shared joke", coverage=["voice", "relation"]),
                _option("childhood_scene", "A childhood scene", coverage=["sensory", "era", "relation"]),
                _option("funny_fight", "A fight that became funny later", coverage=["voice", "relation"]),
                _option("helped_each_other", "A time we helped each other", coverage=["voice", "relation"]),
                _option("normal_family", "A normal family moment", coverage=["sensory", "relation"]),
            ],
        },
    ],
    "child": [
        {
            "id": "child_picture",
            "text": "When you picture them, what are they doing?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("playing", "Playing", coverage=["sensory", "relation"]),
                _option("talking", "Talking", coverage=["voice", "relation"]),
                _option("laughing", "Laughing", coverage=["sensory", "voice"]),
                _option("running", "Running around", coverage=["sensory", "place"]),
                _option("making", "Making something", coverage=["sensory", "voice"]),
                _option("sitting_close", "Sitting close to me", coverage=["sensory", "relation"]),
            ],
        },
        {
            "id": "child_themselves",
            "text": "What are they like when they are fully themselves?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("funny", "Funny", coverage=["voice", "relation"]),
                _option("curious", "Curious", coverage=["voice", "relation"]),
                _option("sweet", "Sweet", coverage=["voice", "relation"]),
                _option("wild", "Wild", coverage=["sensory", "voice"]),
                _option("quiet", "Quiet", coverage=["voice"]),
                _option("sensitive", "Sensitive", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "child_little_thing",
            "text": "What little thing feels completely like them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("phrase", "A phrase", coverage=["voice"]),
                _option("face", "A face they make", coverage=["sensory", "voice"]),
                _option("laugh", "A laugh", coverage=["sensory", "voice"]),
                _option("habit", "A habit", coverage=["sensory", "voice"]),
                _option("movement", "A way they move", coverage=["sensory", "voice"]),
                _option("object", "A favorite object or toy", coverage=["sensory"], entities=[_entity("object", "favorite object or toy", "object connected to the child")]),
            ],
        },
        {
            "id": "child_place",
            "text": "Where do you picture them most clearly?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("home", "At home", coverage=["place", "relation"]),
                _option("room", "In their room", coverage=["place", "sensory"], entities=[_entity("place", "their room", "place they are pictured")]),
                _option("outside", "Outside", coverage=["place", "sensory"]),
                _option("school", "At school", coverage=["place", "era"], entities=[_entity("place", "school", "place they are pictured")]),
                _option("family", "With family", coverage=["relation"]),
                _option("favorite_place", "In a favorite place", coverage=["place", "sensory"]),
            ],
        },
        {
            "id": "child_remember",
            "text": "What kind of moment do you want to remember most?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("funny", "A funny moment", coverage=["voice", "relation"]),
                _option("quiet", "A quiet moment", coverage=["sensory", "relation"]),
                _option("proud", "A proud moment", coverage=["voice", "relation"]),
                _option("tender", "A tender moment", coverage=["sensory", "relation"]),
                _option("everyday", "An everyday moment", coverage=["sensory", "relation"]),
                _option("all_of_it", "All of it", coverage=["sensory", "voice", "relation"]),
            ],
        },
    ],
    "colleague": [
        {
            "id": "colleague_start",
            "text": "How did you start working together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("same_team", "Same team", coverage=["relation", "era"]),
                _option("same_project", "Same project", coverage=["era", "relation"]),
                _option("same_office", "Same office", coverage=["place", "era", "relation"], entities=[_entity("organization", "office", "shared workplace")]),
                _option("they_trained_me", "They trained me", coverage=["voice", "relation"]),
                _option("i_trained_them", "I trained them", coverage=["voice", "relation"]),
                _option("crossed_paths", "We crossed paths often", coverage=["place", "relation"]),
            ],
        },
        {
            "id": "colleague_workday",
            "text": "What were they like during a normal workday?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("focused", "Focused", coverage=["voice"]),
                _option("helpful", "Helpful", coverage=["voice", "relation"]),
                _option("funny", "Funny", coverage=["voice", "relation"]),
                _option("calm", "Calm under pressure", coverage=["voice"]),
                _option("direct", "Direct", coverage=["voice"]),
                _option("warm", "Warm with people", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "colleague_together",
            "text": "What do you remember doing together?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("meetings", "Meetings", coverage=["voice", "relation"]),
                _option("project", "A project", coverage=["era", "relation"]),
                _option("long_days", "Long workdays", coverage=["era", "relation"]),
                _option("problems", "Solving problems", coverage=["voice", "relation"]),
                _option("casual_talk", "Casual conversations", coverage=["voice", "relation"]),
                _option("work_events", "Work trips or events", coverage=["place", "era", "relation"]),
            ],
        },
        {
            "id": "colleague_appreciated",
            "text": "What did people appreciate about them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("kindness", "Their kindness", coverage=["voice", "relation"]),
                _option("skill", "Their skill", coverage=["voice", "era"]),
                _option("humor", "Their humor", coverage=["voice", "relation"]),
                _option("reliability", "Their reliability", coverage=["voice", "relation"]),
                _option("leadership", "Their leadership", coverage=["voice", "relation"]),
                _option("honesty", "Their honesty", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "colleague_learned",
            "text": "What did you learn from working with them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("work_better", "How to do the work better", coverage=["voice", "era"]),
                _option("treat_people", "How to treat people", coverage=["voice", "relation"]),
                _option("stay_calm", "How to stay calm", coverage=["voice"]),
                _option("lead", "How to lead", coverage=["voice", "relation"]),
                _option("keep_going", "How to keep going", coverage=["voice", "era"]),
            ],
        },
    ],
    "mentor": [
        {
            "id": "mentor_guidance",
            "text": "How did they help or guide you?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("advice", "Gave advice", coverage=["voice", "relation"]),
                _option("taught_directly", "Taught me directly", coverage=["voice", "era", "relation"]),
                _option("believed", "Believed in me", coverage=["voice", "relation"]),
                _option("challenged", "Challenged me", coverage=["voice", "relation"]),
                _option("opened_doors", "Opened doors", coverage=["era", "relation"]),
                _option("example", "Led by example", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "mentor_setting",
            "text": "Where did you usually learn from them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("classroom", "In a classroom", coverage=["place", "era"], entities=[_entity("place", "classroom", "mentoring setting")]),
                _option("work", "At work", coverage=["place", "era"], entities=[_entity("organization", "workplace", "mentoring setting")]),
                _option("conversations", "In conversations", coverage=["voice", "relation"]),
                _option("messages", "Through messages or calls", coverage=["voice", "relation"]),
                _option("watching", "By watching them", coverage=["voice", "relation"]),
                _option("years", "Over many years", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "mentor_kind",
            "text": "What kind of teacher or mentor were they?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("patient", "Patient", coverage=["voice", "relation"]),
                _option("tough_caring", "Tough but caring", coverage=["voice", "relation"]),
                _option("encouraging", "Encouraging", coverage=["voice", "relation"]),
                _option("wise", "Wise", coverage=["voice", "relation"]),
                _option("practical", "Practical", coverage=["voice", "era"]),
                _option("quiet_support", "Quietly supportive", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "mentor_taught",
            "text": "What did they teach you that stayed?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("confidence", "Confidence", coverage=["voice", "relation"]),
                _option("discipline", "Discipline", coverage=["voice", "era"]),
                _option("kindness", "Kindness", coverage=["voice", "relation"]),
                _option("skill", "A skill", coverage=["voice", "era"]),
                _option("thinking", "A way of thinking", coverage=["voice", "era"]),
                _option("living", "A way of living", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "mentor_carry",
            "text": "What do you still carry from them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("advice", "Their advice", coverage=["voice", "relation"]),
                _option("belief", "Their belief in me", coverage=["voice", "relation"]),
                _option("example", "Their example", coverage=["voice", "relation"]),
                _option("phrase", "A phrase they said", coverage=["voice"]),
                _option("lesson", "A lesson", coverage=["voice", "era"]),
                _option("feeling", "A feeling", coverage=["sensory", "relation"]),
            ],
        },
    ],
    "ancestor_never_met": [
        {
            "id": "ancestor_known_through",
            "text": "What do you know them through?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("stories", "Family stories", coverage=["voice", "relation"]),
                _option("photos", "Photos", coverage=["sensory", "era"], entities=[_entity("object", "photographs", "photos connected to the ancestor")]),
                _option("place", "A place", coverage=["place", "era"]),
                _option("name", "A name", coverage=["relation"]),
                _option("traditions", "Traditions", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "ancestor_story_source",
            "text": "Who usually talks about them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("parent", "A parent", coverage=["relation"], entities=[_entity("person", "parent", "family storyteller")]),
                _option("grandparent", "A grandparent", coverage=["relation"], entities=[_entity("person", "grandparent", "family storyteller")]),
                _option("aunt_uncle", "An aunt or uncle", coverage=["relation"], entities=[_entity("person", "aunt or uncle", "family storyteller")]),
                _option("cousin_sibling", "An older sibling or cousin", coverage=["relation"], entities=[_entity("person", "older sibling or cousin", "family storyteller")]),
                _option("many_people", "Many people in the family", coverage=["relation"]),
            ],
        },
        {
            "id": "ancestor_trait",
            "text": "What do people say they were like?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("strong", "Strong", coverage=["voice", "relation"]),
                _option("kind", "Kind", coverage=["voice", "relation"]),
                _option("strict", "Strict", coverage=["voice", "relation"]),
                _option("brave", "Brave", coverage=["voice", "era"]),
                _option("funny", "Funny", coverage=["voice", "relation"]),
                _option("mysterious", "Mysterious", coverage=["voice"]),
            ],
        },
        {
            "id": "ancestor_connected",
            "text": "What is connected to them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("photo", "A photo", coverage=["sensory", "era"], entities=[_entity("object", "photograph", "object connected to the ancestor")]),
                _option("home_village", "A home or village", coverage=["place", "era"], entities=[_entity("place", "home or village", "place connected to the ancestor")]),
                _option("object", "An object", coverage=["sensory"], entities=[_entity("object", "family object", "object connected to the ancestor")]),
                _option("recipe", "A recipe", coverage=["sensory", "relation"], entities=[_entity("object", "recipe", "recipe connected to the ancestor")]),
                _option("phrase", "A phrase", coverage=["voice"]),
                _option("tradition", "A family tradition", coverage=["era", "relation"]),
            ],
        },
        {
            "id": "ancestor_present",
            "text": "What part of them still feels present?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("name", "Their name", coverage=["relation"]),
                _option("values", "Their values", coverage=["voice", "relation"]),
                _option("story", "Their story", coverage=["voice", "era"]),
                _option("struggles", "Their struggles", coverage=["era", "voice"]),
                _option("traditions", "Their traditions", coverage=["era", "relation"]),
                _option("resemblance", "Their resemblance in someone", coverage=["relation", "sensory"]),
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
                _option("face", "Their face", coverage=["sensory"]),
                _option("voice", "Their voice", coverage=["voice"]),
                _option("place", "A place", coverage=["place"]),
                _option("object", "An object", coverage=["sensory"]),
                _option("people", "People around them", coverage=["relation"]),
            ],
        },
        {
            "id": "generic_ordinary_day",
            "text": "What were they like on an ordinary day?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("calm", "Calm", coverage=["voice"]),
                _option("busy", "Busy", coverage=["era", "voice"]),
                _option("funny", "Funny", coverage=["voice", "relation"]),
                _option("quiet", "Quiet", coverage=["voice"]),
                _option("caring", "Caring", coverage=["voice", "relation"]),
            ],
        },
        {
            "id": "generic_people",
            "text": "Who comes up when you think about them?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("family", "Family", coverage=["relation"]),
                _option("friends", "Friends", coverage=["relation"]),
                _option("colleagues", "People from work", coverage=["relation", "era"]),
                _option("neighbors", "Neighbors", coverage=["relation", "place"]),
                _option("community", "Community or faith people", coverage=["relation", "era"]),
            ],
        },
        {
            "id": "generic_reminder",
            "text": "What reminds you of them most?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("food", "Food", coverage=["sensory", "relation"]),
                _option("photo", "A photo", coverage=["sensory", "era"]),
                _option("place", "A place", coverage=["place"]),
                _option("smell", "A smell", coverage=["sensory"]),
                _option("phrase", "A phrase", coverage=["voice"]),
                _option("habit", "A habit", coverage=["sensory", "voice"]),
            ],
        },
        {
            "id": "generic_memory",
            "text": "What memory or story comes back first?",
            "allow_free_text": True,
            "allow_skip": True,
            "options": [
                _option("normal_day", "A normal day", coverage=["sensory", "relation"]),
                _option("family_story", "A family story", coverage=["voice", "relation"]),
                _option("funny", "A funny moment", coverage=["voice", "relation"]),
                _option("hard_time", "A hard time", coverage=["era", "voice"]),
                _option("small_detail", "Something small they did", coverage=["sensory", "voice"]),
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


_PRONOUNS = {
    "he": {
        "they": "he",
        "them": "him",
        "their": "his",
        "theirs": "his",
        "are": "is",
        "were": "was",
    },
    "she": {
        "they": "she",
        "them": "her",
        "their": "her",
        "theirs": "hers",
        "are": "is",
        "were": "was",
    },
    "they": {
        "they": "they",
        "them": "them",
        "their": "their",
        "theirs": "theirs",
        "are": "are",
        "were": "were",
    },
}


def render_pronouns(text: str, gender: str | None) -> str:
    """Render neutral onboarding copy with the subject's pronouns."""

    forms = _PRONOUNS.get((gender or "they").strip().lower(), _PRONOUNS["they"])
    if forms is _PRONOUNS["they"]:
        return text

    phrase_replacements = (
        ("What were they like", f"What {forms['were']} {forms['they']} like"),
        ("what were they like", f"what {forms['were']} {forms['they']} like"),
        ("What kind of grandparent were they", f"What kind of grandparent {forms['were']} {forms['they']}"),
        ("what kind of grandparent were they", f"what kind of grandparent {forms['were']} {forms['they']}"),
        ("when they are", f"when {forms['they']} {forms['are']}"),
        ("When they are", f"When {forms['they']} {forms['are']}"),
        ("they are", f"{forms['they']} {forms['are']}"),
        ("They are", f"{forms['they'].capitalize()} {forms['are']}"),
        ("they were", f"{forms['they']} {forms['were']}"),
        ("They were", f"{forms['they'].capitalize()} {forms['were']}"),
    )
    rendered = text
    for source, replacement in phrase_replacements:
        rendered = rendered.replace(source, replacement)

    word_replacements = (
        ("theirs", forms["theirs"]),
        ("Theirs", forms["theirs"].capitalize()),
        ("their", forms["their"]),
        ("Their", forms["their"].capitalize()),
        ("them", forms["them"]),
        ("Them", forms["them"].capitalize()),
        ("they", forms["they"]),
        ("They", forms["they"].capitalize()),
    )
    for source, replacement in word_replacements:
        rendered = re.sub(rf"\b{source}\b", replacement, rendered)
    return rendered


def public_questions_for_relationship(
    relationship: str | None, *, gender: str | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(archetype, questions)`` with server-only implies removed."""

    archetype = archetype_for_relationship(relationship)
    questions = deepcopy(ARCHETYPES[archetype])
    for question in questions:
        question["text"] = render_pronouns(str(question["text"]), gender)
        for option in question.get("options", []):
            option["label"] = render_pronouns(str(option["label"]), gender)
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
    gender: str | None = None,
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
            question_text = render_pronouns(str(question["text"]), gender)
        except ValueError:
            question_text = "Onboarding detail:"
            option = None
        if answer.get("free_text"):
            value = str(answer["free_text"]).strip()
        else:
            value = str(answer.get("label") or (option or {}).get("label") or "").strip()
            value = render_pronouns(value, gender)
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
