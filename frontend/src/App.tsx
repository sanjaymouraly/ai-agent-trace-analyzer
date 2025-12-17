import { BrowserRouter, Route, Routes } from 'react-router-dom'
import AppShell from './components/AppShell'
import { ToastProvider } from './components/Toast'
import { ThemeProvider } from './lib/theme'
import AssistantPage from './pages/AssistantPage'
import AuditPage from './pages/AuditPage'
import ComparePage from './pages/ComparePage'
import OverviewPage from './pages/OverviewPage'
import RunDetailPage from './pages/RunDetailPage'
import RunsPage from './pages/RunsPage'
import SettingsPage from './pages/SettingsPage'
import ToolAnalyticsPage from './pages/ToolAnalyticsPage'
import NotFoundPage from './pages/NotFoundPage'

export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route path="/" element={<OverviewPage />} />
              <Route path="/overview" element={<OverviewPage />} />
              <Route path="/runs" element={<RunsPage />} />
              <Route path="/runs/:runId" element={<RunDetailPage />} />
              <Route path="/tools" element={<ToolAnalyticsPage />} />
              <Route path="/compare" element={<ComparePage />} />
              <Route path="/assistant" element={<AssistantPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </ThemeProvider>
  )
}
