import { BrowserRouter, Navigate, Routes, Route } from 'react-router-dom'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './lib/queryClient'
import AppShell from './components/shell/AppShell'
import ErrorBoundary from './components/ErrorBoundary'
import Home from './pages/Home'
import CrateMind from './pages/CrateMind'
import BpmReview from './pages/BpmReview'
import Export from './pages/Export'
import Jobs from './pages/Jobs'
import Reconciliation from './pages/Reconciliation'
import MetadataRepair from './pages/MetadataRepair'
import MetadataSanitation from './pages/MetadataSanitation'
import Quality from './pages/Quality'
import SetBuilder from './pages/SetBuilder'
import SsdSync from './pages/SsdSync'

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<ErrorBoundary><Home /></ErrorBoundary>} />
            <Route path="library" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
            <Route path="issues" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
            <Route path="enrichment" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
            <Route path="audit" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
            <Route path="folders" element={<ErrorBoundary><CrateMind /></ErrorBoundary>} />
            <Route path="quality" element={<ErrorBoundary><Quality /></ErrorBoundary>} />
            <Route path="metadata-repair" element={<ErrorBoundary><MetadataRepair /></ErrorBoundary>} />
            <Route path="metadata-sanitation" element={<ErrorBoundary><MetadataSanitation /></ErrorBoundary>} />
            <Route path="bpm-review" element={<ErrorBoundary><BpmReview /></ErrorBoundary>} />
            <Route path="jobs" element={<ErrorBoundary><Jobs /></ErrorBoundary>} />
            <Route path="set-builder" element={<ErrorBoundary><SetBuilder /></ErrorBoundary>} />
            <Route path="exports" element={<ErrorBoundary><Export /></ErrorBoundary>} />
            <Route path="sync" element={<ErrorBoundary><SsdSync /></ErrorBoundary>} />
            <Route path="reconciliation" element={<ErrorBoundary><Reconciliation /></ErrorBoundary>} />

            <Route path="dashboard" element={<Navigate to="/" replace />} />
            <Route path="collection" element={<Navigate to="/library" replace />} />
            <Route path="tracks" element={<Navigate to="/library" replace />} />
            <Route path="settings" element={<Navigate to="/" replace />} />
            <Route path="export" element={<Navigate to="/exports" replace />} />
            <Route path="ssd-sync" element={<Navigate to="/sync" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
