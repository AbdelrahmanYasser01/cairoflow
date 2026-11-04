/**
 * From the junction frame to named coordinates, using only record.calibration. EPSG:2229 is
 * NAD83 / California zone 5 (ftUS): Lambert Conformal Conic, two standard parallels, GRS80.
 *
 * Limits, also shown on screen: the frame is treated as a plane (the grid scale factor is ignored,
 * under 0.01 % over this site), and NAD83 latitude and longitude are labelled WGS84 without a datum
 * shift, which is about a metre here. These are axis ticks, not survey coordinates.
 */
import type { CalibrationState } from "../lib/analysis";

const DEG = Math.PI / 180;
const SEMI_MAJOR_M = 6378137;
const FLATTENING = 1 / 298.257222101;
const ECCENTRICITY = Math.sqrt(FLATTENING * (2 - FLATTENING));

const LAT_1 = (35 + 28 / 60) * DEG;
const LAT_2 = (34 + 2 / 60) * DEG;
const LAT_0 = 33.5 * DEG;
const LON_0 = -118 * DEG;
const FALSE_EASTING_M = 2_000_000;
const FALSE_NORTHING_M = 500_000;
export const METRES_PER_US_FOOT = 1200 / 3937;

function mFactor(lat: number): number {
  const s = Math.sin(lat);
  return Math.cos(lat) / Math.sqrt(1 - ECCENTRICITY * ECCENTRICITY * s * s);
}

function tFactor(lat: number): number {
  const s = ECCENTRICITY * Math.sin(lat);
  return Math.tan(Math.PI / 4 - lat / 2) / Math.pow((1 - s) / (1 + s), ECCENTRICITY / 2);
}

const CONE =
  (Math.log(mFactor(LAT_1)) - Math.log(mFactor(LAT_2))) /
  (Math.log(tFactor(LAT_1)) - Math.log(tFactor(LAT_2)));
const SCALE = mFactor(LAT_1) / (CONE * Math.pow(tFactor(LAT_1), CONE));
const RHO_0 = SEMI_MAJOR_M * SCALE * Math.pow(tFactor(LAT_0), CONE);

export interface GridFeet {
  eastFt: number;
  northFt: number;
}

export interface LatLon {
  latDeg: number;
  lonDeg: number;
}

export function latLonToGrid({ latDeg, lonDeg }: LatLon): GridFeet {
  const rho = SEMI_MAJOR_M * SCALE * Math.pow(tFactor(latDeg * DEG), CONE);
  const theta = CONE * (lonDeg * DEG - LON_0);
  return {
    eastFt: (rho * Math.sin(theta) + FALSE_EASTING_M) / METRES_PER_US_FOOT,
    northFt: (RHO_0 - rho * Math.cos(theta) + FALSE_NORTHING_M) / METRES_PER_US_FOOT,
  };
}

export function gridToLatLon({ eastFt, northFt }: GridFeet): LatLon {
  const x = eastFt * METRES_PER_US_FOOT - FALSE_EASTING_M;
  const y = RHO_0 - (northFt * METRES_PER_US_FOOT - FALSE_NORTHING_M);
  const rho = Math.hypot(x, y) * Math.sign(CONE);
  const t = Math.pow(rho / (SEMI_MAJOR_M * SCALE), 1 / CONE);
  let lat = Math.PI / 2 - 2 * Math.atan(t);
  for (let i = 0; i < 8; i++) {
    const s = ECCENTRICITY * Math.sin(lat);
    lat = Math.PI / 2 - 2 * Math.atan(t * Math.pow((1 - s) / (1 + s), ECCENTRICITY / 2));
  }
  return { latDeg: lat / DEG, lonDeg: (Math.atan2(x, y) / CONE + LON_0) / DEG };
}

export interface GeoAnchor {
  origin: LatLon;
  originGrid: GridFeet;
  /** Grid bearing of the frame's +v axis: degrees clockwise from grid north. */
  bearingDeg: number;
}

export function anchorFrom(calibration: CalibrationState | null | undefined): GeoAnchor | null {
  const lat = calibration?.origin_lat_deg;
  const lon = calibration?.origin_lon_deg;
  const bearing = calibration?.bearing_deg;
  if (lat == null || lon == null || bearing == null) return null;
  const origin = { latDeg: lat, lonDeg: lon };
  return { origin, originGrid: latLonToGrid(origin), bearingDeg: bearing };
}

/** Inverse of JunctionFrame.to_junction in cairoflow_perception.ngsim_junction. */
export function junctionToGrid(anchor: GeoAnchor, uM: number, vM: number): GridFeet {
  const b = anchor.bearingDeg * DEG;
  const eastM = uM * Math.cos(b) + vM * Math.sin(b);
  const northM = -uM * Math.sin(b) + vM * Math.cos(b);
  return {
    eastFt: anchor.originGrid.eastFt + eastM / METRES_PER_US_FOOT,
    northFt: anchor.originGrid.northFt + northM / METRES_PER_US_FOOT,
  };
}

export function junctionToLatLon(anchor: GeoAnchor, uM: number, vM: number): LatLon {
  return gridToLatLon(junctionToGrid(anchor, uM, vM));
}

/** Grid north as a unit vector in the junction frame. */
export function gridNorthInJunction(bearingDeg: number): readonly [number, number] {
  const b = bearingDeg * DEG;
  return [-Math.sin(b), Math.cos(b)];
}

export function formatLat(latDeg: number): string {
  return `${Math.abs(latDeg).toFixed(5)}°${latDeg >= 0 ? "N" : "S"}`;
}

export function formatLon(lonDeg: number): string {
  return `${Math.abs(lonDeg).toFixed(5)}°${lonDeg >= 0 ? "E" : "W"}`;
}

export function formatFeet(feet: number): string {
  return Math.round(feet).toLocaleString("en-US");
}
