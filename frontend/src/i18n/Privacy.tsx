import { appStrings } from './app'

export function PrivacyNotice() {
  return (
    <div className="notice">
      <svg
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="10" cy="10" r="7.5" />
        <path d="M10 9v4.5" />
        <path d="M10 6.4v.2" />
      </svg>
      <div>
        <strong>{appStrings.privacy.title}</strong> {appStrings.privacy.body}
      </div>
    </div>
  )
}
