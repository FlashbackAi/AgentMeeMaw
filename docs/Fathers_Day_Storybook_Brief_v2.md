# Flashback — Father's Day Confession Storybook (v2)
## Product Brief — Questionnaire, Voice & Output

**Owner:** Vinay
**Feature:** Father's Day Storybook (first output of Legacy Mode)
**Scope of this revision:** the *cover*, the *voice*, and the *storybook output*.
The question bank is carried over from the original brief, unchanged in wording.
Engineering data-model and end-to-end flow are intentionally out of scope here —
the goal stays the same, only the emotional craft of the output changes.

> What's new in v2 (read this first):
> 1. **Cover = the man in his prime, stylized.** Prime-years photo rendered in
>    Flashback's painterly register, never a raw photoreal image. Falls back to
>    the legacy profile photo, run through the same style.
> 2. **A story-gated "fork in the road" line.** "He could have been something
>    else — he chose to be a father." Emitted *only* when the answers reveal a
>    real given-up path, and **rephrased fresh by the LLM** for that father.
>    Never the template string. If the story doesn't earn it, it's dropped.
> 3. **Two sentences, maximum impact.** Every slide is one or two short
>    sentences. Aphoristic weight over description. Less said, more felt.
> 4. **The questions belong to the Father's Day theme.** They surface as
>    multiple-choice cards (tappable chips + free-text + Skip) when the user taps
>    the Father's Day theme — not the main questionnaire, not open chat. The
>    question *wording* is the original bank — kept as-is.

---

## 1. What We're Building (and Why)

Flashback lets a person capture the story of someone they love **while that
person is still alive.** The Father's Day Storybook is the first, most
emotionally accessible output of Legacy Mode.

A user creates a legacy profile for their father, answers a guided set of
questions about him, and the system generates an illustrated storybook that
auto-plays as a short, shareable video — to Instagram, TikTok, and WhatsApp.

### The strategic frame
- **Father's Day is the temporary north star.** It's the hook — timely, warm,
  inherently about a *living* person. Zero association with death or memorial,
  which is exactly why it's a safe, joyful entry point.
- **Legacy is the distant north star.** Once a user is inside and has captured
  stories, the same profile becomes the foundation for everything else. We do
  **not** market that yet. Let users think this is "just a Father's Day thing" —
  that creates urgency and drives sharing.
- **The output IS the marketing.** Each generated video is unique and heartfelt.
  When shared, it pulls the next user in.

### The emotional thesis
Most people come from families that don't say the soft things out loud. This
product gives them a way to finally say them — a **confession** they never got
the chance to make. The storybook is that confession, in their own voice, told
*about* their father, to the world.

---

## 2. The Core Insight (read before building the questionnaire)

Four principles drive every decision. Violating them breaks the product.

### 2.1 Never ask directly for "the sacrifice"
Sacrifice is never one event — it's a thousand small choices layered over time
(a mother saving her slice of party cake for her child; a father selling the
house he built). A child cannot pinpoint "the biggest sacrifice." So we **never
ask "What's the biggest sacrifice your father made?"**

Instead we ask **oblique, concrete questions about the texture of his world** —
his work, where he's from, what he had as a boy, what he protected — and let the
*system infer* the sacrifice, and let the *child see it themselves* in the
finished story.

> Proof this works: Vinay never answered "what did your dad sacrifice." He told
> stories about waistcoats, a sold house, 4 a.m. shifts with no tea. From those,
> a four-sentence portrait emerged that lets a total stranger feel the weight of
> the man. **That inference is the product.**

### 2.2 The three-generation mirror (the "step forward" device)
Based on the viral format where a parent and child line up and step forward —
"Who had good shoes? Who was taken to the park?" — and *the father never steps
forward.* The silent gap between what he had and what he gave is the most
powerful proof of love.

So the questionnaire is **dual-threaded**: for key items we ask about **the
father's childhood**, then **the user's childhood**, then connect them. And
where we can, we reach **one generation further back** (the father's own
parents) — three generations sharpens the contrast and reveals the specific
shape of each father's sacrifice.

- "Did your father have a bicycle as a boy?" → "Did you?" → the gap is the gift.
- He walked ten miles selling vegetables with holes in his clothes → you rode a
  secondhand bicycle to school in clean clothes.

### 2.3 Show the man, not the worn-down version (the cover)
When kids picture their father, they see the 55-year-old — gray, tired, worn
down by the very sacrifices he made. They forget he was once 21: handsome, full
of dreams, every road still open to him.

The storybook is anchored by a **prime-years cover portrait** — the father at
roughly the age he was when the user was born — **styled in Flashback's
painterly register, never a raw photoreal image.** The stylization is the point:
it lifts him out of a snapshot and into the register of the rest of the book, so
the cover reads as *the man at the fork in the road*, not a photo on a shelf.

**Cover image rules:**
- **Source, in order:** the prime-years photo (asked in Q0.4) → if absent, the
  legacy/current profile photo. Either way, the image is rendered in the
  painterly style; we never place a photoreal image on the cover.
- **One defining phrase** sits on the cover — who he *is* at his core, stripped
  of all the sacrifice. Not his cost. The man.
- The prime face may be **echoed faintly** in transitions so it threads through
  the whole montage.

### 2.4 The fork in the road — a story-gated hero line
The most powerful single idea is the fork: *at 21 this man could have travelled
the world, kept his land, grown wealthy, stayed the main character of his own
life — and instead he chose to make his children the main characters.* Said
plainly: **"He could have been something else. He chose to be a father."**

But this line is **not true for every father**, and forcing it onto a story that
doesn't earn it loses the magic. So:

- The hero line is **story-gated.** It is emitted **only** when the answers
  reveal a concrete given-up alternative — a sold house, abandoned land, a
  dropped degree, a trade he walked away from, or money he had but never spent
  on himself.
- It is **LLM-authored, never the template.** When the gate passes, the model
  writes the fork fresh, in the user's voice, grounded in *that* father's
  specifics: *"He could have owned half that valley. He traded it for a report
  card."* / *"He had the degree in reach and let it go, so I'd have mine."*
- If the story doesn't support a fork, the line is **silently dropped.** No
  fabrication. The cover still carries the defining phrase; the book stands on
  its own.

### 2.5 Voice: first-person, to a friend, "he" not "you"
The storybook is written in **first person, from the user's point of view,
addressed to the world (a friend) — not to the father.** The father is **"he."**

- Correct: *"He walked ten miles. He sold his house. He never bought himself
  anything."*
- Wrong (addressing the father): *"You walked ten miles. You sold your house."*
- Wrong (third-person detached): *"Vinay's father walked ten miles."*

It should feel like the user is sitting with a friend, saying: *"This is who my
father actually is. Look at what he chose."* The reader may or may not be the
father — that ambiguity is intentional, and it's what makes it shareable.

### 2.6 Two sentences. Maximum impact.
Every slide is **one or two short sentences** — never a paragraph. Think
aphorism, not description: a concrete noun and a quiet cost, set side by side, so
the reader does the feeling. *"Never wrestle with a pig"* lands because it's
short and lets you fill the rest. Our lines work the same way.

- Cut every word that isn't carrying weight.
- One image, one turn. Set the thing he had against the thing he gave, and stop.
- Understated beats heightened. The restraint is the emotion.

---

## 3. The Father's Day Theme Questions

These are **not** the main Legacy Mode questionnaire and they are not the open
agent chat. They are the **archetype-style questions for the Father's Day
theme** — the set that surfaces when the user **taps the Father's Day theme**.
The user answers them up front; their answers are the priors the storybook is
generated from.

Three layers. Questions are **oblique and conversational**, never clinical.
Every question is annotated with **[Intent]** — what it actually surfaces. Many
are **optional**; skipping never breaks generation (the system works with what
it has).

### Delivery format
Each question is a **multiple-choice card** shown in the Father's Day theme flow,
not an open chat prompt: the question text, **3–4 short tappable answer chips**
(concrete examples, generated per father the same way the product's existing
theme/tap-card options are), a **free-text** field, and a **Skip** button. The
deeper beats (defining phrase, confession) are free-text-first; chips there are
just starters. A **mirror follow-up** appears as a *second* card only after the
first gets a real answer.

### LAYER 0 — Relationship & Naming (the intimacy layer)

| # | Question | Intent |
|---|----------|--------|
| 0.1 | What do you call your father? (Dad, Papa, Appa, Baba, Pops, his name, etc.) | The vocal signature of the storybook. Used verbatim throughout the slides so the voice is *theirs*, not generic "your father." |
| 0.2 | What does your father call you? (your name, a nickname, term of endearment) | Personalizes the relationship; can appear in the confession ("He still calls me ___"). |
| 0.3 | Is there a word, phrase, or joke your father always says? | A signature line that can anchor a slide and make it unmistakably *him*. |
| 0.4 | Upload a photo of your father from his **prime years** (around the age he was when you were born, if you have it). | The cover portrait. See §2.3 — represents the man before sacrifice aged him; visualizes the fork in the road. |

### LAYER 1 — World-Building (sets the plot & the world)

Purpose: give any reader enough context to *imagine the world* — geography, era,
profession, culture, socioeconomic reality. Two fathers with the same economic
background (e.g., a father and his brother) share a world; one question like
profession then splits them sharply (lineman vs. real-estate agent → fixed
budget vs. fluctuating liquidity). **Socioeconomic status is inferred, never
asked directly** ("are you poor/middle/rich?" is forbidden).

| # | Question | Intent |
|---|----------|--------|
| 1.1 | Where was your father born? (town/region/country) | Anchors the world. "Tier-3 city in South India" vs. "Fairfax, Virginia" are entirely different settings — without this, the AI may render the wrong world. |
| 1.2 | Where did you grow up with him? Did he move? | Surfaces migration/uprooting — often itself a sacrifice. |
| 1.3 | Roughly what decade was he born / what era shaped him? | Scarcity vs. abundance baseline. A father from a no-abundance era who provided in an abundance era is the whole contrast. |
| 1.4 | What was your father's main work or trade? | Highest-yield single question. Defines risk profile, income rhythm, daily texture, dignity, hours. |
| 1.5 | Was his income steady or unpredictable? | Lineman (fixed, high-risk) vs. agent (feast/famine). Shapes the *kind* of sacrifice. |
| 1.6 | Did he work for someone, or for himself? | Autonomy vs. dependence; status texture. |
| 1.7 | What language(s) did he speak at home? | Cultural texture; also relevant to the education/English thread for some users. |
| 1.8 | What faith, culture, or tradition shaped his values? (optional) | Differentiates worlds significantly; handle with sensitivity, fully skippable. |
| 1.9 | Was he raised by both parents, or did he lose someone early? | Origin wound. (Vinay's father lost his mother young — the root of his softness and his drive.) |
| 1.10 | What was his own father like? / What did his parents do? | Reaches one generation back for the three-generation mirror (§2.2). |
| 1.11 | Did he have siblings? Where did he fall among them? | Responsibility/role texture (e.g., eldest who carried the family). |

### LAYER 2 — Texture & Sacrifice (the three-generation mirror)

Purpose: surface what he had, what he gave, and the unspoken cost — **without
ever naming "sacrifice."** Built as **mirror pairs** (his childhood ↔ your
childhood) plus follow-ups that connect them. The child describes; the system
reveals.

**2A — His childhood vs. yours (the mirror pairs)**

| # | Question | Follow-up branch | Intent |
|---|----------|------------------|--------|
| 2.1 | What's something you had as a kid that mattered to you? (a bike, a school, nice clothes, a trip) | → "Did *he* have that when he was a child?" | Builds the core parallel. The gap = the gift. |
| 2.2 | What kind of clothes did you wear growing up? | → "What did *he* wear as a child? What did he wear while raising you?" | Vinay: branded vs. stitched cloth from a village tailor; holes in his own shirt as a boy. |
| 2.3 | What did your school / education look like? | → "What was *his* schooling like? Did he finish?" | The education thread. (Father failed English, gave up his degree, vowed his child would have English-medium schooling.) |
| 2.4 | What food or treats did you have freely as a kid? | → "Could *he* afford those growing up? Did he eat them himself later?" | The cake-slice / no-restaurant-meal texture. |
| 2.5 | Where and how did you get to play / rest as a kid? | → "Did *he* have safety, rest, or play as a child?" | "Let them sleep" — he protected the user's rest while denying his own. |
| 2.6 | How did you get around — bike, car, ride to school? | → "How did *he* get around at your age?" | Walked 10 miles selling vegetables ↔ used bicycle bought for the child. |

**2B — His choices & what he went without**

| # | Question | Intent |
|---|----------|--------|
| 2.7 | What's something he made sure you had that he never did? | Direct line to the central gift, framed through abundance not "sacrifice." |
| 2.8 | Did he ever uproot his life or give up something he'd built, for your sake? | The sold-house beat. The biggest forks. |
| 2.9 | What did he go without, day to day, while providing? | No tea/coffee on a 4 a.m.–4 p.m. shift; never ate out. The quiet daily cost. |
| 2.10 | Did he work unusual hours, multiple jobs, or dangerous work? | Texture of labor; risk; absence. |
| 2.11 | What did he protect or prioritize for you above himself? | Sleep, education, dignity — the thing he guarded. |
| 2.12 | Did he have money he *could* have spent on himself but didn't? | Crucial nuance: he wasn't broke; he had inheritance; he *chose* restraint. Elevates the sacrifice from necessity to choice. **Gate for the fork-in-the-road hero line (§2.4).** |
| 2.13 | What could he have become if he'd chosen himself? | The fork (§2.3 / §2.4). Acres of land, more wealth, the road not taken. Powers the prime-portrait narrative. |

**2C — The unspoken cost & the generational shift**

| # | Question | Intent |
|---|----------|--------|
| 2.14 | Did he ever talk about his own struggles, or stay silent about them? | Surfaces the stoicism most fathers carry. |
| 2.15 | What sacrifice did you only understand *later* — maybe as an adult or parent yourself? | The realization beat. (Vinay understood his father only after becoming a father.) |
| 2.16 | How is your life different from the one he had at your age? | Names the generational leap explicitly. |
| 2.17 | What cycle did he break from his own upbringing? | The breaking-of-cycles theme (motherless boy → fiercely present father). |
| 2.18 | How did his sacrifices pay off in your life and your family's? | The payoff beat — engineer, doctor, the dreams realized. |

**2D — Standing on his shoulders & the confession**

| # | Question | Intent |
|---|----------|--------|
| 2.19 | When you look at your success, how much of it rests on what he built? | "Standing on his shoulders." Guards against the child taking sole credit. |
| 2.20 | Who is your father, stripped of all the sacrifice — in one line? | Generates the **defining phrase** for the cover (§2.3). The man at his core, not his cost. |
| 2.21 | What's the one thing you've never said to him out loud? | The confession payload. Becomes the climax slide. Often "I love you / you're my role model / I admire you." |
| 2.22 | If you could give him anything now, what would it be? | The give-back beat, kept *vague* in output ("he did his job, now it's my turn") per current storyboard direction. |

### Branching rules (high level)
- Every **mirror pair** (2.1–2.6) follows: ask about the user → ask the parallel
  about the father → optionally "what do you think that meant?" Only branch into
  the parallel if the first answer has substance.
- **Profession (1.4)** is a branch hub: physical/dangerous trade vs. salaried
  vs. self-employed/irregular each unlock slightly different follow-up phrasings
  (risk, absence, instability).
- **Loss of a parent (1.9 = yes)** unlocks the origin-wound and cycle-breaking
  follow-ups (2.17).
- **"Had money but didn't spend" (2.12 = yes)** unlocks the "choice not
  necessity" framing in 2.13 — and is the **gate for the fork-in-the-road hero
  line** (§2.4).
- Sensitive items (faith 1.8, finances) are always skippable; skipping routes
  around dependent follow-ups without penalty to progress.

### Progress = sufficiency, not count
Progress is tied to *whether we can tell a true, complete story* — not how many
boxes are ticked. High-yield answers (the work, the forks, a completed mirror
pair, the confession) carry more weight. The user reaches "ready" when there's
enough to fill the arc **without fabricating.** Past that, every extra answer
makes the montage richer — no cap. *The more he gave, the more we can tell.*

---

## 4. The Storybook Output

The output is a sequence of illustrated slides that play as a short, shareable
video (~3s per slide), opening on a **cover**. Slides are **populated** from the
user's answers; the skeleton below is the fixed spine, and the lines shown are
*examples from Vinay's data* — rewritten to the two-sentence rule (§2.6).

**Global rules for every slide:**
- **Voice:** first person, user → the world, father = "he" (§2.5).
- **Length:** one or two short sentences. No paragraphs. (§2.6)
- **Image:** one symbolic painterly scene per slide — Flashback's register, not
  photoreal. Symbol over literalism.
- **Naming:** use the real term from Q0.1 (Dad / Papa / Appa) where a line names
  him.
- **No fabrication:** if a slide's source answer is thin, soften to a *true
  general* line — never invent a specific event.

### Cover (shown before slide 1)
```
┌────────────────────────────────┐
│   [PRIME-YEARS PORTRAIT]        │  ← painterly-styled, ~age 21
│   (stylized, never photoreal;   │     prime photo → else profile photo
│    falls back to profile photo) │
│                                 │
│   "A man who spent himself      │  ← DEFINING PHRASE
│    so we'd never have to."       │
│                                 │
│   [optional fork line, if       │  ← HERO LINE (§2.4) — only if the
│    earned: "He could have       │     story earned it; LLM-authored,
│    owned the valley. He chose   │     rephrased per father, else absent
│    a report card."]             │
└────────────────────────────────┘
```
**Intent:** before any story, the reader meets *the man he was* — and one line
for who he is at his core. This reframes the mental image from the worn-down
55-year-old to the 21-year-old standing at the fork.

### The 15 slides (skeleton → source → example line, two-sentence rule)

Slides are **not** mapped one-to-one to questions. The story is built dynamically
from *all* the answers; "Source" below names the texture each slide draws on, not
a fixed question.

| # | Slide (purpose) | Source (texture) | Example confession line |
|---|-----------------|------------------|------------------------|
| 1 | **The Confession** — open on the unspoken | the thing never said | *"I've carried this my whole life and never said it. So here it is, finally."* |
| 2 | **Dressed Like a Prince** — provision vs. self-denial | the clothes mirror | *"He dressed me like a prince — cake, waistcoats, the best he could find. He wore plain cloth from the village tailor, and kept whatever was left for himself."* |
| 3 | **A Boy Without His Mother** — origin wound | the origin wound | *"He lost his mother while he was still a boy. It left him gentle — quick to feel, quick to care."* |
| 4 | **The Word He Couldn't Pass** — his struggle → his vow | the education thread | *"He failed English again and again, and let his degree go. So he decided: his child would study in English, whatever it took."* |
| 5 | **The House at Twenty-Four** — what he built | what he built / gave up | *"At twenty-four, with his own money, he built one of the only concrete houses in the village."* |
| 6 | **Knocking on Doors** — the lengths he went to | what he made sure you had | *"He took me door to door to get me into the best school. I said my alphabet to a panel and got in — he still calls it one of the happiest days of his life."* |
| 7 | **Selling the Dream** — the biggest fork | the thing he uprooted | *"He sold that house — the one he built with his own hands — so we could live near my school. He let go of his proudest thing so I wouldn't be tired."* |
| 8 | **The Road Not Taken** — what he could have been | the fork in the road | *"With his work, he could have owned acres by now. He traded the land for my report card."* |
| 9 | **Steady, and Free** — discipline + room to be a kid | rest & play | *"He kept me steady — years without a missed school day. And still he left me room to just be a child."* |
| 10 | **Let Them Sleep** — what he guarded | what he protected | *"He never let anyone wake us. 'Let them sleep,' he'd say — already up and out the door himself."* |
| 11 | **Nothing for Himself** — the daily cost + the choice | the daily cost + the choice | *"Out at four in the morning, home at four — and never even a tea for himself. He wasn't broke. He just chose us, every time."* |
| 12 | **The Dreams Come True** — the payoff | the payoff | *"It all came true: I became an engineer, my sister a doctor. The man who had nothing raised both."* |
| 13 | **On His Shoulders** — credit where it's due | standing on his shoulders | *"People call it my achievement. I did the climbing — but I climbed on his shoulders."* |
| 14 | **The Things I Never Said** — the confession climax | the give-back + the confession | *"He did his job; now it's mine. So hear it, Dad: everything I am, you gave up something to build. I love you."* |
| 15 | **Closing** — the line that lingers | derived | *"They say a father hopes his children outdo him. He got his wish — and I got the best father I could have asked for."*|

> **Slide 14 is the one moment the voice may turn to "you"** — the confession,
> spoken directly to him — before the closing pulls back to the world. This
> direct-address spike is intentional and is the emotional peak. (If the team
> prefers strict "he" throughout, flag it; current direction keeps the spike.)

### Per-slide layout (wireframe)
```
┌────────────────────────────────┐
│                                 │
│     [SYMBOLIC PAINTERLY SCENE]  │  ← one moment, one image
│      (Flashback register)       │
│                                 │
│  "one or two short lines here"  │  ← from the slide's source question
│                                 │
└────────────────────────────────┘
   slide N of 15 · ~3s on screen
```

---

## 5. Voice & Tone Specification (for generation + copy)

**Do:**
- First person, the user speaking.
- Refer to the father as **"he"** (except the slide-14 climax, which may turn to
  "you").
- Use the real term from Q0.1 (Dad / Papa / Appa) when a line names him.
- **One or two short sentences per slide.** Concrete, sensory, true to the
  answers.
- Warm, honest, slightly understated. Trust the reader to feel it.

**Don't:**
- Don't write it as a letter *to* the father throughout ("you did… you gave…").
- Don't go third-person detached ("Vinay's father…").
- Don't use the word "sacrifice" as a label; *show* it.
- Don't melodramatize ("this broke me," "brutally").
- Don't pad. If a line needs a third sentence, it's two ideas — cut one.
- Don't invent specifics the user didn't give. Thin source → true general line.

---

## 6. Guardrails Summary (do not violate)
1. Never ask for "the biggest sacrifice." Ask oblique, concrete questions; infer
   the rest.
2. Never ask socioeconomic status directly. Infer it from texture.
3. Always offer a skip on sensitive items; skipping never breaks generation.
4. Never fabricate specifics. Thin slots get true general lines.
5. Voice = first person, "he," to the world (slide-14 climax excepted).
6. Cover = prime-years portrait, **stylized in Flashback's register, never
   photoreal**; fall back to the profile photo, styled the same way.
7. The "fork in the road" hero line is **story-gated and LLM-rephrased** — only
   when the answers earn it, never the template string, dropped otherwise.
8. Two sentences, maximum impact. Less said, more felt.
9. Father's Day is the hook; Legacy is the quiet seed — don't over-market the
   bigger vision yet.
