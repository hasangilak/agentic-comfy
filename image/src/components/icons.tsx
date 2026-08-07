/**
 * Drawn icons, not glyphs — one family, 16px box, 1.4 stroke, currentColor.
 * Every icon is decorative: the control that holds it supplies the accessible name.
 */
import type { SVGProps } from 'react'

const base: SVGProps<SVGSVGElement> = {
  width: 16,
  height: 16,
  viewBox: '0 0 16 16',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.4,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
  focusable: false,
}

export function IconTrash(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M2.75 4.25h10.5" />
      <path d="M6.25 4.25V3a.75.75 0 0 1 .75-.75h2a.75.75 0 0 1 .75.75v1.25" />
      <path d="M4.25 4.25 4.9 13.05a.75.75 0 0 0 .75.7h4.7a.75.75 0 0 0 .75-.7l.65-8.8" />
      <path d="M6.75 6.9v4.4M9.25 6.9v4.4" />
    </svg>
  )
}

export function IconExpand(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...base} {...props}>
      <path d="M9.75 2.75h3.5v3.5" />
      <path d="M6.25 13.25h-3.5v-3.5" />
      <path d="M13.25 2.75 9.5 6.5" />
      <path d="M2.75 13.25 6.5 9.5" />
    </svg>
  )
}
