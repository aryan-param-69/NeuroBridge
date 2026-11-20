export type Sex = 'male' | 'female' | 'other' | 'unknown'
export type Etiology = 'traumatic' | 'anoxic' | 'vascular' | 'infectious' | 'metabolic' | 'other' | 'unknown'
export type PatientStatus = 'active' | 'discharged' | 'archived'

/** Identifier fields are absent in the researcher (pseudonymised) view. */
export type Patient = {
  id: string
  display_id: string
  view: 'full' | 'standard' | 'research'
  sex: Sex
  etiology: Etiology
  status: PatientStatus
  craniectomy: boolean
  days_since_injury: number | null
  consent_research: boolean
  // standard + full views
  full_name?: string
  date_of_birth?: string
  age?: number
  mrn?: string | null
  etiology_other?: string | null
  date_of_injury?: string | null
  admission_date?: string | null
  clinical_status_label?: string | null
  consent_clinical?: boolean
  consent_research_date?: string | null
  tags?: string[]
  created_at?: string
  updated_at?: string
  guardian_contact?: string | null // full view only
  // research view
  birth_year?: number
}

export type PatientPage = { items: Patient[]; total: number; page: number; page_size: number }

export type HistoryItem = {
  id: string
  category: 'diagnosis' | 'comorbidity' | 'medication' | 'surgery' | 'seizure'
  description: string
  code_system: 'ICD-10' | 'SNOMED' | null
  code: string | null
  start_date: string | null
  end_date: string | null
  is_active: boolean
}

export type ClinicalEvent = {
  id: string
  event_type: 'medication_change' | 'surgery' | 'infection' | 'transfer' | 'seizure' | 'other'
  occurred_at: string
  description: string
}

export type FieldChange = {
  id: string | number; field: string; old_value: unknown; new_value: unknown; changed_by: string; changed_at: string
}

export type AccessMember = { user_id: string; full_name: string; roles: string[]; access_level: 'owner' | 'member' }
export type DirectoryUser = { id: string; full_name: string; roles: string[] }

export const ETIOLOGIES: { value: Etiology; label: string }[] = [
  { value: 'traumatic', label: 'Traumatic brain injury' },
  { value: 'anoxic', label: 'Anoxic / hypoxic' },
  { value: 'vascular', label: 'Vascular (stroke, haemorrhage)' },
  { value: 'infectious', label: 'Infectious / inflammatory' },
  { value: 'metabolic', label: 'Metabolic / toxic' },
  { value: 'other', label: 'Other' },
  { value: 'unknown', label: 'Unknown' },
]

export const etiologyLabel = (e: Etiology) => ETIOLOGIES.find((x) => x.value === e)?.label ?? e

// ---- Recordings (Phase 2) ----
export type Channel = {
  index: number; original_name: string; name: string; ch_type: string; has_position: boolean
  recognised: boolean; sfreq_hz: number; unit: string; auto_flag: string; user_flag: 'bad' | 'ok' | null
}

export type Sedation = { agent: string; dose?: number | null; unit?: string | null; time?: string | null }

export type RecordingCondition = {
  condition: string; patient_state: string; sedation: Sedation[]; anti_seizure_meds: string[]
  setting: string | null; time_of_day: string | null; device_model: string | null
  electrode_system: string; reference_label: string | null; notes: string | null
}

export type RecordingSummary = {
  id: string; patient_id: string; recording_code: string; file_format: string; start_time: string | null
  duration_s: number; n_channels: number; n_eeg_channels: number; sfreq_hz: number; size_bytes: number
  identity_check: string; warnings: string[]; condition: RecordingCondition | null
  is_baseline: boolean; qc_status: string; bad_channels: string[]; created_at: string
  quality_grade?: 'good' | 'fair' | 'poor' | null; quality_score?: number | null
}

export type RecordingDetail = RecordingSummary & {
  sha256: string; channels: Channel[]; annotations: Record<string, number>
  overview?: { bin_s: number; global_sd_uv: number[] } | null; baseline_reason?: string | null
}

export type SignalWindow = {
  sfreq: number; start: number; duration: number; montage: string; unit: string; channels: string[]
  times: number[]; data: number[][]; decimated: boolean; bad_channels: string[]
  annotations: { onset: number; duration: number; text: string }[]; total_duration: number
  stage?: 'raw' | 'processed'; run_id?: string | null; unreviewed?: boolean; epochs?: EpochMark[]; interpolated?: string[]
}

export const CONDITIONS = [
  { value: 'resting_eyes_closed', label: 'Resting, eyes closed' },
  { value: 'resting_eyes_open', label: 'Resting, eyes open' },
  { value: 'resting_unknown', label: 'Resting, eyes state unknown' },
  { value: 'task_motor_imagery', label: 'Task: motor imagery' },
  { value: 'task_command_following', label: 'Task: command following' },
  { value: 'auditory_paradigm', label: 'Auditory paradigm' },
  { value: 'other', label: 'Other' },
]
export const PATIENT_STATES = ['awake', 'drowsy', 'asleep', 'sedated', 'unknown']
export const SETTINGS = ['icu', 'ward', 'lab', 'home', 'other']
export const ELECTRODE_SYSTEMS = ['10-20', '10-10', 'custom', 'unknown']

// ---- Processing & QC (Phase 3) ----
export type QualityChannel = {
  name: string; std_uv: number | null; flat_fraction: number | null; clipped_fraction: number | null
  line_noise_db: number | null; emg_ratio: number | null; drift_ratio: number | null
  corr_low_fraction: number | null; flags: string[]; bad: boolean
}
export type Quality = {
  overall_score: number; grade: 'good' | 'fair' | 'poor'; sub_scores: Record<string, number | null>
  weights: Record<string, number>; channels: QualityChannel[]; bad_channels: string[]; calibration_note: string
  grade_cap: string | null; correlation_reference: string; usable_duration_s: number; line_freq_hz: number
  line_noise_assessed: boolean; sampled_all: boolean; sampled_seconds: number; recording_seconds: number
}
export type ProcStep = { name: string; params: Record<string, unknown>; seconds: number; summary: Record<string, unknown> }
export type EpochSummary = {
  total: number; kept: number; rejected_ptp: number; rejected_annotation: number; outside_selection: number
  masked_channel_epochs: number; masked_fraction_of_kept: number; most_masked_channels: string[]
}
export type RunSummary = {
  id: string; recording_id: string; pipeline_name: string; pipeline_version: string; params_hash: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'; qc_status: 'pending' | 'approved' | 'rejected'
  qc_note: string | null; is_current: boolean; grade: 'good' | 'fair' | 'poor' | null; score: number | null
  n_epochs_total: number | null; n_epochs_kept: number | null; bad_channels: string[]
  interpolated_channels: string[]; dropped_channels: string[]; low_confidence: boolean | null
  error: string | null; created_at: string
}
export type RunDetail = RunSummary & {
  params: Record<string, unknown>; software_versions: Record<string, string>; input_sha256: string
  output_sha256: string | null; steps: ProcStep[]; quality: Quality | null; warnings: string[]
  channels: { index: number; name: string; interpolated: boolean }[]
  epochs: { summary: EpochSummary } | null
  segments_proposed: { onset_s: number; duration_s: number; label: string }[]
}
export type JobInfo = { id: string; status: 'queued' | 'running' | 'succeeded' | 'failed'; progress: number; message: string; error: string | null; run_id: string | null }
export type SegmentAnnotation = { id: string; onset_s: number; duration_s: number; label: string; note: string | null; source: string }
export type EpochMark = { onset: number; duration: number; status: number }

// ---- Features & analysis (Phase 4) ----
export type FeatureDef = { name: string; domain: string; unit: string; description: string; how: string; caution: string }
export type Catalog = { version: string; bands: Record<string, { range_hz: number[]; plain: string }>; features: FeatureDef[] }
export type FeatureRow = {
  level: 'channel' | 'region' | 'global' | 'pair'; name: string; feature: string; band: string | null
  mean: number | null; median: number | null; sd: number | null; ci_low: number | null; ci_high: number | null
  n_epochs: number; unit: string
}
export type FeatureRunStatus = {
  status: 'none' | 'queued' | 'running' | 'succeeded' | 'failed'; run_id: string; error?: string | null; job_id?: string
  version?: string; n_epochs?: number; features_hash?: string; bands?: Record<string, number[]>
  low_confidence?: boolean; qc_status: string; unreviewed?: boolean; software_versions?: Record<string, string>
}
export type PsdResponse = {
  freqs_hz: number[]; unit: string; channels: Record<string, (number | null)[]>
  groups: Record<string, { mean: (number | null)[]; ci_low: (number | null)[]; ci_high: (number | null)[] }>
  available_channels: string[]; available_groups: string[]; bands: Record<string, number[]>; unreviewed: boolean
}
export type SpectrogramResponse = {
  channel: string; freqs_hz: number[]; times_s: number[]; log10_power: number[][]; unreviewed: boolean
  rejected_epochs: { onset: number; duration: number; status: number }[]
}
export type AperiodicFit = { level: string; name: string; exponent?: number; offset?: number; r2?: number; error?: number; good_fit: boolean }

// ---- Assessments, notes, timeline (Phase 5) ----
export type CrsrItem = { level: number; label: string; marker: 'MCS' | 'eMCS' | null }
export type CrsrSubscale = { key: string; label: string; max: number; items: CrsrItem[] }
export type CrsrDefinition = {
  instrument: string; version: string; max_total: number; rule: string; verify: string; subscales: CrsrSubscale[]
  categories: string[]; confounders: string[]; sedation: string[]; markers: Record<string, string>
}
export type CrsrScores = Record<string, number>
export type PatternCheck = {
  pattern: string; differs_from_entered: boolean; message: string | null; scoring_note: string | null; disclaimer: string
}
export type Assessment = {
  id: string; assessment_id: string; version: number; status: 'draft' | 'signed'; assessed_at: string; scores: CrsrScores
  total: number; max_total: number; setting: string | null; arousal_facilitation_used: boolean; sedation_status: string
  sedation_note: string | null; confounders: string[]; clinician_category: string | null; category_note: string | null
  pattern: PatternCheck; assessor: { id: string; name: string }; signed_by: string | null; signed_at: string | null
  amends: string | null; amendment_reason: string | null; created_at: string; observations?: Record<string, string | null>
  nearest_recording: { recording_code: string; hours_between: number } | null
}
export type AssessmentVersion = { id: string; version: number; status: string; total: number; amendment_reason: string | null; signed_at?: string | null; signed_by?: string | null }
export type AssessmentDetail = Assessment & { versions: AssessmentVersion[] }
export type ResearchAssessment = { id: string; scores: CrsrScores; total: number; clinician_category: string | null; days_since_injury: number | null }
export type TrendResponse = {
  max: Record<string, number>; max_total: number; note: string
  points: { id: string; assessed_at: string; total: number; scores: CrsrScores; clinician_category: string | null; sedation_status: string; version: number; confounders: string[] }[]
}
export type Reminder = { due: boolean; count_in_window: number; window_days: number; required: number; last_assessed_at: string | null; message: string; basis: string }
export type EegPairs = {
  window_hours: number; note: string; unpaired_recordings: string[]
  pairs: { recording: { id: string; recording_code: string; start_utc: string; quality_grade: string | null; sedated: boolean }
    assessment: { id: string; assessed_at: string; total: number; clinician_category: string | null }; hours_between: number }[]
  unpaired_assessments: { id: string; assessed_at: string; total: number }[]
}
export type Note = {
  id: string; note_id: string; version: number; note_type: 'observation' | 'interpretation' | 'plan'; body: string
  recording_id: string | null; created_at: string; first_written_at: string; n_versions: number | null; author: { id: string; name: string }
}
export type TimelineItem = {
  lane: 'recording' | 'assessment' | 'event' | 'medication' | 'note'; id: string; start: string; end: string | null
  title: string; subtitle: string; badges: string[]; link: string | null
}
export type Timeline = { lanes: string[]; items: TimelineItem[]; counts: Record<string, number>; note: string }

// ---- Overview pages ----
export type OverviewRecording = RecordingSummary & { display_id: string }
export type RecordingsPage = { total: number; page: number; page_size: number; items: OverviewRecording[]; counts: Record<string, number> }
export type AssessmentOverviewRow = {
  patient_id: string; display_id: string; n_signed: number; count_in_window: number; due: boolean; my_drafts: number
  last: { id: string; assessed_at: string; total: number; category: string | null; version: number } | null
}
export type AssessmentOverview = { window_days: number; required: number; items: AssessmentOverviewRow[]; due_count: number; basis: string }

// ---- Comparisons (Phase 6) ----
export type CmpRun = {
  run_id: string; recording_id: string; recording_code: string; start: string | null; is_baseline: boolean; condition: string | null
  patient_state: string | null; sedation: string[]; time_of_day: string | null; setting: string | null; quality_grade: string | null
  pipeline: string; features_version: string | null; n_epochs: number | null; low_confidence: boolean
}
export type CmpCandidates = { runs: CmpRun[]; default_base: string | null; default_target: string | null; note: string }
export type CmpItem = {
  feature: string; band: string | null; name: string; unit: string; base: number; target: number; base_ci: (number | null)[]
  target_ci: (number | null)[]; delta: number; relative: number | null; has_ci: boolean; interval_separated: boolean
}
export type Comparison = {
  base: CmpRun; target: CmpRun; level: string; days_between: number | null; warnings: { key: string; message: string }[]
  items: CmpItem[]; n_features: number; n_separated: number; note: string
  assessments: { base: CmpAssessment | null; target: CmpAssessment | null }
}
export type CmpAssessment = { id: string; total: number; category: string | null; assessed_at: string; hours_from_recording: number }
export type FeatureTrend = {
  feature: string; level: string; name: string; band: string | null; note: string; mixed_context: boolean
  points: { start: string | null; recording_code: string; run_id: string; mean: number; ci_low: number | null; ci_high: number | null; unit: string
    condition: string | null; sedation: string[]; quality_grade: string | null; low_confidence: boolean; is_baseline: boolean }[]
  crsr: { assessed_at: string; total: number }[]
}

// ---- AI analysis ----
export type AiPrediction = {
  status?: 'done' | 'none'; id: string; run_id: string; recording_id: string; recording_code?: string; p_sedated: number
  prediction_set: string[]; verdict: 'awake-like' | 'sedated-like' | 'uncertain'; n_epochs: number; n_windows_used: number
  unfamiliar_fraction: number; unfamiliar: boolean; epoch_spread_sd: number | null; coverage_target: number
  explanation: { feature: string; plain: string; shap: number; pushes_towards: string }[]; reliability: string[]
  model: { name: string; version: string; status: string; sha256: string; algorithm: string | null }; disclaimer: string; created_at: string
  qc_status: string; quality_grade: string | null; pipeline: string; unreviewed: boolean
  context: { condition: string | null; patient_state: string | null; sedation: string[] }; recording_start?: string | null
}
export type AiPatient = { items: AiPrediction[]; crsr: { assessed_at: string; total: number }[]; note: string }
export type AiModelInfo = {
  dir: string; name: string; version: string; status: string; intended_use: string; not_for: string[]; known_limits: string[]; model_sha256: string
  validation: { balanced_accuracy: number; ba_ci95: number[]; auc: number; permutation_p: number } | null
  training_data: { dataset: string; subjects: number; recordings_used: number; population: string } | null; model: string | null
}
export type AiListRow = { id: string; patient: string; p_sedated: number; verdict: string; unfamiliar: boolean; model_version: string
  condition: string | null; unreviewed: boolean; days_since_injury?: number | null; patient_id?: string; recording_id?: string; created_at?: string }

// ---- Reports (Phase 7) ----
export type ReportSummary = {
  id: string; report_id: string; version: number; status: 'draft' | 'signed'; title: string; clinical_summary: string
  author: { id: string; name: string }; created_at: string; signed_at: string | null; signed_by: string | null; amends: string | null
  amendment_reason: string | null; content_sha256: string; integrity_ok: boolean
}
export type ReportDetail = ReportSummary & {
  options: Record<string, unknown>; snapshot: Record<string, unknown>; versions: { id: string; version: number; status: string; signed_at: string | null; amendment_reason: string | null }[]
}
export type ReportRow = { id: string; report_id: string; patient_id: string; display_id: string; title: string; version: number; status: string; created_at: string; signed_at: string | null }
