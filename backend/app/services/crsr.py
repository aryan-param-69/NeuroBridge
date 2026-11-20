"""Coma Recovery Scale-Revised (CRS-R): item definitions, scoring and a gentle pattern check.

Source: Giacino JT, Kalmar K, Whyte J. The JFK Coma Recovery Scale-Revised: measurement characteristics and
diagnostic utility. Arch Phys Med Rehabil 2004;85:2020-9. MCS+ / MCS- split after Bruno et al., 2011.

IMPORTANT: the item labels, the 23-point structure and the pattern rules below are transcribed from published
descriptions. They MUST be checked against the official CRS-R Administration and Scoring Guidelines before any
clinical use. The pattern check is a prompt to re-check scoring; it never decides a category.

Scoring rule: each subscale score is the level of the HIGHEST item observed; the total is the sum (0-23).
"""

from dataclasses import dataclass

CRSR_VERSION = "CRS-R (Giacino 2004), 23-point"


@dataclass(frozen=True)
class Item:
    level: int
    label: str
    marker: str | None = None  # "MCS" (asterisk in the CRS-R) or "eMCS" (dagger)


@dataclass(frozen=True)
class Subscale:
    key: str
    label: str
    items: tuple[Item, ...]

    @property
    def max(self) -> int:
        return max(i.level for i in self.items)


SUBSCALES: tuple[Subscale, ...] = (
    Subscale("auditory", "Auditory function", (
        Item(4, "Consistent movement to command", "MCS"), Item(3, "Reproducible movement to command", "MCS"),
        Item(2, "Localization to sound"), Item(1, "Auditory startle"), Item(0, "None"))),
    Subscale("visual", "Visual function", (
        Item(5, "Object recognition", "MCS"), Item(4, "Object localization: reaching", "MCS"),
        Item(3, "Visual pursuit", "MCS"), Item(2, "Fixation", "MCS"), Item(1, "Visual startle"), Item(0, "None"))),
    Subscale("motor", "Motor function", (
        Item(6, "Functional object use", "eMCS"), Item(5, "Automatic motor response", "MCS"),
        Item(4, "Object manipulation", "MCS"), Item(3, "Localization to noxious stimulation", "MCS"),
        Item(2, "Flexion withdrawal"), Item(1, "Abnormal posturing"), Item(0, "None / flaccid"))),
    Subscale("oromotor_verbal", "Oromotor / verbal function", (
        Item(3, "Intelligible verbalization", "MCS"), Item(2, "Vocalization / oral movement"),
        Item(1, "Oral reflexive movement"), Item(0, "None"))),
    Subscale("communication", "Communication", (
        Item(2, "Functional: accurate", "eMCS"), Item(1, "Non-functional: intentional", "MCS"), Item(0, "None"))),
    Subscale("arousal", "Arousal", (
        Item(3, "Attention"), Item(2, "Eye opening without stimulation"), Item(1, "Eye opening with stimulation"),
        Item(0, "Unarousable"))),
)
KEYS = tuple(s.key for s in SUBSCALES)
MAX = {s.key: s.max for s in SUBSCALES}
MAX_TOTAL = sum(MAX.values())  # 23

CATEGORIES = ("UWS/VS", "MCS-", "MCS+", "eMCS", "other")
CONFOUNDERS = ("aphasia", "motor_impairment", "visual_impairment", "hearing_impairment", "fatigue", "pain", "seizure",
               "infection", "medication_effects", "other")
SEDATION = ("none", "reduced", "ongoing", "unknown")


def definition() -> dict:
    return {
        "instrument": "CRS-R", "version": CRSR_VERSION, "max_total": MAX_TOTAL,
        "rule": "Score each subscale at the highest item observed. The total is the sum of the six subscale scores.",
        "verify": "Check against the official CRS-R Administration and Scoring Guidelines before clinical use.",
        "subscales": [{"key": s.key, "label": s.label, "max": s.max,
                       "items": [{"level": i.level, "label": i.label, "marker": i.marker} for i in s.items]} for s in SUBSCALES],
        "categories": list(CATEGORIES), "confounders": list(CONFOUNDERS), "sedation": list(SEDATION),
        "markers": {"MCS": "behaviour marked with an asterisk in the CRS-R (minimally conscious state)",
                    "eMCS": "behaviour marked with a dagger in the CRS-R (emergence from MCS)"},
    }


class ScoreError(ValueError):
    pass


def validate_scores(scores: dict) -> dict[str, int]:
    out = {}
    for k in KEYS:
        v = scores.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX[k]:
            raise ScoreError(f"{k} must be a whole number from 0 to {MAX[k]}")
        out[k] = v
    extra = set(scores) - set(KEYS)
    if extra:
        raise ScoreError(f"Unknown subscale(s): {sorted(extra)}")
    return out


def total(scores: dict) -> int:
    return sum(validate_scores(scores).values())


def pattern_check(scores: dict, entered: str | None = None) -> dict:
    """Which category the item pattern is consistent with, per the CRS-R scoring guideline. Never a diagnosis."""
    s = validate_scores(scores)
    emerged = [n for n, ok in (("functional object use", s["motor"] == 6), ("functional communication", s["communication"] == 2)) if ok]
    plus = [n for n, ok in (("command following", s["auditory"] >= 3), ("intelligible verbalization", s["oromotor_verbal"] == 3),
                            ("non-functional intentional communication", s["communication"] == 1)) if ok]
    minus = [n for n, ok in (("visual fixation / pursuit / localization / recognition", s["visual"] >= 2),
                             ("localization to noxious stimulation or automatic / manipulative motor response", 3 <= s["motor"] <= 5)) if ok]
    note = None
    if emerged:
        pattern = "eMCS"
    elif plus:
        pattern = "MCS+"
    elif minus:
        pattern = "MCS-"
    else:
        pattern = "UWS/VS"
        if s["arousal"] == 3:
            note = "No behavioural sign of consciousness is scored although arousal is scored 'attention': please re-check the scoring."
    message = None
    if entered in ("UWS/VS", "MCS-", "MCS+", "eMCS") and entered != pattern:
        message = (f"The item scores follow the pattern of '{pattern}', but '{entered}' was entered. This is only a prompt to "
                   "re-check; the category is your clinical judgement.")
    return {"pattern": pattern, "markers": {"emergence": emerged, "mcs_plus": plus, "mcs_minus": minus}, "scoring_note": note,
            "entered_category": entered, "differs_from_entered": bool(message), "message": message,
            "disclaimer": "Pattern check from the item scores only. Not a diagnosis; it ignores confounders and repeated-assessment evidence."}
