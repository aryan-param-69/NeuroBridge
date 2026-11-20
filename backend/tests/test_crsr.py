"""CRS-R scoring: every valid item combination, range validation, and the pattern check."""

import itertools

import pytest

from app.services import crsr

RANGES = {"auditory": 4, "visual": 5, "motor": 6, "oromotor_verbal": 3, "communication": 2, "arousal": 3}


def all_combinations():
    return itertools.product(*(range(m + 1) for m in RANGES.values()))


def test_definition_is_a_23_point_scale_with_contiguous_items():
    d = crsr.definition()
    assert d["max_total"] == 23 and sum(s["max"] for s in d["subscales"]) == 23
    assert {s["key"]: s["max"] for s in d["subscales"]} == RANGES
    for s in d["subscales"]:
        assert sorted(i["level"] for i in s["items"]) == list(range(s["max"] + 1))  # one item per level, 0..max
        assert all(i["label"].strip() for i in s["items"])
        assert [i for i in s["items"] if i["level"] == 0][0]["marker"] is None
    mk = {(s["key"], i["level"]): i["marker"] for s in d["subscales"] for i in s["items"] if i["marker"]}
    assert {k for k, v in mk.items() if v == "eMCS"} == {("motor", 6), ("communication", 2)}
    assert {k for k, v in mk.items() if v == "MCS"} == {("auditory", 3), ("auditory", 4), ("visual", 2), ("visual", 3), ("visual", 4),
                                                         ("visual", 5), ("motor", 3), ("motor", 4), ("motor", 5), ("oromotor_verbal", 3),
                                                         ("communication", 1)}


def test_total_is_correct_for_all_10080_valid_combinations():
    n = 0
    seen_totals = set()
    for combo in all_combinations():
        scores = dict(zip(RANGES, combo, strict=True))
        t = crsr.total(scores)
        assert t == sum(combo) and 0 <= t <= 23
        seen_totals.add(t)
        n += 1
    assert n == 5 * 6 * 7 * 4 * 3 * 4 == 10080
    assert seen_totals == set(range(24))  # every total from 0 to 23 is reachable


def test_extremes():
    assert crsr.total({k: 0 for k in RANGES}) == 0
    assert crsr.total(dict(RANGES)) == 23


@pytest.mark.parametrize("key,bad", [(k, v) for k, m in RANGES.items() for v in (-1, m + 1, 99)] +
                         [("auditory", 1.5), ("auditory", "2"), ("motor", None), ("arousal", True)])
def test_out_of_range_or_wrong_type_is_rejected(key, bad):
    scores = {k: 0 for k in RANGES}
    scores[key] = bad
    with pytest.raises(crsr.ScoreError):
        crsr.total(scores)


def test_missing_and_unknown_subscales_are_rejected():
    with pytest.raises(crsr.ScoreError):
        crsr.total({"auditory": 1})
    with pytest.raises(crsr.ScoreError, match="Unknown"):
        crsr.total({**{k: 0 for k in RANGES}, "mood": 1})


def profile(**kw):
    return {**{k: 0 for k in RANGES}, **kw}


@pytest.mark.parametrize("scores,pattern", [
    (profile(), "UWS/VS"),
    (profile(auditory=2, visual=1, motor=2, oromotor_verbal=2, arousal=2), "UWS/VS"),  # highest reflexive profile
    (profile(visual=2), "MCS-"), (profile(visual=5), "MCS-"), (profile(motor=3), "MCS-"), (profile(motor=5), "MCS-"),
    (profile(auditory=3), "MCS+"), (profile(auditory=4), "MCS+"), (profile(oromotor_verbal=3), "MCS+"), (profile(communication=1), "MCS+"),
    (profile(visual=3, auditory=3), "MCS+"),  # MCS+ takes precedence over MCS- markers
    (profile(motor=6), "eMCS"), (profile(communication=2), "eMCS"), (profile(motor=6, auditory=4, visual=5), "eMCS"),
    (dict(RANGES), "eMCS"),
])
def test_pattern_for_canonical_profiles(scores, pattern):
    assert crsr.pattern_check(scores)["pattern"] == pattern


def test_pattern_agrees_with_the_published_definitions_for_every_combination():
    """Independent re-statement of the rules, checked on all 10 080 combinations."""
    for combo in all_combinations():
        a, v, m, o, c, ar = combo
        scores = dict(zip(RANGES, combo, strict=True))
        pat = crsr.pattern_check(scores)["pattern"]
        if m == 6 or c == 2:
            assert pat == "eMCS"
        elif a >= 3 or o == 3 or c == 1:
            assert pat == "MCS+"
        elif v >= 2 or 3 <= m <= 5:
            assert pat == "MCS-"
        else:
            assert pat == "UWS/VS"
            assert a <= 2 and v <= 1 and m <= 2 and o <= 2 and c == 0  # the CRS-R criteria for UWS/VS


def test_pattern_check_is_a_prompt_not_a_verdict():
    r = crsr.pattern_check(profile(auditory=3), entered="MCS-")
    assert r["pattern"] == "MCS+" and r["differs_from_entered"] and "judgement" in r["message"] and "Not a diagnosis" in r["disclaimer"]
    same = crsr.pattern_check(profile(visual=2), entered="MCS-")
    assert not same["differs_from_entered"] and same["message"] is None
    assert not crsr.pattern_check(profile(visual=2), entered="other")["differs_from_entered"]  # no check for 'other'
    assert not crsr.pattern_check(profile(visual=2))["differs_from_entered"]
    odd = crsr.pattern_check(profile(arousal=3))
    assert odd["pattern"] == "UWS/VS" and "re-check" in odd["scoring_note"]
