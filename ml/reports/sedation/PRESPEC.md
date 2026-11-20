# Pre-specification: propofol-sedation level-of-responsiveness model

Written 2026-11-20, **after** the features were built and sanity-checked for direction (group means only, no model run) and **before** any model was trained.

## Question
Can a model, trained only on healthy volunteers, tell from resting EEG whether a **new person's** recording was taken awake or under moderate propofol sedation — and say how sure it is?
This is a **level-of-responsiveness** question on healthy volunteers under a drug. It is not a diagnosis, not consciousness detection in patients, and not a recovery measure.

## Data
Chennu et al. 2016 (Cambridge repository, CC BY 2.0 UK): 20 people × 4 recordings (1 baseline, 2 mild, 3 moderate, 4 recovery), 91 channels, 250 Hz, 10 s epochs, already filtered 0.5–45 Hz and cleaned by the authors.
Only the 19 standard 10–20 channels are used (re-referenced to their own average), so the same model can read any 19-channel clinical recording.

## Labels
- Primary: **sedated = level 3 (moderate)**, **awake = levels 1 (baseline) and 4 (recovery)**. Level 2 (mild) is *not* used for training or for the primary metric.
- Level 2 is used afterwards as a dose-response check (does P(sedated) rise with propofol?).

## Unit of evaluation
The **recording** (the mean of its epoch probabilities); epoch-level metrics are secondary. Epochs of one recording are not independent.

## Validation
- **Leave-one-subject-out (20 folds).** No person is ever in training and test. Scalers, hyper-parameters (inner 3-fold *grouped by subject* grid search), calibrators and OOD statistics are fitted on the training people only.
- No locked hold-out: with 20 people a hold-out of 4 would leave 12 recordings (uselessly wide intervals). The price is a small selection optimism, bounded by tiny fixed grids and a pre-specified rule; it is stated in the report. The real external test is data from another site/device (see the report).
- 95 % intervals: bootstrap over **subjects**.
- Significance: label-permutation test (labels permuted among each person's own 3 recordings), 100 permutations, fixed hyper-parameters.

## Models (fixed list and grids, `nbml.sed_models`)
Logistic regression, SVM (RBF), Random Forest, Gradient Boosting, XGBoost, LightGBM.

## Selection rule (same as the earlier study)
Best = highest pooled recording-level balanced accuracy. A model is "tied" if the 95 % paired subject-bootstrap interval of (best − model) includes 0.
Among tied: lowest Brier score, then most interpretable, then cheapest.

## Reliability add-ons (all computed leave-one-subject-out)
- **Calibration:** Platt scaling of the epoch scores.
- **Uncertainty:** split-conformal prediction sets at 80 % coverage ("awake", "sedated", "uncertain"); coverage is checked on the held-out person.
- **Unfamiliar-EEG flag:** Mahalanobis distance of a recording's epochs from the training epochs (PCA space, 95 % variance); flag threshold = 99th percentile of training distances.

## Explanations
SHAP values of each leave-one-out model on its own **held-out** person, averaged; global ranking reported with plain-language names.

## What would make me distrust the result (written in advance)
- Balanced accuracy not above the permutation null (p ≥ 0.05).
- Calibration ECE > 0.15, or conformal coverage far below 80 %.
- A top feature that is amplitude-only (log power): it would suggest a device/gain effect, not physiology.
