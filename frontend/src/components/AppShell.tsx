import {
  BarChart3, ClipboardList, FileText, FlaskConical, GitCompare, LayoutDashboard, ScrollText,
  Users, Waves, type LucideIcon,
} from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

type Item = { to: string; label: string; icon: LucideIcon; ready: boolean; research?: boolean }

const MAIN: Item[] = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, ready: true },
  { to: '/patients', label: 'Patients', icon: Users, ready: true },
  { to: '/recordings', label: 'Recordings', icon: Waves, ready: true },
  { to: '/comparisons', label: 'Comparisons', icon: GitCompare, ready: true },
  { to: '/assessments', label: 'Assessments', icon: ClipboardList, ready: true },
  { to: '/reports', label: 'Reports', icon: FileText, ready: true },
]

function NavItem({ item }: { item: Item }) {
  const Icon = item.icon
  if (!item.ready) {
    return (
      <span className="nav-item disabled" aria-disabled="true">
        <Icon size={18} strokeWidth={1.5} />{item.label}<span className="soon">Soon</span>
      </span>
    )
  }
  return (
    <NavLink to={item.to} end={item.to === '/'} className={({ isActive }) =>
      `nav-item${isActive ? ' active' : ''}${item.research ? ' research' : ''}`}>
      <Icon size={18} strokeWidth={1.5} />{item.label}
    </NavLink>
  )
}

export default function AppShell() {
  const { user, logout, hasRole } = useAuth()
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main navigation">
        <div className="brand"><div className="brand-mark">NT</div>NeuroTrace</div>
        {MAIN.filter((i) => i.to === '/' || hasRole('clinician', 'eeg_technologist', 'viewer', 'researcher')).map((i) => <NavItem key={i.to} item={i} />)}

        {hasRole('researcher', 'clinician', 'admin', 'eeg_technologist', 'viewer') && (
          <>
            <div className="nav-section">Research</div>
            <NavItem item={{ to: '/research', label: 'AI analysis', icon: FlaskConical, ready: true, research: true }} />
          </>
        )}
        {hasRole('admin') && (
          <>
            <div className="nav-section">Administration</div>
            <NavItem item={{ to: '/admin/audit', label: 'Audit log', icon: ScrollText, ready: true }} />
            <NavItem item={{ to: '/admin/settings', label: 'Settings', icon: BarChart3, ready: false }} />
          </>
        )}
        <div className="sidebar-footer">v0.9.0 · Phases 0–7 + AI</div>
      </nav>

      <div className="main">
        <header className="topbar">
          <span>{user?.full_name} <span className="muted">· {user?.roles.join(', ')}</span></span>
          <button type="button" onClick={() => void logout()}
            style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 8, padding: '6px 12px', cursor: 'pointer', color: 'var(--text)' }}>
            Sign out
          </button>
        </header>
        <main className="content"><Outlet /></main>
      </div>
    </div>
  )
}
