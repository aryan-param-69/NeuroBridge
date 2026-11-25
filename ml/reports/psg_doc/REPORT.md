# Pilot on real patient EEG: vegetative state vs minimally conscious state

Plan: `PRESPEC.md` (written before any feature was computed). Code: `nbml/psg_doc.py`. Numbers: `results.json`.

## Data
Mendeley Data 6wx4n25h4v (CC BY 4.0; Nekrasova, Kanarskii, Yankevich, Borisov, Pradhan): 42 patients, median 6 hours of EEG each (256 Hz, six channels F3 F4 C3 C4 O1 O2),
diagnosis from the file name: 30 vegetative state (VS), 8 MCS+, 4 MCS-. All 42 passed the fixed inclusion rule (median 718 usable 30 s epochs per patient).
All files were checked against the published SHA-256 fingerprints.

## Result
| Analysis | ROC-AUC (95 % CI) | Balanced accuracy (95 % CI) | Permutation p |
|---|---|---|---|
| Primary: 14 features, whole recording, leave-one-patient-out | 0.556 (0.36-0.75) | 0.575 (0.41-0.74) | 0.32 |
| Sensitivity: most awake-like quarter of each recording | 0.600 (0.40-0.78) | 0.592 (0.41-0.76) | 0.20 |
| Primary without its strongest single feature (aperiodic slope) | 0.536 (0.34-0.72) | 0.592 (0.42-0.76) | 0.36 |

The pre-specified success criterion (AUC above 0.65, lower bound above 0.5, p below 0.05) was **not met**. The interval includes 0.5, i.e. chance.
**With these six channels and these features, we could not separate VS from MCS patients.** This is a negative result and is reported as such.

## Observations (exploratory, no multiplicity correction)
Single features were also weak (AUC 0.29-0.66). The direction was the opposite of what a simple "more awake means faster rhythms" story predicts:
MCS patients had slightly more delta and theta (AUC 0.66 each) and less aperiodic slope steepness (AUC 0.29, i.e. steeper in MCS). These are not
significant, and several plausible explanations were not controllable here: sleep during 4-8 hour recordings, sedating or anticonvulsant medication,
cause and time since injury, age, and different recording references between files.

## What this means for the project
- The sedation models are unchanged and are **not** claimed to work on patients. This pilot did not use them (the recordings do not have the 19 channels).
- Simple spectral features from six channels in long, uncontrolled recordings are not enough to tell VS from MCS in 42 patients. This agrees with the
  literature, where bedside-assessment gaps and heterogeneity are the reason for the project's longitudinal, per-patient design.
- Value for the viva: the platform was tested against real patient diagnoses with a plan written in advance, and the honest answer was "no signal found".
- Nothing here was tuned after seeing the result. A fair next step would be a pre-planned analysis with matched wake periods and metadata, or a larger
  labelled dataset (for example the Bath or other centre data after an access request).

## Limits
42 patients, 12 in the smaller group; one centre and one device; diagnoses as recorded by the centre, no CRS-R or independent review; six channels; no age, cause or medication.
