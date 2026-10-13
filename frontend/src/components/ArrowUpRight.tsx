/** Decorative UI arrow; SVG avoids platform-specific emoji rendering. */
export function ArrowUpRight() {
  return <svg className="arrow-up-right" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" focusable="false">
    <path d="M7 17 17 7M7 7h10v10" />
  </svg>;
}
