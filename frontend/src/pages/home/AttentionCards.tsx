import {
  Wrench,
  Eraser,
  Sparkles,
  Activity,
  FileX,
  FilePlus2,
  XCircle,
} from 'lucide-react'
import ActionCard from '../../components/ui/ActionCard'
import { EmptyState } from '../../components/ui/StatePanel'

export interface AttentionCounts {
  repairPending: number | null
  sanitationPending: number | null
  enrichmentReviewCount: number | null
  bpmPending: number | null
  missingFiles: number | null
  untrackedFiles: number | null
  failedJobs: number | null
}

interface Props {
  counts: AttentionCounts
  loading: boolean
}

export default function AttentionCards({ counts, loading }: Props) {
  if (loading) {
    return (
      <div className="cm-attention-list" data-testid="attention-loading">
        <div className="cm-skeleton" style={{ height: 64, borderRadius: 9 }} />
        <div className="cm-skeleton" style={{ height: 64, borderRadius: 9 }} />
      </div>
    )
  }

  const cards: JSX.Element[] = []

  if (counts.failedJobs && counts.failedJobs > 0) {
    cards.push(
      <ActionCard
        key="failed-jobs"
        icon={XCircle}
        tone="danger"
        title={`${counts.failedJobs} failed ${counts.failedJobs === 1 ? 'job' : 'jobs'}`}
        detail="Recent operations failed and need inspection."
        to="/jobs"
        testId="attention-failed-jobs"
      />,
    )
  }
  if (counts.repairPending && counts.repairPending > 0) {
    cards.push(
      <ActionCard
        key="repair"
        icon={Wrench}
        tone="review"
        title={`Review ${counts.repairPending} metadata ${counts.repairPending === 1 ? 'repair' : 'repairs'}`}
        detail="Deterministic proposals from filename parsing. Database-only after approval."
        to="/metadata-repair"
        testId="attention-repair"
      />,
    )
  }
  if (counts.sanitationPending && counts.sanitationPending > 0) {
    cards.push(
      <ActionCard
        key="sanitation"
        icon={Eraser}
        tone="review"
        title={`Review ${counts.sanitationPending} sanitation ${counts.sanitationPending === 1 ? 'proposal' : 'proposals'}`}
        detail="Junk tokens detected in tags — promo URLs, bracket noise, label suffixes."
        to="/metadata-sanitation"
        testId="attention-sanitation"
      />,
    )
  }
  if (counts.enrichmentReviewCount && counts.enrichmentReviewCount > 0) {
    cards.push(
      <ActionCard
        key="enrichment"
        icon={Sparkles}
        tone="review"
        title={`Review ${counts.enrichmentReviewCount} enrichment ${counts.enrichmentReviewCount === 1 ? 'match' : 'matches'}`}
        detail="Online candidates matched against your tracks. Nothing applies without review."
        to="/enrichment"
        testId="attention-enrichment"
      />,
    )
  }
  if (counts.bpmPending && counts.bpmPending > 0) {
    cards.push(
      <ActionCard
        key="bpm"
        icon={Activity}
        tone="warning"
        title={`Review ${counts.bpmPending} BPM ${counts.bpmPending === 1 ? 'anomaly' : 'anomalies'}`}
        detail="Suspicious BPM values flagged. BPM and key are never overwritten automatically."
        to="/bpm-review"
        testId="attention-bpm"
      />,
    )
  }
  if (counts.missingFiles && counts.missingFiles > 0) {
    cards.push(
      <ActionCard
        key="missing"
        icon={FileX}
        tone="danger"
        title={`${counts.missingFiles} missing ${counts.missingFiles === 1 ? 'file' : 'files'}`}
        detail="Database rows reference files that no longer exist on disk."
        to="/audit"
        testId="attention-missing-files"
      />,
    )
  }
  if (counts.untrackedFiles && counts.untrackedFiles > 0) {
    cards.push(
      <ActionCard
        key="untracked"
        icon={FilePlus2}
        tone="warning"
        title={`${counts.untrackedFiles} untracked ${counts.untrackedFiles === 1 ? 'file' : 'files'}`}
        detail="Audio files on disk that are not represented in the database."
        to="/audit"
        testId="attention-untracked-files"
      />,
    )
  }

  if (cards.length === 0) {
    return (
      <EmptyState
        title="Nothing requires attention"
        detail="Review queues are clear and no path issues were reported by the last audit."
        testId="attention-all-clear"
      />
    )
  }

  return (
    <div className="cm-attention-list" data-testid="attention-cards">
      {cards}
    </div>
  )
}
