import { Button, Modal, MultiSelect, PasswordInput, TextInput } from '@mantine/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, del, patch, post } from '../api/client'
import { useAuth } from '../auth/AuthContext'

type Row = { id: string; email: string; full_name: string; roles: string[]; is_active: boolean; last_login_at: string | null }

const ROLES = [
  { value: 'clinician', label: 'Clinician' },
  { value: 'eeg_technologist', label: 'EEG technologist' },
  { value: 'viewer', label: 'Viewer' },
  { value: 'researcher', label: 'Researcher' },
  { value: 'admin', label: 'Admin' },
]
const POLICY = 'At least 12 characters, with an upper-case letter, a lower-case letter and a digit.'

type Dialog =
  | { kind: 'add' }
  | { kind: 'password'; user: Row }
  | { kind: 'roles'; user: Row }
  | { kind: 'delete'; user: Row }
  | null

export default function Users() {
  const { user: me } = useAuth()
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ['admin-users'], queryFn: () => api<Row[]>('/admin/users') })
  const [dialog, setDialog] = useState<Dialog>(null)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [form, setForm] = useState({ full_name: '', email: '', password: '', roles: ['clinician'] as string[] })
  const [password, setPassword] = useState('')
  const [roles, setRoles] = useState<string[]>([])

  const close = () => { setDialog(null); setError(null); setPassword('') }
  const done = (text: string) => { setMessage(text); close(); void qc.invalidateQueries({ queryKey: ['admin-users'] }) }
  const fail = (e: unknown) => setError((e as Error).message)

  const create = useMutation({
    mutationFn: () => post('/admin/users', { ...form, email: form.email.trim() }),
    onSuccess: () => { done(`Created ${form.email.trim()}`); setForm({ full_name: '', email: '', password: '', roles: ['clinician'] }) },
    onError: fail,
  })
  const reset = useMutation({
    mutationFn: (u: Row) => post(`/admin/users/${u.id}/password`, { password }),
    onSuccess: (_, u) => done(`Password changed for ${u.email}. They are signed out everywhere and any lockout is cleared.`),
    onError: fail,
  })
  const setRoleList = useMutation({
    mutationFn: (u: Row) => patch(`/admin/users/${u.id}`, { roles }),
    onSuccess: (_, u) => done(`Roles updated for ${u.email}`),
    onError: fail,
  })
  const toggle = useMutation({
    mutationFn: (u: Row) => patch(`/admin/users/${u.id}`, { is_active: !u.is_active }),
    onSuccess: (_, u) => done(`${u.email} ${u.is_active ? 'deactivated' : 'activated'}`),
    onError: (e) => { setMessage(null); setError((e as Error).message) },
  })
  const remove = useMutation({
    mutationFn: (u: Row) => del(`/admin/users/${u.id}`),
    onSuccess: (_, u) => done(`Deleted ${u.email}`),
    onError: fail,
  })

  const busy = create.isPending || reset.isPending || setRoleList.isPending || remove.isPending

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'end', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ marginBottom: 4 }}>Users</h1>
          <p className="muted" style={{ margin: 0 }}>Add people, change their password or role, and remove accounts. Every change is written to the audit log.</p>
        </div>
        <Button onClick={() => { setDialog({ kind: 'add' }); setError(null) }}>Add user</Button>
      </div>

      {message && <div className="notice" style={{ background: 'var(--accent-soft)', color: 'var(--accent)', marginTop: 16 }}>{message}</div>}
      {error && !dialog && <div className="notice error" style={{ marginTop: 16 }}>{error}</div>}

      <div className="card" style={{ padding: 0, overflow: 'hidden', marginTop: 16 }}>
        <table className="data">
          <thead><tr><th>Name</th><th>Email</th><th>Roles</th><th>Status</th><th>Last sign-in (UTC)</th><th>Actions</th></tr></thead>
          <tbody>
            {users.data?.map((u) => {
              const self = u.id === me?.id
              return (
                <tr key={u.id}>
                  <td>{u.full_name}{self && <span className="muted"> (you)</span>}</td>
                  <td className="mono">{u.email}</td>
                  <td>{u.roles.map((r) => <span key={r} className="pill role" style={{ marginRight: 6 }}>{r}</span>)}</td>
                  <td><span className={`pill ${u.is_active ? 'ok' : 'bad'}`}>{u.is_active ? 'active' : 'deactivated'}</span></td>
                  <td>{u.last_login_at ? new Date(u.last_login_at).toISOString().slice(0, 16).replace('T', ' ') : 'never'}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <Button size="compact-sm" variant="light" mr={6} onClick={() => { setDialog({ kind: 'password', user: u }); setError(null) }}>Password</Button>
                    <Button size="compact-sm" variant="light" mr={6} onClick={() => { setRoles(u.roles); setDialog({ kind: 'roles', user: u }); setError(null) }}>Roles</Button>
                    <Button size="compact-sm" variant="light" mr={6} disabled={self || toggle.isPending} onClick={() => { setError(null); setMessage(null); toggle.mutate(u) }}>
                      {u.is_active ? 'Deactivate' : 'Activate'}
                    </Button>
                    <Button size="compact-sm" variant="light" color="red" disabled={self} onClick={() => { setDialog({ kind: 'delete', user: u }); setError(null) }}>Delete</Button>
                  </td>
                </tr>
              )
            })}
            {users.isLoading && <tr><td colSpan={6} className="muted">Loading…</td></tr>}
            {users.isError && <tr><td colSpan={6} className="muted">Could not load users.</td></tr>}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 13 }}>
        Delete removes an account that never wrote or signed any record. If a user did, the platform keeps their name on those records, so deactivate the account instead.
        Deactivated users cannot sign in.
      </p>

      <Modal opened={dialog?.kind === 'add'} onClose={close} title="Add user" centered>
        <form onSubmit={(e) => { e.preventDefault(); setError(null); create.mutate() }}>
          <TextInput label="Full name" required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.currentTarget.value })} mb="sm" />
          <TextInput label="Email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.currentTarget.value })} mb="sm"
            description="Use a real-looking domain; reserved ones such as .local or .test are rejected." />
          <PasswordInput label="Password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.currentTarget.value })} mb="sm" description={POLICY} autoComplete="new-password" />
          <MultiSelect label="Roles" data={ROLES} value={form.roles} onChange={(v) => setForm({ ...form, roles: v })} mb="md" />
          {error && <div className="notice error" style={{ marginBottom: 12 }}>{error}</div>}
          <Button type="submit" loading={busy} disabled={!form.roles.length}>Create user</Button>
        </form>
      </Modal>

      <Modal opened={dialog?.kind === 'password'} onClose={close} title={dialog?.kind === 'password' ? `New password for ${dialog.user.email}` : ''} centered>
        {dialog?.kind === 'password' && (
          <form onSubmit={(e) => { e.preventDefault(); setError(null); reset.mutate(dialog.user) }}>
            <PasswordInput label="New password" required value={password} onChange={(e) => setPassword(e.currentTarget.value)} description={POLICY} autoComplete="new-password" mb="md" />
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>The user is signed out everywhere and any lockout is cleared. Tell them the password in person.</p>
            {error && <div className="notice error" style={{ marginBottom: 12 }}>{error}</div>}
            <Button type="submit" loading={busy}>Change password</Button>
          </form>
        )}
      </Modal>

      <Modal opened={dialog?.kind === 'roles'} onClose={close} title={dialog?.kind === 'roles' ? `Roles for ${dialog.user.email}` : ''} centered>
        {dialog?.kind === 'roles' && (
          <>
            <MultiSelect label="Roles" data={ROLES} value={roles} onChange={setRoles} mb="md" />
            {error && <div className="notice error" style={{ marginBottom: 12 }}>{error}</div>}
            <Button loading={busy} disabled={!roles.length} onClick={() => { setError(null); setRoleList.mutate(dialog.user) }}>Save roles</Button>
          </>
        )}
      </Modal>

      <Modal opened={dialog?.kind === 'delete'} onClose={close} title="Delete this account?" centered>
        {dialog?.kind === 'delete' && (
          <>
            <p style={{ marginTop: 0 }}>This permanently deletes <b>{dialog.user.full_name}</b> ({dialog.user.email}). It cannot be undone.</p>
            {error && <div className="notice error" style={{ marginBottom: 12 }}>{error}</div>}
            <Button color="red" loading={busy} onClick={() => { setError(null); remove.mutate(dialog.user) }} mr="sm">Delete permanently</Button>
            <Button variant="default" onClick={close}>Cancel</Button>
          </>
        )}
      </Modal>
    </>
  )
}
