import { useAuth } from '../auth/AuthContext'

export default function Dashboard() {
  const { user } = useAuth()
  return (
    <>
      <h1>Welcome, {user?.full_name}</h1>
      <p className="muted" style={{ marginTop: 0 }}>
        Patients, EEG recordings, quality checks, features and CRS-R assessments are available from each patient profile.
      </p>
      <div className="card" style={{ maxWidth: 560, marginTop: 24 }}>
        <h2>Your access</h2>
        <p style={{ marginTop: 0 }}>
          {user?.roles.map((r) => <span key={r} className="pill role" style={{ marginRight: 8 }}>{r}</span>)}
        </p>
        <p className="muted" style={{ marginBottom: 0 }}>
          Every access to patient data is recorded in a tamper-evident audit log.
        </p>
      </div>
    </>
  )
}
