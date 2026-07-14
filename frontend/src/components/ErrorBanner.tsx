interface Props {
  message:   string | null
  onDismiss?: () => void
}

/**
 * Displays an inline error message. Renders nothing when message is null.
 */
export default function ErrorBanner({ message, onDismiss }: Props) {
  if (!message) return null
  return (
    <div className="error-banner" role="alert">
      <strong>Error:</strong> {message}
      {onDismiss && (
        <button
          onClick={onDismiss}
          className="error-banner-dismiss"
          aria-label="Dismiss"
        >
          ×
        </button>
      )}
    </div>
  )
}
