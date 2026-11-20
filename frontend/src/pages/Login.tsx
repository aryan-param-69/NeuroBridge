import { Button, PasswordInput, TextInput } from '@mantine/core'
import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export default function Login() {
  const { user, login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (user) return <Navigate to="/" replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="card">
          <div className="brand" style={{ padding: '0 0 20px' }}>
            <div className="brand-mark">NB</div>
            NeuroBridge AI
          </div>

          {error && <div className="notice error" role="alert">{error}</div>}

          <form className="stack" onSubmit={(e) => void submit(e)}>
            <h2>Sign in</h2>
            <TextInput label="Email" type="email" autoComplete="username" required
              value={email} onChange={(e) => setEmail(e.currentTarget.value)} />
            <PasswordInput label="Password" autoComplete="current-password" required
              value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
            <Button type="submit" loading={busy}>Sign in</Button>
          </form>
        </div>
        <p className="login-note">Authorised clinical use only. Activity is logged.</p>
      </div>
    </div>
  )
}
