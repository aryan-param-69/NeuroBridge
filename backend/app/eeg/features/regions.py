"""Electrode -> brain region (lobe x side) from the standard 10-10 names, and homologous left/right pairs."""

import re

LOBE_OF_PREFIX = {
    "Fp": "frontal", "AF": "frontal", "F": "frontal",
    "FC": "central", "C": "central", "CP": "central",
    "FT": "temporal", "T": "temporal", "TP": "temporal",
    "P": "parietal", "PO": "parietal",
    "O": "occipital", "I": "occipital",
}
_NAME = re.compile(r"^([A-Za-z]*?[A-Za-z])(z|\d+)$")


def region_of(name: str) -> str | None:
    """'F3' -> 'frontal_L', 'Cz' -> 'central_M', 'T8' -> 'temporal_R'; None for non-standard names."""
    m = _NAME.match(name)
    if not m or m.group(1) not in LOBE_OF_PREFIX:
        return None
    suffix = m.group(2)
    side = "M" if suffix == "z" else ("L" if int(suffix) % 2 else "R")
    return f"{LOBE_OF_PREFIX[m.group(1)]}_{side}"


def regions_for(names: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, n in enumerate(names):
        r = region_of(n)
        if r:
            out.setdefault(r, []).append(i)
    return out


def homologous_pairs(names: list[str]) -> list[tuple[int, int, str]]:
    """(left index, right index, 'F3-F4') for odd/even neighbours with the same prefix (F3-F4, T7-T8, FC1-FC2...)."""
    idx = {n: i for i, n in enumerate(names)}
    pairs = []
    for n, i in idx.items():
        m = _NAME.match(n)
        if m and m.group(2) != "z" and int(m.group(2)) % 2 == 1:
            partner = f"{m.group(1)}{int(m.group(2)) + 1}"
            if partner in idx:
                pairs.append((i, idx[partner], f"{n}-{partner}"))
    return pairs
