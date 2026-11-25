# Pre-specified plan: vegetative state vs minimally conscious state from long EEG (patient data)

Written before any feature was extracted from these files. Code: `nbml/psg_doc.py`.

## Data
Mendeley Data 6wx4n25h4v, "Polysomnographic records of patients with disorders of consciousness" (CC BY 4.0; Nekrasova, Kanarskii, Yankevich, Borisov, Pradhan).
42 recordings of 42 patients, 4-8 hours, 256 Hz, SOMNOscreen. Diagnosis is in the file name: VS 30, MCS- 4, MCS+ 8.
Common EEG channels in every file: F3, F4, C3, C4, O1, O2. Channel references differ between files (some also carry :A1/:A2 derivations);
only the plain channels are used, then re-referenced to the average of the six. No age, sex, cause, time since injury, CRS-R or medication is available.

## Question
Do simple, interpretable EEG features separate patients diagnosed as vegetative state (VS, label 0) from patients diagnosed as minimally conscious
(MCS- and MCS+ merged, label 1), in patients the model has not seen?

## Preparation (no labels used)
Notch 50 Hz, Butterworth 0.5-45 Hz zero-phase, average reference over the six channels, 30 s epochs.
Drop an epoch if peak-to-peak exceeds 400 microvolts or the signal is flat (SD below 0.5 microvolt) in 2 or more channels.
A patient needs at least 60 usable epochs (30 minutes).

## Features (fixed list of 14, computed per epoch, then the median over a patient's epochs)
Relative power in delta (1-4), theta (4-8), alpha (8-13), beta (13-30) Hz; spectral entropy; permutation entropy; aperiodic slope (2-40 Hz without 7-14 Hz);
Hjorth mobility and complexity; log ratio alpha/delta; frontal minus occipital relative alpha; wPLI (mean over the 15 channel pairs) in delta, theta, alpha.
Gamma and overall amplitude are excluded (muscle activity and device gain).

## Primary analysis
Standardised L2 logistic regression (C = 0.1, balanced class weights, no tuning) on the 14 patient-level features, leave-one-patient-out.
Primary metric: ROC-AUC of the 42 held-out predictions. Secondary: balanced accuracy at 0.5, sensitivity and specificity.
Uncertainty: bootstrap over patients (2,000 resamples) for 95 % intervals. Chance level: 1,000 label permutations of the whole procedure.
Success criterion: AUC above 0.65 with lower 95 % bound above 0.5 and permutation p below 0.05.
Warning signs (result is not trusted): AUC below 0.5 on the permuted-label check, a single feature carrying all the signal, or the result vanishing in the sensitivity analysis.

## Secondary and sensitivity
1. Descriptive univariate AUC of each feature (no multiplicity correction; exploratory).
2. Sensitivity: the same analysis using only each patient's quartile of epochs with the lowest delta share (the most awake-like stretches), because
   these are 4-8 hour recordings that include sleep, and sleep affects EEG at least as much as diagnosis.

## Interpretation limits (written now)
42 patients and 12 in the smaller group give wide intervals. Diagnoses are the centre's own, with no CRS-R or consensus review available to us.
Sleep, medication, age and cause of injury are uncontrolled. One centre, one device. Only six channels, so none of our 19-channel sedation features can be used.
A positive result would be a pilot signal, not a clinical validation, and nothing here changes the "staging" status of any model.
