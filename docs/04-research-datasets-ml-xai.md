# 04 — Scientific Background, Datasets, ML Selection, Validation & Explainable AI

Covers Section 5 (research & scientific validity) and deliverables **6 (Datasets)**, **7 (ML comparison & selection)**, **8 (Training & validation)**, **9 (XAI methodology)**.

> **Citation note.** The references below are well-known published works drawn from my knowledge of the literature, not from a live database search in this session. Before using them in a thesis or paper, verify every citation (authors, year, DOI) in PubMed/Google Scholar, and run a proper systematic search to confirm the research gaps listed in §1.3.

---

## 1. Scientific background

### 1.1 Established knowledge (what the field already accepts)

| Topic | Established findings | Key references |
|---|---|---|
| **Quantitative EEG (qEEG)** | Spectral power, ratios and complexity measures are standard, reproducible descriptors of EEG when acquisition and processing are standardised. | Gramfort et al., 2013 (MNE); Donoghue et al., 2020 (aperiodic/periodic separation) |
| **Disorders of consciousness (DoC)** | Clinical categories: coma, unresponsive wakefulness syndrome (UWS/VS), minimally conscious state (MCS−/MCS+), emergence (eMCS). Behavioural assessment with the **CRS-R** is the recommended reference standard; repeated assessments reduce misdiagnosis. | Giacino et al., 2004 (CRS-R); Giacino et al., 2018 (AAN practice guideline); Kondziella et al., 2020 (EAN guideline); Wannez et al., 2017 (≥5 repeated CRS-R) |
| **EEG markers of consciousness level** | At group level, patients in MCS show more alpha/theta power, higher complexity and information sharing (e.g., wSMI) than UWS patients; multivariate EEG markers generalise across sites/protocols at group level. | King et al., 2013; Sitt et al., 2014; Engemann et al., 2018; Chennu et al., 2017 |
| **Aperiodic (1/f) exponent** | Steeper spectral slope associated with unconsciousness under anaesthesia; being studied in DoC. | Colombo et al., 2019; Donoghue et al., 2020 |
| **Cognitive motor dissociation (CMD)** | Some behaviourally unresponsive patients show command-following brain activity on fMRI or EEG. A meta-analysis estimated ~15 % of UWS patients; a 2024 multicentre study found CMD in ~25 % of patients without behavioural command following. CMD was associated with better functional outcome in acute brain injury. | Owen et al., 2006; Monti et al., 2010; Schiff, 2015 (term CMD); Kondziella et al., 2016; Claassen et al., 2019; Bodien et al., 2024 |
| **EEG motor imagery (MI) in DoC** | EEG MI paradigms detected command following in a subset of UWS patients, but analyses were contested: re-analysis showed sensitivity to statistical assumptions, highlighting false-positive risk. | Cruse et al., 2011; Goldfine et al., 2013 |
| **MI decoding in healthy people** | CSP+LDA and Riemannian tangent-space classifiers are strong baselines; EEGNet/ShallowConvNet competitive, especially with more data; cross-subject decoding is much harder than within-subject. | Lotte et al., 2018; Barachant et al., 2012; Lawhern et al., 2018; Schirrmeister et al., 2017; Jayaram & Barachant, 2018 (MOABB) |
| **Test–retest reliability of qEEG** | Resting spectral power (especially relative alpha/theta) shows moderate-to-high reliability in healthy adults across sessions; reliability depends on epoch count and feature. | Salinsky et al., 1991; Gudmundsson et al., 2007 |
| **Small-sample ML pitfalls** | Small test sets give wide error bars; data leakage and non-subject-independent splits inflate EEG accuracies. | Varoquaux, 2018; Roy et al., 2019 |
| **XAI** | SHAP provides additive feature attributions; explanation ≠ causation, and correlated features split attributions. | Lundberg & Lee, 2017 |

### 1.2 Critical distinction the platform must enforce

| | Motor-imagery classification | Command following / CMD detection | Consciousness assessment |
|---|---|---|---|
| Question | "Is this EEG epoch left- or right-hand imagery?" | "Does this patient's EEG change consistently when instructed (e.g., 'move your hand' vs 'stop')?" | "What is the patient's level of consciousness?" |
| Data | Healthy participants performing cued MI | Patient-specific task recording, within-patient statistics | Multimodal clinical judgement (behaviour, imaging, EEG, history) |
| What NeuroBridge does | Research benchmark model trained on public healthy datasets | Research prototype: within-patient permutation test (Claassen-style) | **Nothing automated.** Records clinician's CRS-R and category |
| Valid conclusion | Classifier accuracy on healthy held-out subjects | Evidence (or no evidence) of task-related EEG modulation in this session | Clinician's responsibility |

A negative command-following test **never** implies absence of awareness (fatigue, aphasia, deafness, arousal fluctuation, sedation, or poor signal can cause false negatives).

### 1.3 Candidate research gaps (to be confirmed by systematic review)
These are plausible, defensible gaps based on the literature above — stated as hypotheses to verify, not as proven facts:

1. **Within-patient longitudinal qEEG change quantification.** Most DoC EEG studies are cross-sectional group comparisons or prognostic studies. Methods that quantify *individual* session-to-session change relative to measurement error (SDC/reliable-change) in bedside ICU/rehabilitation EEG appear under-reported.
2. **Condition- and acquisition-aware comparison.** Few tools make recording-compatibility (montage, sedation, state, quality) an explicit, auditable part of longitudinal comparison.
3. **Reliability of features in patients.** Test–retest reliability of spectral, aperiodic and complexity features is mainly characterised in healthy adults; reliability in brain-injured patients is less known.
4. **Transfer of MI decoders from healthy to patient data.** Models trained on healthy MI data face a large distribution shift; OOD detection for this setting is rarely reported.
5. **Clinically oriented XAI for EEG.** Explanations mapped to clinically understandable concepts (channel region × band) plus uncertainty and OOD flags, evaluated with clinicians, are limited.

### 1.4 Established methods vs proposed contributions

| Established (we implement) | Proposed contribution (research novelty) |
|---|---|
| MNE preprocessing, ICA, autoreject, Welch PSD, band power, specparam, entropy | **Compatibility-aware longitudinal comparison engine** with explicit, auditable rules |
| CSP/Riemannian/EEGNet MI decoders; MOABB benchmarking | **Patient-specific SDC framework** using repeated baselines + reference test–retest data |
| Permutation testing, FDR, cluster permutation | **Feature-reliability study**: ICC of all platform features on public multi-session data, published as a reference table used by the platform |
| SHAP, permutation importance, calibration, conformal prediction | **OOD-gated research inference** with plain-language channel×band explanations, evaluated in a clinician usability study |
| CRS-R | **Integrated longitudinal EEG + CRS-R timeline** with time-aligned exploratory association analysis |

---

## 2. Dataset recommendations (deliverable 6)

> No synthetic data will be presented as research evidence. Synthetic signals are used **only** for unit tests (e.g., a pure 10 Hz sine must give peak alpha at 10 Hz), clearly labelled.

### 2.1 Recommended datasets

| Dataset | Content | Format | Use in NeuroBridge | Limitations |
|---|---|---|---|---|
| **PhysioNet EEG Motor Movement/Imagery (EEGMMIDB)** (Schalk et al., 2004; Goldberger et al., 2000) | 109 healthy subjects, 64 ch, 160 Hz, real & imagined hand/feet movements, baseline eyes-open/closed runs | **EDF+** | **Primary MI dataset** (large participant count → good for subject-independent CV); also tests EDF ingest | Healthy volunteers only; short trials; 160 Hz limits gamma; some noisy subjects; MI ≠ patient command following |
| **BCI Competition IV 2a** (Tangermann et al., 2012) | 9 subjects, 22 EEG + 3 EOG, 250 Hz, 4-class MI, 2 sessions/subject | GDF (convert) | Secondary benchmark; cross-session evaluation | Only 9 subjects → very wide CIs; not EDF |
| **Lee2019 / OpenBMI** (Lee et al., 2019) | 54 subjects, 62 ch, MI + ERP + SSVEP, 2 sessions | .mat (via MOABB) | External validation of MI model; cross-session | Healthy; different montage |
| **Cho2017** (Cho et al., 2017) | 52 subjects, 64 ch, left/right MI | .mat (via MOABB) | External validation | Healthy |
| **Stieger 2021 multi-session BCI** (Stieger et al., 2021) | 62 subjects, 7–11 MI-BCI sessions each | .mat | **Feature stability across repeated sessions** (key for longitudinal research) | Healthy; BCI-training context |
| **Test–retest resting/cognitive EEG** (Wang et al., 2022, *Sci Data*; OpenNeuro — *verify accession*) | ~60 participants, 3 sessions, resting eyes-open/closed | BIDS (BrainVision) | **Reference SDC/ICC for resting features** | Healthy young adults |
| **MPI-LEMON** (Babayan et al., 2019) | ~200 healthy participants, resting EEG (62 ch) | BIDS (BrainVision) | Feature distribution reference, OOD training set for resting features | Single session; healthy |
| **TUH EEG Corpus** + subsets **TUAR** (artifacts), **TUAB** (normal/abnormal) (Obeid & Picone, 2016) | Tens of thousands of clinical hospital EEGs | **EDF** | Real-world EDF robustness testing; **artifact-detector / quality-score calibration** (TUAR) | Requires data-use agreement; heterogeneous montages; labels from reports; not DoC-specific |
| **CHB-MIT Scalp EEG** (Shoeb, 2009) | 23 paediatric patients, long recordings | **EDF** | Ingest/performance testing of long EDFs | Paediatric epilepsy; not for DoC models |
| **Sleep-EDF** (Kemp et al., 2000) | PSG with 2 EEG channels | **EDF** | Edge-case testing (few channels, mixed rates) | Sleep only |
| **MNE sample / testing data** | Small reference files | FIF/EDF | Unit/golden tests | Not research evidence |

### 2.2 DoC-specific data
I am not aware of a large, openly downloadable DoC EEG dataset with item-level CRS-R labels and repeated sessions that I can recommend with confidence. Some groups share data on request or via OpenNeuro, but availability changes — search OpenNeuro, PhysioNet and published data-availability statements, and verify licensing.
**Practical path:** (1) build and validate the platform on public healthy/clinical data; (2) obtain ethics approval and a data-sharing agreement with a clinical DoC centre for any patient-level analysis; (3) design a prospective protocol (standardised resting + command-following recordings paired with CRS-R).

---

## 3. ML algorithm comparison and selection (deliverable 7)

### 3.1 Tasks the platform can legitimately model

| Task | Data | Status |
|---|---|---|
| **T1. MI classification (left vs right hand imagery)** | EEGMMIDB (primary), Lee2019/Cho2017 (external) | Research benchmark |
| **T2. Signal-quality / artifact classification (epoch-level)** | TUAR | Supports quality score; practical clinical value |
| **T3. Within-patient command following** | Patient task recordings | Statistical test, not a pre-trained model |
| ~~Consciousness-level classifier~~ | Needs labelled DoC data | **Not built** until real labelled data + ethics exist |

### 3.2 Candidate algorithms evaluated

| Algorithm | Input | Strengths | Weaknesses | Expected fit |
|---|---|---|---|---|
| **Logistic Regression (L2 / elastic-net)** | Band-power, CSP or tangent-space features | Fast, well-calibrated, coefficients interpretable, robust with small n | Linear only | Strong default with good features |
| **Shrinkage LDA** | CSP log-variance | Classic MI baseline, very data-efficient | Linear; assumes Gaussian classes | Baseline |
| **SVM (linear / RBF)** | Same | Good with high-dimensional small samples | Probabilities need calibration; RBF less interpretable; tuning | Strong comparator |
| **Random Forest** | Feature table | Handles non-linearity, little scaling needed | Often weaker on EEG spatial-covariance structure; poorer calibration | Comparator |
| **Gradient Boosting (sklearn HistGB)** | Feature table | Strong tabular performance | Overfits small data without care | Comparator |
| **XGBoost** | Feature table | Regularised boosting, native SHAP TreeExplainer | Many hyperparameters; overkill for small n | Comparator |
| **LightGBM** | Feature table | Fast, memory-efficient | Leaf-wise growth overfits small datasets | Comparator |
| **Riemannian MDM / Tangent Space + LR** (pyRiemann) | Spatial covariance matrices | State-of-the-art classical cross-subject MI; few hyperparameters | Requires consistent channel set | **Likely top classical model** |
| **EEGNet** (Lawhern et al., 2018) | Raw epoched EEG | Compact CNN (few thousand parameters), learns spatial+spectral filters, designed for small EEG data | Needs GPU for speed, more variance across seeds, explanations less direct | Deep benchmark |
| **ShallowConvNet** (Schirrmeister et al., 2017) | Raw epochs | Mimics FBCSP; strong for MI | Same as EEGNet | Deep benchmark |
| Larger deep models (DeepConvNet, transformers) | Raw epochs | Can exploit large data | Data-hungry; 109 subjects is small for these; hard to explain | **Excluded** (unjustified complexity) |

### 3.3 Selection protocol (decision is made by evidence, not in advance)
1. Run every candidate in the **identical** nested, subject-independent CV (§4) with identical preprocessing.
2. Primary metric: **balanced accuracy** (mean ± 95 % CI across outer folds); secondary: ROC-AUC, F1, **Brier score / expected calibration error**.
3. Statistical comparison: paired comparison across outer folds (corrected resampled t-test, Nadeau & Bengio, or Wilcoxon) — differences within noise are treated as ties.
4. **Tie-breaking rule (pre-registered):** among models statistically tied with the best, choose the one that is (a) best calibrated, (b) most interpretable, (c) cheapest to run on CPU.

**Prior expectation (hypothesis, not a result):** for cross-subject MI on EEGMMIDB, Riemannian tangent-space + logistic regression and EEGNet will likely perform comparably and better than tree ensembles on band-power features; under the tie-breaking rule the expected deployed model is **Tangent-Space + Logistic Regression**, with EEGNet reported as the deep-learning benchmark. If the experiments show otherwise, the selection follows the data.

For **T2 (artifact classification)** on tabular quality features, LightGBM/XGBoost vs logistic regression will be compared; tree ensembles are plausible winners because artifact signatures are non-linear threshold-like patterns.

---

## 4. Training and validation strategy (deliverable 8)

### 4.1 Leakage-prevention rules
| Risk | Rule |
|---|---|
| Same participant in train & test | **GroupKFold / LeaveOneGroupOut by subject**; automated test asserts empty intersection |
| Epoch overlap across splits | Splits happen at subject level before epoching |
| Preprocessing fitted on all data (scalers, CSP, ICA templates, feature selection, covariance reference mean) | Everything fitted **inside** an sklearn `Pipeline` on training folds only |
| Hyperparameter tuning on test data | **Nested CV:** inner GroupKFold for tuning, outer for evaluation |
| Choosing models after peeking at test results | Final hold-out set (e.g., 20 % of subjects, stratified, fixed seed) locked until the very end, used **once** |
| Duplicate/near-duplicate recordings | Hash check across datasets |
| Session leakage (multi-session datasets) | Cross-session evaluation: train session 1, test session 2 (within-subject) reported separately from cross-subject |

### 4.2 Protocol
```
EEGMMIDB (109 subjects)
├── Locked hold-out: 22 subjects (never touched until final)
└── Development: 87 subjects
     └── Outer: 5-fold GroupKFold (subjects) ── for each outer fold:
            └── Inner: 4-fold GroupKFold → hyperparameter search (small, predefined grids)
            └── Fit best on outer-train → evaluate on outer-test
Final: refit chosen model on all 87 → evaluate ONCE on 22 hold-out
External: evaluate frozen model on Lee2019 / Cho2017 (channel subset mapped) → reports generalisation drop
```
- Repeat outer CV with 3–5 different seeds; report mean and spread.
- Exclude known bad subjects only by a **pre-specified** rule (e.g., documented annotation problems), never by performance.

### 4.3 Metrics reported
- Balanced accuracy, precision, recall, F1 (macro), ROC-AUC (binary), confusion matrix (summed over outer folds), **per-subject accuracy distribution** (box plot — reveals "BCI-inefficient" subjects), calibration curve, Brier score, ECE.
- Chance level with confidence bounds for the test-set size (e.g., binomial or permutation-based chance; Combrisson & Jerbi, 2015) — accuracies are only meaningful above this.
- **Permutation test of the whole pipeline** (labels shuffled within subject, 100–1000 runs) to confirm performance is not achievable by chance/leakage.
- Compute cost: training time, inference latency on CPU, model size.

### 4.4 Reporting honesty
- All numbers in the app/model card come from the actual evaluation report generated by `ml/` scripts, with commit hash and dataset version. No placeholder or illustrative accuracy figures are shown anywhere in the UI.
- Model card sections: intended use, out-of-scope use (explicitly: *not* for consciousness/CMD diagnosis), training data, evaluation, subgroup results, limitations, ethical considerations (Mitchell et al., 2019 model cards).

### 4.5 Within-patient command-following analysis (T3)
Following the published approach (Claassen et al., 2019): task recordings with alternating instruction blocks ("keep opening and closing your right hand" vs "stop"); PSD features per epoch; classifier (linear SVM / LR) trained and tested **within the patient** with block-wise cross-validation (blocks never split); significance by **permutation of block labels** (≥ 500 permutations); report AUC and permutation p-value. Mandatory controls: EMG/movement artifact check (real movement or muscle artifact can cause false positives), block-structure-aware permutation, and caution that results are research outputs needing independent replication.

---

## 5. Explainable AI methodology (deliverable 9)

| Layer | Method | Shown to user as |
|---|---|---|
| **Global importance** | Permutation importance on held-out folds (model-agnostic, robust); SHAP summary (TreeExplainer for trees, LinearExplainer for LR) | "Features the model relies on most, averaged across test subjects" with region×band labels |
| **Spatial interpretation** | For Riemannian/LR: spatial patterns (Haufe et al., 2014 transformation — weights ≠ patterns); for EEGNet: learned spatial filter patterns as topomaps | Topomap of model patterns |
| **Local explanation** | SHAP values for one prediction (waterfall); top 5 contributions translated by template ("Higher mu-band (8–13 Hz) power decrease over C3 pushed the prediction towards *right hand*") | Plain-language list + chart |
| **Uncertainty** | Calibrated probability (Platt or isotonic fitted in inner CV); **conformal prediction sets** (MAPIE, split-conformal on calibration subjects) at 90 % coverage; seed-ensemble spread for EEGNet | "Probability 0.71 (calibrated). Prediction set: {right}" or "Uncertain: {left, right}" |
| **Out-of-distribution** | (1) Hard rules: required channels present, sfreq ≥ required, condition matches, quality ≥ fair; (2) Mahalanobis distance of feature vector to training distribution (with shrinkage covariance); (3) Riemannian distance of covariance to training mean; threshold = 99th percentile of training distances | "Outside model scope — reason: 7 of 22 required channels missing" → prediction withheld |
| **Version tracking** | Model name+version, feature pipeline version, input hash, explainer version stored with every prediction | Footer on every AI card; reproducible on demand |

**Explanation caveats shown in UI:** SHAP shows association within the model, not physiological causation; correlated features (neighbouring channels) share credit; explanations are only meaningful for in-distribution inputs (OOD inputs get no explanation).
