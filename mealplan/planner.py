"""Assign one recipe to each of the next seven dinners.

The shape of the problem: seven days, each with a wanted heartiness (from the
forecast) and a wanted effort (from where it falls in the week), against a
library of scored recipes. Every day/recipe pair has a cost; we want the
cheapest set of seven with no repeats.

That is an assignment problem, and seven days is small enough that a greedy
pass followed by exhaustive pairwise swaps reaches the optimum in practice
without dragging in scipy. The swap pass matters more than it looks: greedy
alone gives Monday first pick of the whole library and leaves Saturday with
whatever survived, which is exactly backwards from how the week should feel.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from .scoring import Scored, tags
from .store import DAY_NAMES
from .weather import DayWeather, describe, wanted_heartiness


# The three kinds of evening, which is what the effort rules are actually keyed
# on. Not weekday-versus-weekend: a work-from-home Wednesday has an afternoon in
# it that an office Wednesday does not, and lumping the two together is what
# makes a planner tell you to braise something on a day you are on a train.
#
#   office   out of the house, short evening, hard cap on the clock
#   home     a weekday at home -- a real cook is possible, nothing showy
#   weekend  the slot reserved for the recipes worth an afternoon
DAY_TYPES = ("office", "home", "weekend")

DEFAULT_DAY_TYPE = {
    "office": {"effort_ceiling": 0.30, "effort_target": 0.12, "max_minutes": 50},
    "home": {"effort_ceiling": 0.62, "effort_target": 0.32, "max_minutes": None},
    "weekend": {"effort_ceiling": 1.0, "effort_target": 0.78, "max_minutes": None},
}


@dataclass
class Slot:
    day: date
    weather: DayWeather
    day_type: str = "home"

    @property
    def day_name(self) -> str:
        return DAY_NAMES[self.day.weekday()]

    @property
    def is_weekend(self) -> bool:
        return self.day.weekday() >= 5

    @property
    def is_office(self) -> bool:
        return self.day_type == "office"


def day_rules(slot: Slot, config: dict) -> dict:
    """The effort rules for this slot's kind of evening."""
    table = config.get("day_types") or {}
    base = dict(DEFAULT_DAY_TYPE.get(slot.day_type, DEFAULT_DAY_TYPE["home"]))
    base.update(table.get(slot.day_type) or {})
    return base


def classify(day: date, office_days: set[str]) -> str:
    """Which kind of evening a date is, given the office days for that week.

    Office wins over weekend: someone in the office on a Saturday has a short
    evening whatever the calendar calls it.
    """
    name = DAY_NAMES[day.weekday()]
    if name in office_days or day.isoformat() in office_days:
        return "office"
    return "weekend" if day.weekday() >= 5 else "home"


@dataclass
class Choice:
    slot: Slot
    scored: Scored
    cost: float
    why: list[str] = field(default_factory=list)


def _effort_cost(s: Scored, slot: Slot, config: dict) -> float:
    """How wrong this recipe's workload is for this day.

    Two different penalties, because the two directions are not symmetric. Over
    the day's ceiling is a hard problem -- a two-hour braise on a Wednesday is a
    plan that does not survive contact with a Wednesday -- so it is quadratic
    and steep. Below the day's target is a soft one: an easy Saturday is a
    wasted opportunity, not a broken evening, so it is linear and gentle.
    """
    rules = day_rules(slot, config)
    ceiling = float(rules.get("effort_ceiling", 1.0))
    target = float(rules.get("effort_target", 0.5))

    if s.effort > ceiling:
        cost = 4.0 * (s.effort - ceiling) ** 2 + 0.35
    elif s.effort < target:
        # Undershooting is a wasted slot rather than a broken evening, but how
        # wasted depends on the evening. An easy office Tuesday is the point, so
        # it pays a gentle linear price. A weekend is the only time there is
        # *room* for a long cook, so a trivial Saturday spends the one slot that
        # could have held the ambitious recipe, and nothing else in the week can
        # take it -- hence quadratic, which stays cheap for a near miss and
        # turns expensive for a twenty-minute dinner on the night reserved for
        # more. A day at home sits between the two.
        # All three day types share one curve and differ only by coefficient, so
        # their priority is a fixed ratio at every shortfall instead of an
        # emergent property of three shapes that cross each other somewhere.
        # Both crossings were real and both were found by testing the ordering
        # across the whole range rather than on averages: a square for the
        # weekend against a 1.5 power for home met near gap 0.5, letting home
        # days win the ambitious recipes away from the nights reserved for them;
        # and a *linear* office penalty starts steeper than any fractional power
        # does, so a small shortfall cost an office day more than a home day,
        # which is backwards -- an office day wants the low-effort recipe.
        #
        # The exponent is below 2 because a square is almost free across the
        # first third of the gap, which let a free evening quietly take the
        # ten-minute salad. Below 2 keeps a near miss cheap while making a
        # trivial dinner on a free night cost about what a weather mismatch does.
        gap = target - s.effort
        weight = {"weekend": 2.6, "home": 1.2}.get(slot.day_type, 0.45)
        cost = weight * gap ** 1.5
    else:
        cost = 0.15 * (s.effort - target)

    return cost + _clock_cost(s, slot, config)


def _clock_cost(s: Scored, slot: Slot, config: dict) -> float:
    """A separate charge for the wall clock, which effort does not cover.

    A three-hour braise is fifteen minutes of work and still cannot happen on an
    office day, because it has to go in the oven at half past three and nobody
    is there. Anything that can be started in the morning and left -- a slow
    cooker, something made the night before -- is exempt however long it runs,
    which is what makes a slow cooker the ideal office-day dinner rather than a
    borderline one.

    The cap belongs to the day type, so it lifts entirely on a day at home. That
    is the real gain from splitting office days out of "weekday": the braise
    that is impossible on an office Wednesday is perfectly possible on a
    work-from-home one, and the old model could not say so.
    """
    cap = day_rules(slot, config).get("max_minutes")
    if cap is None:
        return 0.0
    cap = float(cap)
    if s.unattended or not s.minutes or s.minutes <= cap:
        return 0.0
    # Steep, and per hour over: this is a "that evening does not work" problem
    # rather than a preference, so it should outrank an excellent flavour match.
    return 0.9 + 0.6 * ((s.minutes - cap) / 60.0)


def _repeat_cost(s: Scored, recent: dict[str, int], weeks: int) -> float:
    """Recently eaten is not forbidden, just expensive, and it decays."""
    age = recent.get(s.uid)
    if age is None:
        return 0.0
    window = max(1, weeks * 7)
    return max(0.0, 1.0 - age / window)


def _source_cost(s: Scored, config: dict) -> float:
    """A discount for a recipe from a source you actually cook from.

    Expressed as a cost on everything *else* rather than a bonus, so it cannot
    drive a total negative and quietly outrank the constraints. At the default
    weight it wins any close call and beats a modestly better fit, while a real
    mismatch -- a stew on a hot night, a two-hour cook on an office day -- still
    outweighs it. That is the intended shape: a preference, not a filter.
    """
    if not config.get("preferred_sources"):
        return 0.0
    return 0.0 if s.preferred_source else float(config["weights"].get("source", 0.0))


def pair_cost(s: Scored, slot: Slot, config: dict, recent: dict[str, int]) -> float:
    w = config["weights"]
    want = wanted_heartiness(slot.weather)
    heart = abs(s.heartiness - want)
    return (
        _source_cost(s, config)
        + float(w.get("heartiness", 1.0)) * heart
        + float(w.get("effort", 1.0)) * _effort_cost(s, slot, config)
        + float(w.get("repeat", 1.0))
        * _repeat_cost(s, recent, int(config.get("repeat_memory_weeks", 4)))
    )


def _variety_cost(chosen: list[Scored], config: dict) -> float:
    """A penalty for a week that keeps eating the same thing.

    Counted over the whole week rather than per pair, because "the third
    chicken" is only expensive once there are two already.
    """
    weight = float(config["weights"].get("variety", 0.6))
    if weight <= 0:
        return 0.0
    seen: dict[str, int] = {}
    for s in chosen:
        for t in tags(s):
            seen[t] = seen.get(t, 0) + 1
    penalty = 0.0
    for tag, n in seen.items():
        if n <= 1:
            continue
        # Proteins are what a week is remembered by; a starch repeating twice
        # is barely worth mentioning.
        scale = 1.0 if tag.startswith("protein:") else 0.45
        penalty += scale * (n - 1) ** 1.5
    # No further scaling: one repeated protein should cost about what a real
    # weather mismatch costs, or "prawns twice in three days" never outweighs a
    # marginally better temperature fit and the whole term is decoration.
    return weight * penalty


def total_cost(assignment: list[Choice], config: dict) -> float:
    return (sum(c.cost for c in assignment)
            + _variety_cost([c.scored for c in assignment], config))


def _explain(s: Scored, slot: Slot, config: dict) -> list[str]:
    why: list[str] = []
    want = wanted_heartiness(slot.weather)
    gap = s.heartiness - want
    if abs(gap) <= 0.12:
        why.append(f"matches the forecast ({describe(slot.weather)})")
    elif gap > 0:
        why.append("heartier than the day strictly needs")
    else:
        why.append("lighter than the day strictly needs")

    rules = day_rules(slot, config)
    ceiling = float(rules.get("effort_ceiling", 1.0))
    cap = rules.get("max_minutes")

    if slot.day_type == "weekend" and s.effort >= 0.55:
        why.append("worth the weekend")
    elif slot.day_type == "office":
        if s.unattended and cap and s.minutes and s.minutes > float(cap):
            why.append("start it before you leave")
        elif s.effort <= ceiling:
            why.append("fast enough for an office day")
    elif slot.day_type == "home" and s.effort >= 0.35:
        why.append("you are home for this one")

    if s.hands_on and s.minutes and s.minutes - s.hands_on >= 45:
        why.append(f"{s.hands_on} min hands-on of {s.minutes}")
    elif s.minutes:
        why.append(f"{s.minutes} min total")

    if s.preferred_source and config.get("preferred_sources"):
        why.append(s.domain or "a favourite source")
    return why


def build_plan(
    library: list[Scored],
    slots: list[Slot],
    config: dict,
    recent: dict[str, int],
    pinned: dict[str, str] | None = None,
    exclude: set[str] | None = None,
    seed: int | None = None,
) -> list[Choice]:
    """Choose a recipe per slot.

    `pinned` maps an ISO date to a recipe uid that must stay put -- that is how
    a re-roll keeps the other six days steady. `exclude` drops uids from
    consideration entirely, which is how a re-roll refuses to hand back the
    recipe it was asked to replace.
    """
    pinned = pinned or {}
    exclude = exclude or set()
    rng = random.Random(seed)

    pool = [s for s in library if s.uid not in exclude]
    by_uid = {s.uid: s for s in pool}
    if not pool:
        raise ValueError("No recipes left to choose from.")

    assignment: dict[str, Choice] = {}
    used: set[str] = set()

    # Pinned days first: they are decided, and they consume their recipe so
    # nothing else can be handed it.
    for slot in slots:
        iso = slot.day.isoformat()
        uid = pinned.get(iso)
        if uid and uid in by_uid:
            s = by_uid[uid]
            assignment[iso] = Choice(slot, s, pair_cost(s, slot, config, recent),
                                     _explain(s, slot, config))
            used.add(uid)

    # Then the rest, hardest day first. A day whose best option is barely
    # better than its tenth-best has nothing to lose by going last.
    free = [s for s in slots if s.day.isoformat() not in assignment]

    def regret(slot: Slot) -> float:
        costs = sorted(pair_cost(s, slot, config, recent)
                       for s in pool if s.uid not in used)
        if len(costs) < 2:
            return 0.0
        return costs[1] - costs[0]

    for slot in sorted(free, key=regret, reverse=True):
        candidates = [s for s in pool if s.uid not in used]
        if not candidates:
            raise ValueError(
                "Not enough recipes to fill the week. The library has "
                f"{len(pool)} usable dinners for {len(slots)} days."
            )
        # Shuffle before the min so equally-good options do not always resolve
        # in library order, which is what makes two runs of the same week look
        # identical when the fit is a tie.
        rng.shuffle(candidates)

        # Variety has to be charged *here*, as the marginal cost of adding this
        # recipe to what is already picked. It cannot be left to the swap pass
        # below: a swap only moves recipes between days, so it never changes
        # which set was chosen, and a term that depends only on the set is
        # invisible to it. That is exactly how two prawn dishes landed in the
        # same week with a variety weight switched on.
        so_far = [assignment[s.day.isoformat()].scored for s in slots
                  if s.day.isoformat() in assignment]
        base_variety = _variety_cost(so_far, config)

        def cost_with_variety(cand: Scored) -> float:
            marginal = _variety_cost(so_far + [cand], config) - base_variety
            return pair_cost(cand, slot, config, recent) + marginal

        best = min(candidates, key=cost_with_variety)
        assignment[slot.day.isoformat()] = Choice(
            slot, best, pair_cost(best, slot, config, recent),
            _explain(best, slot, config))
        used.add(best.uid)

    ordered = [assignment[s.day.isoformat()] for s in slots]
    return _improve(ordered, config, recent, pinned, pool)


def _place(s: Scored, slot: Slot, config: dict, recent: dict[str, int]) -> Choice:
    return Choice(slot, s, pair_cost(s, slot, config, recent),
                  _explain(s, slot, config))


def _improve(
    assignment: list[Choice],
    config: dict,
    recent: dict[str, int],
    pinned: dict[str, str],
    pool: list[Scored],
) -> list[Choice]:
    """Local search over two moves, run to a fixed point.

    **Swap** exchanges two days. Greedy hands the earliest-considered day the
    best recipe in the library, so this is what moves Monday's showpiece onto
    Saturday: it costs Monday a little and saves Saturday a lot, and only a
    pairwise view can see that.

    **Replace** substitutes an unused recipe for a chosen one. This is the only
    move that changes *which set* was picked, which makes it the only move that
    can do anything about variety -- a swap leaves the week's ingredients
    identical and merely reorders them.
    """
    movable = [i for i, c in enumerate(assignment)
               if c.slot.day.isoformat() not in pinned]
    best = total_cost(assignment, config)

    improved = True
    rounds = 0
    while improved and rounds < 12:   # a guard, not a limit; it converges in 2-3
        improved = False
        rounds += 1

        for a in range(len(movable)):
            for b in range(a + 1, len(movable)):
                i, j = movable[a], movable[b]
                ci, cj = assignment[i], assignment[j]
                trial = list(assignment)
                trial[i] = _place(cj.scored, ci.slot, config, recent)
                trial[j] = _place(ci.scored, cj.slot, config, recent)
                cost = total_cost(trial, config)
                if cost < best - 1e-9:
                    assignment, best, improved = trial, cost, True

        used = {c.scored.uid for c in assignment}
        spare = [s for s in pool if s.uid not in used]
        for i in movable:
            slot = assignment[i].slot
            for cand in spare:
                trial = list(assignment)
                trial[i] = _place(cand, slot, config, recent)
                cost = total_cost(trial, config)
                if cost < best - 1e-9:
                    assignment, best, improved = trial, cost, True
                    used = {c.scored.uid for c in assignment}
                    spare = [s for s in pool if s.uid not in used]
                    break
    return assignment


def is_dinner(s: Scored, config: dict) -> bool:
    """Filter out anything that is plainly not a dinner.

    Category-based, because that is the one bit of curation Paprika libraries
    reliably have. A recipe with no categories at all is kept -- an uncategorised
    library should still plan, and excluding on absence would empty it.
    """
    excluded = {c.strip().lower() for c in config.get("exclude_categories", [])}
    if not excluded or not s.category_names:
        return True
    names = {n.strip().lower() for n in s.category_names}
    # Only drop it if *every* category it has is an excluded one; a recipe
    # filed under both "Dinner" and "Baking" is still dinner.
    return not names.issubset(excluded)
