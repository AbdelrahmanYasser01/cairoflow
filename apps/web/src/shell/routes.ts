import type { ComponentType } from "react";
import {
  IconAnalytics,
  IconCommand,
  IconEvidence,
  IconIntersections,
  IconTwin,
  IconVision,
} from "./icons";

export interface AppRoute {
  path: string;
  label: string;
  /** Rail and bottom bar label. */
  short: string;
  Icon: ComponentType;
}

export const ROUTES: readonly AppRoute[] = [
  { path: "/", label: "Command Center", short: "Command", Icon: IconCommand },
  { path: "/intersections", label: "Intersections", short: "Sites", Icon: IconIntersections },
  { path: "/vision", label: "Vision Lab", short: "Vision", Icon: IconVision },
  { path: "/twin", label: "Digital Twin", short: "Twin", Icon: IconTwin },
  { path: "/analytics", label: "Traffic Analytics", short: "Analytics", Icon: IconAnalytics },
  { path: "/evidence", label: "Evidence & Reports", short: "Evidence", Icon: IconEvidence },
];
