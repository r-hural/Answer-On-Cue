/** A small turning ring for a step that is under way and has no known length. Still under reduced motion. */
export function Spinner() {
  return <span className="spinner" aria-hidden="true" />
}

/** A status line for something being loaded or prepared: the spinner and the words. */
export function Loading({ children, className }: { children: string; className?: string }) {
  return (
    <p role="status" className={className ? `loading ${className}` : 'loading'}>
      <Spinner />
      {children}
    </p>
  )
}
