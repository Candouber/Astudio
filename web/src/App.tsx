import { useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import AppLayout from './components/layout/AppLayout'
import ChatHome from './pages/ChatHome'
import TaskBoard from './pages/TaskBoard'
import TaskDetail from './pages/TaskDetail'
import Studios from './pages/Studios'
import StudioDetail from './pages/StudioDetail'
import AgentDetail from './pages/AgentDetail'
import Sandboxes from './pages/Sandboxes'
import SandboxDetail from './pages/SandboxDetail'
import Schedules from './pages/Schedules'
import ScheduleResults from './pages/ScheduleResults'
import SkillPool from './pages/SkillPool'
import { useConfigStore } from './stores/configStore'

export default function App() {
  const fetchConfig = useConfigStore(s => s.fetchConfig)
  useEffect(() => { fetchConfig() }, [fetchConfig])

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<ChatHome />} />
        <Route path="/tasks" element={<TaskBoard />} />
        <Route path="/tasks/:id" element={<TaskDetail />} />
        <Route path="/studios" element={<Studios />} />
        <Route path="/studios/:id" element={<StudioDetail />} />
        <Route path="/studios/:id/agents/:memberId" element={<AgentDetail />} />
        <Route path="/sandboxes" element={<Sandboxes />} />
        <Route path="/sandboxes/:id" element={<SandboxDetail />} />
        <Route path="/schedules" element={<Schedules />} />
        <Route path="/schedule-results" element={<ScheduleResults />} />
        <Route path="/skills" element={<SkillPool />} />
      </Route>
    </Routes>
  )
}
