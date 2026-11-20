# Pre-specification: multi-site propofol-sedation model (v2)

Written 2026-11-20, **before** any model was trained on the pooled data. The Oslo features had been built and checked only for shape and for the number of epochs that survive artefact rejection (no model, no group comparison, no look at feature values by label).

## Question
Does a model that reads "awake vs propofol-sedated" from resting EEG keep working (a) on new people from a mixture of two laboratories and (b) on a laboratory it has never seen? This addresses the project's generalisation gap (models trained on one site fail on another).

## Data (all open, healthy adults)
| Site | Source | People | Recordings used | Epochs (10 s) | Hardware |
|---|---|---|---|---|---|
| Cambridge | Chennu et al. 2016, CC BY 2.0 UK | 20 | 60 (baseline, moderate, recovery) + 20 mild (never trained) | 2,302 + 780 | 91-ch, 250 Hz, already cleaned by the authors |
| Oslo | OpenNeuro ds005620, Bajwa et al., CC BY 4.0 | 20 | 71 (awake eyes-closed, first 120 s; 1-minute rest under propofol, up to 3 per person) | 512 | Brain Products, 65 ch, 5 kHz, raw |

Oslo recordings are prepared by `nbml.prep` (notch 50 Hz, Butterworth 0.5-45 Hz zero-phase, resample 250 Hz, **EOG regression** with VEOG/HEOG, average reference over the 19 standard channels, epochs rejected if peak-to-peak > 200 uV in more than 10 % of channels). These numbers were fixed before any model result. Cambridge data are used exactly as in v1.

## Labels
awake = 0 (Cambridge baseline and recovery; Oslo awake eyes-closed); sedated = 1 (Cambridge moderate sedation; Oslo rest under propofol). Cambridge mild sedation is never used for training or for the primary metrics. **Caveat:** Oslo sedation was deliberately deep enough to need awakenings, so it is probably deeper than Cambridge "moderate"; the two sedated classes are related, not identical.

## Features
Primary set (68): the 74 v1 features **without the six log-power features**, because overall amplitude depends on amplifier gain, reference and filtering and is the most obvious carrier of a site effect (v1 report: "amplitude-only features" was a pre-written warning sign). Sensitivity analysis: all 74.

## Unit and validation
Unit = recording (mean of its epoch probabilities). Models: logistic regression, SVM (RBF), random forest, gradient boosting, XGBoost, LightGBM; same small fixed grids as v1; hyper-parameters tuned only on training people (inner 3-fold grouped by person).
1. **Pooled leave-one-person-out** over all 40 people (40 folds). Selection rule unchanged: best pooled recording-level balanced accuracy; "tied" if the 95 % paired person-bootstrap interval of the difference includes 0; ties broken by Brier, then interpretability, then cost.
2. **Cross-site** (the key external-validity result): train on Cambridge only -> test Oslo; train on Oslo only -> test Cambridge. All six models, plus the chosen model. 95 % intervals by bootstrap over test people.
3. Permutation test for the chosen model (100 permutations, labels shuffled among each person's own recordings).
4. Per-site balanced accuracy inside the pooled analysis; calibration (Platt, leave-one-person-out), conformal sets (80 % target), SHAP on held-out people, unfamiliar-EEG detector (leave-one-person-out), Cambridge mild-sedation dose-response.

## Success and warning criteria (written in advance)
- **Success:** pooled balanced accuracy > 0.80 AND the lower 95 % bound of balanced accuracy > 0.5 in BOTH cross-site directions.
- **Warning:** either cross-site balanced accuracy < 0.65; the model calls almost every recording of the test site the same class (a site detector, not a sedation detector); the top SHAP features are not spectral/phase features; permutation p >= 0.05.
- If the criteria fail, v1 stays as the deployed model and the failure is reported as the finding.

## Observational check on clinical EEG (no labels about sedation)
The frozen v2 model is applied to OpenNeuro ds004504 (88 people: 29 healthy, 36 Alzheimer's, 23 frontotemporal dementia; routine 19-channel clinical EEG, 500 Hz, eyes closed, prepared with `nbml.prep` without EOG regression because the set has no EOG channels). This is **not** an accuracy test: nobody in it is sedated. It asks how often a model trained on sedated healthy volunteers calls a patient "sedated-like" (EEG slowing in dementia is a known confounder), per group, and how often the unfamiliar-EEG detector fires.
