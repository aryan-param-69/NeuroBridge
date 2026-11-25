# 05 — UI/UX Page-by-Page Blueprint

Covers deliverable **10**. Goal: looks like premium hospital software (calm, precise, trustworthy) — not a student project or a neon "AI dashboard".

---

## 1. Design system

### 1.1 Colour tokens
| Token | Hex | Use |
|---|---|---|
| `--bg` | `#FFFFFF` | Page background |
| `--surface` | `#F7F9FB` | Sidebar, table headers, subtle panels |
| `--surface-raised` | `#FFFFFF` + 1px border | Cards |
| `--border` | `#E3E8EE` | Dividers, card borders |
| `--text` | `#0F1C2E` | Primary text (deep navy, not pure black) |
| `--text-muted` | `#5B6B7F` | Labels, secondary |
| `--primary` | `#1F5FAE` | Primary actions, links, selected nav (clinical blue) |
| `--primary-soft` | `#E8F0FA` | Selected rows, hover |
| `--accent` | `#0F8C8C` | Secondary data series, positive status (teal) |
| `--accent-soft` | `#E6F4F4` | Tags |
| `--warning` | `#B7791F` / soft `#FDF5E6` | Caveats, Fair quality |
| `--danger` | `#B42318` / soft `#FDECEA` | Not comparable, Poor quality, errors |
| `--research` | `#6B4FA0` / soft `#F2EEF8` | Research-mode banner (distinct from clinical) |
| EEG trace | `#1E293B` on white, grid `#EEF2F6` (1 s), `#F5F7FA` (0.2 s) | |

Charts: blue `#1F5FAE` = recording A / earlier; teal `#0F8C8C` = recording B / later; grey for references. Diverging change maps use a colour-blind-safe blue–white–orange (never red–green). All text ≥ 4.5:1 contrast (WCAG AA).

### 1.2 Typography
- **Inter** (UI) with tabular numerals for all metrics; **IBM Plex Mono** for IDs, hashes, parameters.
- Scale: 12 / 13 (table) / 14 (body) / 16 / 20 / 24 / 30 (page title). Weights 400/500/600 only.

### 1.3 Layout & components
- 8 px spacing grid; content max-width 1440 px; 24 px card padding; 12 px radius cards, 8 px inputs.
- Shadows: almost none (`0 1px 2px rgba(15,28,46,0.04)`); depth from borders, not glow.
- Motion: 150 ms fades only; no decorative animations.
- Desktop-first (1280–1920 px). Tablet (≥ 1024 px) supported with collapsible sidebar. Phones: read-only summary views.
- Core components: `PatientCard`, `PatientHeader`, `QualityBadge` (Good/Fair/Poor with tooltip breakdown), `CompatibilityChecklist`, `StatTile` (value + CI + unit), `ProvenanceDrawer`, `ResearchBanner`, `TimelineItem`, `EEGViewer`, `TopomapGrid`, `ChangeHeatmap`, `JobProgress`.

### 1.4 App shell
```
┌──────────────┬──────────────────────────────────────────────────────────┐
│ ◉ NeuroBridge│  Search patients (⌘K)                 🔔  Dr. A. Rao ▾   │
│              ├──────────────────────────────────────────────────────────┤
│ Dashboard    │                                                          │
│ Patients     │                    PAGE CONTENT                          │
│ Recordings   │                                                          │
│ Comparisons  │                                                          │
│ Assessments  │                                                          │
│ Reports      │                                                          │
│ ──────────   │                                                          │
│ Research ⓡ   │  (role-gated, purple marker)                             │
│ Admin        │                                                          │
│              │                                                          │
│ v1.0.0 · ⚙   │                                                          │
└──────────────┴──────────────────────────────────────────────────────────┘
```
Sidebar 240 px, `--surface` background, icons (Lucide, 1.5 px stroke) + labels; active item = blue text + 2 px left bar.

---

## 2. Pages

### P1. Login
Centered card on white, product name, institution logo slot, email/password (the original design had a second TOTP step; it was removed). Footer: "Authorised clinical use only. Activity is logged." No illustrations of glowing brains.

### P2. Dashboard (worklist, not vanity stats)
- **My patients** (cards, 3–4 per row): display ID, name (role-dependent), age/sex, etiology, days since injury, last recording date + QualityBadge, last CRS-R total, flags (e.g., "QC pending").
- **Work queue** table: recordings awaiting QC, failed processing jobs, comparisons awaiting sign-off, assessments due (per protocol).
- **Recent activity** (own actions).
- No large "accuracy" numbers or AI hero widgets.

### P3. Patient list
Table with search, filters (status, etiology, clinician, date range), columns: ID, Name, Age, Sex, Etiology, Injury date, Recordings (#), Last CRS-R, Last recording, Status. Toggle grid/card view. "Register patient" primary button top-right.

### P4. Register / edit patient
Two-column form in sections: Identity → Clinical background → Medications & history → Consent → Care team. Duplicate-check banner appears inline. Change-history drawer on edit.

### P5. Patient profile (hub)
```
┌──────────────────────────────────────────────────────────────────────────┐
│ NB-2026-000123 · Name · 54 y · M · Traumatic · Injury 02 Feb 2026 (D+105) │
│ Status: Active  · Clinician category (latest): MCS− (entered 12 May)       │
│ [Upload recording] [Add assessment] [Compare] [Generate report]           │
├──────────────────────────────────────────────────────────────────────────┤
│ Overview | Timeline | Recordings | Assessments | Trends | Notes | Reports │
└──────────────────────────────────────────────────────────────────────────┘
```
- **Overview tab:** key facts card, latest recording summary (quality, key features), latest CRS-R with subscale bars, latest comparison summary, active medications (sedatives highlighted), alerts (missing baseline, few repeated assessments).
- **Timeline tab:** horizontal zoomable timeline with lanes: Recordings · Assessments · Clinical events · Notes · Medication (sedation). Click item → side panel detail. Filters by lane and range.
- **Recordings tab:** table: code, date/time, condition, state/sedation, duration, channels, quality, pipeline version, QC status, baseline ★.
- **Assessments tab:** CRS-R history table + subscale heatmap over time.
- **Trends tab:** see P11.
- **Notes tab:** chronological signed notes, filter by type.
- **Reports tab:** generated reports with status and download.

### P6. Upload recording (wizard, 4 steps)
1. **File** — drag & drop, upload progress, SHA-256 verify.
2. **Header check** — parsed info, identity check (match/mismatch banner), channel table with normalised names and warnings (missing positions, mixed rates).
3. **Recording context** — protocol template, condition, patient state, sedation (structured), time, setting, device, reference, technologist.
4. **Review & process** — choose pipeline (default preselected), submit → job progress.

### P7. Recording detail — EEG viewer
```
┌───────────────────────────────────────────────────────────────────────────┐
│ R04 · 02 May 2026 09:12 · Resting EC · Awake · No sedation · Quality: Fair │
│ [Raw | Processed]  Montage [Average ▾]  Gain [7 µV/mm ▾]  Page [10 s ▾]    │
│ Channels [19/21 ▾]  Filters (display) [0.5–45]  Annotations ☑  ICA ☐        │
├────┬──────────────────────────────────────────────────────────────────────┤
│Fp1 │ ∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿∿                     │
│Fp2 │ ∿∿∿∿∿∿∿∿∿∿∿∿▒▒▒▒(eye artifact, shaded amber)∿∿∿∿∿∿∿                   │
│ …  │                                                                      │
│O2  │ ──────────────── (bad channel: greyed, dashed)                       │
├────┴──────────────────────────────────────────────────────────────────────┤
│ Overview strip (whole recording, quality heat bar; drag to navigate)      │
└───────────────────────────────────────────────────────────────────────────┘
 Right panel tabs: Quality | Segments | Channels | Provenance
```
- Interactions: keyboard paging (←/→), drag-select a time range → "Mark as artifact / Exclude / Select for analysis", click channel label → mark bad, hover shows time + amplitude.
- Rendering: server returns min–max decimated data for the visible window at screen resolution; Canvas draws; prefetch next/prev page.

### P8. Processing & QC
Step list (validate → standardise → filter → ICA → epochs → features) with status and parameters; ICA panel: component topomaps + time course + ICLabel suggestion with probability; checkbox to exclude; epoch rejection summary (kept/rejected bar); **Approve QC** button (clinician/technologist), with note.

### P9. Analysis
Tabbed: **Spectrum** (PSD log-scale, channel selector, band shading) · **Band power** (absolute/relative toggle, bar chart by region, table by channel) · **Spectrogram** (channel selector, time × freq heatmap, shared time cursor with viewer) · **Topomaps** (grid of 5 bands, shared colour scale toggle) · **Features** (searchable table: feature, channel/region, value, CI, unit, ⓘ plain-language definition) · **Aperiodic** (fit plot + exponent/offset + fit R²). Export CSV on every tab.

### P10. Comparison workspace
```
┌───────────────────────────────────────────────────────────────────────────┐
│ Compare: [Baseline vs latest ▾]   A: R01 12 Mar ▾    B: R04 02 May ▾       │
├───────────────────────────────────────────────────────────────────────────┤
│ COMPATIBILITY  ● Compatible with caveats                                   │
│ ✓ Same condition (rest EC) ✓ No sedation ✓ Same pipeline v1.0.0            │
│ ⚠ 17/19 channels shared (T7, O2 excluded)  ⚠ Recorded 4 h apart            │
├──────────────────────────────┬────────────────────────────────────────────┤
│ Summary (auto, neutral)      │ Topomaps A | B | Δ  (band selector)         │
│ …template text…              │                                            │
├──────────────────────────────┴────────────────────────────────────────────┤
│ Change heatmap: channels × bands (Δ, dot = exceeds SDC & q<0.05)          │
│ PSD overlay (A blue, B teal, CI shading)  · Paired bars by region          │
│ Results table (sortable, CSV)                                              │
├───────────────────────────────────────────────────────────────────────────┤
│ Clinician comment [..........]   [Save comparison]  [Sign off]             │
└───────────────────────────────────────────────────────────────────────────┘
```
Persistent footer text: "Quantitative signal changes only. Not evidence of clinical recovery or decline."

### P11. Progress & trends
- Feature picker (e.g., global relative alpha, aperiodic exponent, permutation entropy) + region picker.
- Top panel: feature over time with CI whiskers; marker shape = condition; hollow marker = poor quality; sedation icon; SDC band around baseline mean shaded.
- Middle panel: CRS-R total + subscales (small multiples) on the same time axis.
- Bottom lane: clinical events.
- Linked crosshair across panels. Toggle "show only comparable recordings".

### P12. CRS-R assessment entry
Six subscale sections, each listing items in hierarchical order with radio selection of highest item observed (as per CRS-R scoring), auto-computed subscale and total, context fields (arousal facilitation, sedation, confounders), observation text boxes (auditory, visual, motor, communication), clinician category select (required, with note "entered by clinician"), consistency hint if item pattern appears inconsistent with the chosen category. Save as draft / sign.

### P13. AI analysis (research mode)
Purple `ResearchBanner` across the top. Cards: model card summary (name, version, task, training data, evaluated performance with CIs from the evaluation report), **scope check** (OOD checklist; if failing → no prediction, explanation of why), prediction with calibrated probability and conformal set, SHAP waterfall + plain-language contributions, model pattern topomap, "How to read this" expandable. Command-following prototype view: block design plot, AUC, permutation distribution histogram with observed value, EMG check result.

### P14. Model registry (admin/researcher)
Table of models with status (staging/approved/retired), metrics, dates, approver; detail page renders full model card and evaluation report.

### P15. Reports
Builder: choose sections (checkboxes), date range, identifiers level (full / initials / pseudonymised), preview pane (paginated A4 preview), generate → draft → sign → final. History list with hash.

**PDF report layout:** header with institution, patient ID block, generated-by; sections with clean tables and the same chart style; footer: page x/y, report hash, software & pipeline versions, disclaimer.

### P16. Audit log (admin)
Filterable table: time, user, action, entity, patient, IP. Export (itself audited). Hash-chain verification status indicator.

### P17. Admin & settings
Users & roles, care-team access, site settings (line frequency 50/60 Hz, default bands, protocol templates, pipeline defaults), password policy.

---

## 3. UX rules
- Every number carries its unit, CI where applicable, and a ⓘ explaining it in plain language.
- Every chart has a **provenance** link (recording, run, pipeline version).
- Warnings are calm (amber soft background + icon + specific text), never flashing.
- Empty states explain the next step ("Upload a baseline recording to enable comparisons").
- Destructive actions require typed confirmation; nothing clinical is hard-deleted.
- Accessibility: keyboard navigation, focus rings, ARIA labels on charts with tabular alternative, colour-blind-safe palettes.
