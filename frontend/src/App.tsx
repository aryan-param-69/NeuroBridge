import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './auth/AuthContext'
import AppShell from './components/AppShell'
import AuditLog from './pages/AuditLog'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import PatientForm from './pages/patients/PatientForm'
import PatientList from './pages/patients/PatientList'
import AiHome from './pages/ai/AiHome'
import AssessmentPage from './pages/assessments/AssessmentPage'
import AssessmentsOverview from './pages/AssessmentsOverview'
import ComparePage from './pages/comparisons/ComparePage'
import ComparisonsHome from './pages/comparisons/ComparisonsHome'
import ReportPage from './pages/reports/ReportPage'
import ReportsHome from './pages/reports/ReportsHome'
import RecordingsOverview from './pages/RecordingsOverview'
import Users from './pages/Users'
import PatientProfile from './pages/patients/PatientProfile'
import RecordingViewer from './pages/recordings/RecordingViewer'
import UploadRecording from './pages/recordings/UploadRecording'

// Charts are heavy: load the analysis page only when it is opened.
const AnalysisPage = lazy(() => import('./pages/analysis/AnalysisPage'))

function Protected({ roles, children }: { roles?: string[]; children: React.ReactNode }) {
  const { user, loading, hasRole } = useAuth()
  if (loading) return <div className="login-wrap muted">Loading…</div>
  if (!user) return <Navigate to="/login" replace />
  if (roles && !hasRole(...roles)) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Protected><AppShell /></Protected>}>
        <Route index element={<Dashboard />} />
        <Route path="patients" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer', 'researcher']}><PatientList /></Protected>} />
        <Route path="patients/new" element={<Protected roles={['clinician']}><PatientForm /></Protected>} />
        <Route path="patients/:id" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer', 'researcher']}><PatientProfile /></Protected>} />
        <Route path="patients/:id/edit" element={<Protected roles={['clinician']}><PatientForm /></Protected>} />
        <Route path="patients/:id/recordings/upload" element={<Protected roles={['clinician', 'eeg_technologist']}><UploadRecording /></Protected>} />
        <Route path="research" element={<Protected roles={['researcher', 'clinician', 'admin', 'eeg_technologist', 'viewer']}><AiHome /></Protected>} />
        <Route path="reports" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><ReportsHome /></Protected>} />
        <Route path="reports/:id" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><ReportPage /></Protected>} />
        <Route path="recordings" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><RecordingsOverview /></Protected>} />
        <Route path="assessments" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><AssessmentsOverview /></Protected>} />
        <Route path="comparisons" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><ComparisonsHome /></Protected>} />
        <Route path="patients/:pid/compare" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><ComparePage /></Protected>} />
        <Route path="recordings/:id" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><RecordingViewer /></Protected>} />
        <Route path="patients/:pid/assessments/new" element={<Protected roles={['clinician']}><AssessmentPage /></Protected>} />
        <Route path="assessments/:id" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><AssessmentPage /></Protected>} />
        <Route path="runs/:id/analysis" element={<Protected roles={['clinician', 'eeg_technologist', 'viewer']}><Suspense fallback={<p className="muted">Loading…</p>}><AnalysisPage /></Suspense></Protected>} />
        <Route path="admin/audit" element={<Protected roles={['admin']}><AuditLog /></Protected>} />
        <Route path="admin/users" element={<Protected roles={['admin']}><Users /></Protected>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
