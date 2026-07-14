import type { Tone } from '../../lib/status'

interface Props {
  tone: Tone
  label: string
  testId?: string
}

export default function StatusPill({ tone, label, testId }: Props) {
  return (
    <span className={`cm-pill cm-pill--${tone}`} data-testid={testId}>
      <span className="cm-pill-dot" aria-hidden="true" />
      {label}
    </span>
  )
}
