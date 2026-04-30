import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import SettingsModal from '../settings/SettingsModal'
import './AppLayout.css'

export default function AppLayout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem('astudio.sidebarCollapsed') === 'true'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem('astudio.sidebarCollapsed', String(sidebarCollapsed))
    } catch {
      // Ignore storage errors in restricted runtimes.
    }
  }, [sidebarCollapsed])

  return (
    <div className={`app-layout ${sidebarCollapsed ? 'app-layout--sidebar-collapsed' : ''}`}>
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapsed={() => setSidebarCollapsed(value => !value)}
      />
      <main className="app-main">
        <Outlet />
      </main>
      <SettingsModal />
    </div>
  )
}
