# Is more data needed? — dataset advice for NeuroTrace

Short answer: **yes, but different data for different goals.** EEGMMIDB is a good *benchmark* for motor-imagery
algorithms and for testing the EDF pipeline. It cannot, by design, support the project's clinical aims
(longitudinal change, consciousness, command following). Nothing below is a clinical claim; numbers
from the experiment are in `left_vs_right/REPORT.md`.

## What EEGMMIDB can and cannot answer

| Project goal | Can EEGMMIDB support it? | Why |
|---|---|---|
| Compare ML models for left/right motor imagery across people | **Yes** | 105 usable subjects, standard 64-channel EDF+, public |
| Test EDF ingest, channel normalisation, viewer | **Yes** | real EDF+ files, 109 subjects |
| Estimate how stable EEG features are *across sessions* (needed for the longitudinal "smallest detectable change") | **No** | one session per person; ~22 trials per class per person |
| Within-subject (calibrated) decoders | **Weak** | only ~45 imagery trials per person |
| Detect command following / CMD in patients | **No** | healthy volunteers; no patients; no clinical labels |
| Anything about consciousness, CRS-R, sedation | **No** | none of these are recorded |
| Train a quality / artefact classifier | **No** | no artefact labels |

## What to add, in priority order

1. **Multi-session motor-imagery data (for external validation and repeatability).** Lee 2019 (OpenBMI), Cho 2017 and
   Stieger 2021 are available through MOABB; they use different amplifiers, montages and several sessions per person.
   Use them to (a) test the *frozen* model on a new dataset and see how much accuracy drops, and (b) measure
   session-to-session variability of the same features the platform uses for longitudinal comparison.
   *Limit: still healthy volunteers.*
2. **Test–retest resting-state EEG from healthy adults with repeated sessions** (search OpenNeuro / PhysioNet for resting EEG with ≥ 2 sessions; verify
   licence and accession numbers before use). This is what the longitudinal module needs to compute
   reliability (ICC) and a *smallest detectable change* for band power, aperiodic slope and entropy. Without it, the platform
   can only say "the change is larger than this patient's own variability" when they have repeated baselines.
3. **Artefact-annotated clinical EEG** (Temple University Hospital EEG corpus, TUAR subset; requires a data-use agreement). It would let
   the signal-quality score be calibrated against expert labels instead of rules chosen by hand.
4. **Real patient data with CRS-R — the only way to answer the clinical question.** No large public dataset fits (to my knowledge; verify
   current availability). It needs ethics approval and a partnership with a disorders-of-consciousness centre. A usable
   prospective protocol: standardised resting recordings (same montage, condition, duration) **plus** a command-following task,
   **plus** a CRS-R and a record of sedation at every session, repeated over time. The number of patients and sessions should come
   from a power analysis with a statistician; do not train a consciousness classifier on a handful of people.

## Different *types* of data that matter more than "more of the same"
- **Repeated sessions per person** (not more people in one session) — for the longitudinal goal.
- **More trials per person (≥ 100 per class)** — if within-person calibration is wanted; per-person calibration is usually
  where motor-imagery decoding gains most.
- **EMG / EOG / movement channels recorded alongside the EEG** — required to rule out muscle artefact as the "signal" in command-following tests.
- **Metadata** — sedation, state, time of day, electrode system. The platform now records these per recording (Phase 2); datasets without them are much less useful for comparison.

## Modelling advice (not more data)
- Tangent-space + logistic regression was the best of the nine models in the measured comparison; tree ensembles were the weakest on band-power features.
  Do not add larger deep models until more data exists: with ~100 people they are unlikely to help, and they are harder to explain.
- The probabilities are poorly calibrated (for the chosen model they are *under*-confident and the hold-out showed a class bias); add recalibration, uncertainty and out-of-distribution checks before showing them to anyone.
- A per-subject recentring step was tried only as an **exploratory** follow-up (see `left_vs_right_ra/` if it exists); it must be re-validated on a *new* dataset, because the hold-out set has already been used once.
