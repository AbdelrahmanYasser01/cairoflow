import type { ReactNode } from "react";

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** A signal head: the room's own instrument. */
export const IconCommand = () => (
  <Icon>
    <rect x="6.5" y="1.7" width="7" height="16.6" />
    <circle cx="10" cy="5.2" r="1.5" />
    <circle cx="10" cy="10" r="1.5" />
    <circle cx="10" cy="14.8" r="1.5" />
  </Icon>
);

/** Two roads crossing, in plan. */
export const IconIntersections = () => (
  <Icon>
    <path d="M7 1.5V7H1.5M13 1.5V7h5.5M7 18.5V13H1.5M13 18.5V13h5.5" />
  </Icon>
);

/** A viewfinder holding one detection box. */
export const IconVision = () => (
  <Icon>
    <path d="M1.7 6V1.7H6M14 1.7h4.3V6M18.3 14v4.3H14M6 18.3H1.7V14" />
    <rect x="6" y="7" width="8" height="6" />
  </Icon>
);

export const IconTwin = () => (
  <Icon>
    <path d="m10 1.8 7 4v8.4l-7 4-7-4V5.800z" />
    <path d="m3 5.800 7 4 7-4M10 9.800v8.400" />
  </Icon>
);

/** A through-and-right lane arrow, the way a road paints a movement. */
export const IconAnalytics = () => (
  <Icon>
    <path d="M8 18.500V3.500M4.700 6.800 8 3.500l3.300 3.300" />
    <path d="M8 13.500c0-3 2-4.500 5-4.500h4M14.200 6l3 3-3 3" />
  </Icon>
);

/** Two chain links: provenance. */
export const IconEvidence = () => (
  <Icon>
    <path d="m8.300 11.700 3.400-3.400" />
    <path d="M10.700 5.300 12 4a3.300 3.300 0 0 1 4.700 4.700l-2.300 2.300a3.300 3.300 0 0 1-3.400.8" />
    <path d="M9.300 14.700 8 16a3.300 3.300 0 0 1-4.700-4.700l2.300-2.300a3.300 3.300 0 0 1 3.400-.8" />
  </Icon>
);

export const IconSheet = () => (
  <Icon>
    <path d="M2 2h7v7H2zM11 11h7v7h-7zM11 2h7v7h-7zM2 11h7v7H2z" />
  </Icon>
);

/** The product mark: four stop bars around one amber lamp, a junction seen from above. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <g fill="var(--paint-1)">
        <rect x="12" y="2" width="8" height="3" />
        <rect x="12" y="27" width="8" height="3" />
        <rect x="2" y="12" width="3" height="8" />
        <rect x="27" y="12" width="3" height="8" />
      </g>
      <circle cx="16" cy="16" r="5.500" fill="var(--amber)" />
    </svg>
  );
}
