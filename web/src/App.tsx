import { lazy, Suspense, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import AppLayout from './components/layout/AppLayout'
import ToastViewport from './components/common/ToastViewport'
import { useConfigStore } from './stores/configStore'

const ChatHome = lazy(() => import('./pages/ChatHome'))
const TaskBoard = lazy(() => import('./pages/TaskBoard'))
const TaskDetail = lazy(() => import('./pages/TaskDetail'))
const Studios = lazy(() => import('./pages/Studios'))
const StudioDetail = lazy(() => import('./pages/StudioDetail'))
const AgentDetail = lazy(() => import('./pages/AgentDetail'))
const Sandboxes = lazy(() => import('./pages/Sandboxes'))
const SandboxDetail = lazy(() => import('./pages/SandboxDetail'))
const Schedules = lazy(() => import('./pages/Schedules'))
const ScheduleResults = lazy(() => import('./pages/ScheduleResults'))
const SkillPool = lazy(() => import('./pages/SkillPool'))
const About = lazy(() => import('./pages/About'))

export default function App() {
  const fetchConfig = useConfigStore(s => s.fetchConfig)
  useEffect(() => { fetchConfig() }, [fetchConfig])

  return (
    <Suspense fallback={null}>
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
          <Route path="/about" element={<About />} />
        </Route>
      </Routes>
      <ToastViewport />
    </Suspense>
  )
}
