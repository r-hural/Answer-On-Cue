import { svgProps } from '../../ui/Notice'

/** The upload arrow of the drop zones. */
export function UploadIcon() {
  return (
    <svg {...svgProps} viewBox="0 0 24 24">
      <path d="M12 15V4" />
      <path d="M7.5 8.5 12 4l4.5 4.5" />
      <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
    </svg>
  )
}
