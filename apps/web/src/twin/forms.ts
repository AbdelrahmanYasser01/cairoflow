/**
 * Low-poly vehicle bodies, built in code. Each is a unit box: width and length 1, height 1, nose
 * toward -z, sitting on y = 0. An instance scales it to the track's real length and width.
 * Body shape and height are drawing conventions, not measurements, and the overlay says so.
 *
 * Shade multiplies the instance colour: body 1, glass near 0. Glow feeds the material's emission:
 * above 0 a headlamp, below 0 a tail lamp.
 */
import { BufferAttribute, BufferGeometry } from "three";

type V3 = readonly [number, number, number];

const BODY = 1;
const SKIRT = 0.5;
const GLASS = 0.09;
const RUBBER = 0.05;

class Soup {
  private positions: number[] = [];
  private normals: number[] = [];
  private shades: number[] = [];
  private glows: number[] = [];

  /** `outside` is a point the face should turn away from: it settles the winding. */
  tri(a: V3, b: V3, c: V3, shade: number, glow: number, inside: V3): void {
    const ux = b[0] - a[0];
    const uy = b[1] - a[1];
    const uz = b[2] - a[2];
    const vx = c[0] - a[0];
    const vy = c[1] - a[1];
    const vz = c[2] - a[2];
    let nx = uy * vz - uz * vy;
    let ny = uz * vx - ux * vz;
    let nz = ux * vy - uy * vx;
    const length = Math.hypot(nx, ny, nz);
    if (length < 1e-9) return;
    nx /= length;
    ny /= length;
    nz /= length;
    const mx = (a[0] + b[0] + c[0]) / 3 - inside[0];
    const my = (a[1] + b[1] + c[1]) / 3 - inside[1];
    const mz = (a[2] + b[2] + c[2]) / 3 - inside[2];
    const flip = nx * mx + ny * my + nz * mz < 0;
    const order = flip ? [a, c, b] : [a, b, c];
    const sign = flip ? -1 : 1;
    for (const p of order) {
      this.positions.push(p[0], p[1], p[2]);
      this.normals.push(nx * sign, ny * sign, nz * sign);
      this.shades.push(shade, shade, shade);
      this.glows.push(glow);
    }
  }

  quad(a: V3, b: V3, c: V3, d: V3, shade: number, glow: number, inside: V3): void {
    this.tri(a, b, c, shade, glow, inside);
    this.tri(a, c, d, shade, glow, inside);
  }

  box(min: V3, max: V3, shade: number, glow = 0): void {
    const [x0, y0, z0] = min;
    const [x1, y1, z1] = max;
    const inside: V3 = [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2];
    const p = (x: number, y: number, z: number): V3 => [x, y, z];
    this.quad(p(x0, y0, z0), p(x1, y0, z0), p(x1, y1, z0), p(x0, y1, z0), shade, glow, inside);
    this.quad(p(x0, y0, z1), p(x1, y0, z1), p(x1, y1, z1), p(x0, y1, z1), shade, glow, inside);
    this.quad(p(x0, y0, z0), p(x0, y1, z0), p(x0, y1, z1), p(x0, y0, z1), shade, glow, inside);
    this.quad(p(x1, y0, z0), p(x1, y1, z0), p(x1, y1, z1), p(x1, y0, z1), shade, glow, inside);
    this.quad(p(x0, y1, z0), p(x1, y1, z0), p(x1, y1, z1), p(x0, y1, z1), shade, glow, inside);
    this.quad(p(x0, y0, z0), p(x1, y0, z0), p(x1, y0, z1), p(x0, y0, z1), shade, glow, inside);
  }

  /** A short cylinder across the vehicle. Radii differ because the unit box is not square. */
  wheel(x: number, centreY: number, centreZ: number, radiusY: number, radiusZ: number, halfWidth: number): void {
    const sides = 8;
    const inside: V3 = [x, centreY, centreZ];
    const rim = (i: number, side: number): V3 => {
      const angle = (i / sides) * Math.PI * 2;
      return [x + side * halfWidth, centreY + Math.sin(angle) * radiusY, centreZ + Math.cos(angle) * radiusZ];
    };
    for (let i = 0; i < sides; i++) {
      this.quad(rim(i, -1), rim(i + 1, -1), rim(i + 1, 1), rim(i, 1), RUBBER, 0, inside);
      this.tri([x + halfWidth, centreY, centreZ], rim(i, 1), rim(i + 1, 1), 0.18, 0, inside);
      this.tri([x - halfWidth, centreY, centreZ], rim(i, -1), rim(i + 1, -1), 0.18, 0, inside);
    }
  }

  /** Skins rings of equal size along z. `shade` gets the band around the ring and the span along z. */
  loft(
    sections: readonly { z: number; ring: readonly (readonly [number, number])[] }[],
    shade: (band: number, span: number) => number,
    inside: V3,
  ): void {
    const size = sections[0].ring.length;
    for (let s = 0; s + 1 < sections.length; s++) {
      const near = sections[s];
      const far = sections[s + 1];
      for (let band = 0; band < size; band++) {
        const next = (band + 1) % size;
        const a: V3 = [near.ring[band][0], near.ring[band][1], near.z];
        const b: V3 = [near.ring[next][0], near.ring[next][1], near.z];
        const c: V3 = [far.ring[next][0], far.ring[next][1], far.z];
        const d: V3 = [far.ring[band][0], far.ring[band][1], far.z];
        this.quad(a, b, c, d, shade(band, s), 0, inside);
      }
    }
    for (const end of [sections[0], sections[sections.length - 1]]) {
      const p = (i: number): V3 => [end.ring[i][0], end.ring[i][1], end.z];
      for (let i = 1; i + 1 < size; i++) this.tri(p(0), p(i), p(i + 1), BODY, 0, inside);
    }
  }

  build(): BufferGeometry {
    const geometry = new BufferGeometry();
    geometry.setAttribute("position", new BufferAttribute(new Float32Array(this.positions), 3));
    geometry.setAttribute("normal", new BufferAttribute(new Float32Array(this.normals), 3));
    geometry.setAttribute("color", new BufferAttribute(new Float32Array(this.shades), 3));
    geometry.setAttribute("aGlow", new BufferAttribute(new Float32Array(this.glows), 1));
    return geometry;
  }
}

/** Sill, belt and roof pairs, mirrored: six points around the body at one station. */
function ring(
  sillW: number,
  sillY: number,
  beltW: number,
  beltY: number,
  roofW: number,
  roofY: number,
): readonly (readonly [number, number])[] {
  return [
    [-sillW, sillY],
    [-beltW, beltY],
    [-roofW, roofY],
    [roofW, roofY],
    [beltW, beltY],
    [sillW, sillY],
  ];
}

const SIDE_LOW = [0, 4];
const SIDE_GLASS = [1, 3];
const TOP = 2;

function car(): BufferGeometry {
  const soup = new Soup();
  const sections = [
    { z: -0.5, ring: ring(0.41, 0.2, 0.43, 0.4, 0.33, 0.47) },
    { z: -0.465, ring: ring(0.47, 0.15, 0.48, 0.47, 0.4, 0.54) },
    { z: -0.2, ring: ring(0.5, 0.15, 0.5, 0.55, 0.43, 0.61) },
    { z: -0.1, ring: ring(0.5, 0.15, 0.5, 0.58, 0.42, 0.64) },
    { z: 0.03, ring: ring(0.5, 0.15, 0.49, 0.61, 0.36, 0.97) },
    { z: 0.26, ring: ring(0.5, 0.15, 0.49, 0.63, 0.36, 0.96) },
    { z: 0.38, ring: ring(0.5, 0.15, 0.49, 0.65, 0.42, 0.69) },
    { z: 0.5, ring: ring(0.44, 0.22, 0.45, 0.6, 0.37, 0.66) },
  ];
  soup.loft(
    sections,
    (band, span) => {
      if (SIDE_GLASS.includes(band)) return span >= 3 && span <= 5 ? GLASS : BODY;
      if (band === TOP) return span === 3 || span === 5 ? GLASS : BODY;
      if (SIDE_LOW.includes(band)) return BODY;
      return SKIRT;
    },
    [0, 0.45, 0],
  );
  // Two lamps at each end, proud of the body by a sliver.
  for (const x of [-0.31, 0.31]) {
    soup.box([x - 0.1, 0.33, -0.506], [x + 0.1, 0.42, -0.495], BODY, 0.85);
    soup.box([x - 0.11, 0.42, 0.495], [x + 0.11, 0.53, 0.506], BODY, -0.7);
  }
  for (const z of [-0.31, 0.31]) {
    for (const x of [-0.45, 0.45]) soup.wheel(x, 0.22, z, 0.22, 0.07, 0.06);
  }
  return soup.build();
}

function truck(): BufferGeometry {
  const soup = new Soup();
  const cabBack = -0.2;
  const cab = [
    { z: -0.5, ring: ring(0.47, 0.1, 0.47, 0.42, 0.44, 0.5) },
    { z: -0.47, ring: ring(0.5, 0.1, 0.5, 0.46, 0.46, 0.76) },
    { z: -0.42, ring: ring(0.5, 0.1, 0.5, 0.47, 0.45, 0.8) },
    { z: cabBack, ring: ring(0.5, 0.1, 0.5, 0.48, 0.45, 0.8) },
  ];
  soup.loft(
    cab,
    (band, span) => {
      if (SIDE_GLASS.includes(band)) return span >= 1 && span <= 1 ? GLASS : span === 2 ? GLASS : BODY;
      if (band === TOP) return span === 0 ? GLASS : BODY;
      return SIDE_LOW.includes(band) ? BODY : SKIRT;
    },
    [0, 0.42, -0.35],
  );
  // Cargo box, a gap ahead of it, chassis under it.
  soup.box([-0.5, 0.2, cabBack + 0.018], [0.5, 1, 0.5], 0.82);
  soup.box([-0.42, 0.1, -0.5], [0.42, 0.2, 0.5], 0.22);
  for (const x of [-0.31, 0.31]) {
    soup.box([x - 0.1, 0.18, -0.504], [x + 0.1, 0.26, -0.495], BODY, 0.85);
    soup.box([x - 0.08, 0.24, 0.5], [x + 0.08, 0.3, 0.504], BODY, -0.7);
  }
  for (const z of [-0.36, 0.2, 0.33]) {
    for (const x of [-0.45, 0.45]) soup.wheel(x, 0.14, z, 0.14, 0.05, 0.06);
  }
  return soup.build();
}

function bus(): BufferGeometry {
  const soup = new Soup();
  const sections = [
    { z: -0.5, ring: ring(0.48, 0.08, 0.48, 0.48, 0.44, 0.9) },
    { z: -0.48, ring: ring(0.5, 0.08, 0.5, 0.5, 0.46, 0.98) },
    { z: 0.48, ring: ring(0.5, 0.08, 0.5, 0.5, 0.46, 0.98) },
    { z: 0.5, ring: ring(0.48, 0.08, 0.48, 0.48, 0.44, 0.94) },
  ];
  soup.loft(
    sections,
    (band, span) => {
      if (SIDE_GLASS.includes(band)) return span === 1 ? GLASS : BODY;
      if (band === TOP) return span === 0 ? GLASS : BODY;
      return SIDE_LOW.includes(band) ? BODY : SKIRT;
    },
    [0, 0.5, 0],
  );
  soup.box([-0.47, 0.5, -0.503], [0.47, 0.88, -0.5], GLASS);
  // Pillars break the window band into panes.
  for (let z = -0.38; z <= 0.42; z += 0.13) {
    for (const x of [-0.5, 0.5]) soup.box([x - 0.012, 0.52, z - 0.006], [x + 0.012, 0.92, z + 0.006], BODY);
  }
  soup.box([-0.3, 0.985, -0.1], [0.3, 1, 0.22], 0.7);
  for (const x of [-0.33, 0.33]) {
    soup.box([x - 0.1, 0.18, -0.505], [x + 0.1, 0.25, -0.498], BODY, 0.85);
    soup.box([x - 0.08, 0.22, 0.498], [x + 0.08, 0.3, 0.505], BODY, -0.7);
  }
  for (const z of [-0.3, 0.3]) {
    for (const x of [-0.45, 0.45]) soup.wheel(x, 0.15, z, 0.15, 0.04, 0.06);
  }
  return soup.build();
}

function motorcycle(): BufferGeometry {
  const soup = new Soup();
  soup.wheel(0, 0.21, -0.33, 0.21, 0.16, 0.07);
  soup.wheel(0, 0.21, 0.33, 0.21, 0.16, 0.07);
  soup.box([-0.13, 0.26, -0.22], [0.13, 0.46, 0.2], 0.6);
  soup.box([-0.2, 0.44, -0.2], [0.2, 0.58, 0.02], BODY);
  soup.box([-0.17, 0.5, 0.02], [0.17, 0.57, 0.3], 0.2);
  soup.box([-0.44, 0.62, -0.25], [0.44, 0.66, -0.21], 0.25);
  soup.box([-0.1, 0.3, -0.34], [0.1, 0.64, -0.3], BODY);
  soup.box([-0.12, 0.44, -0.355], [0.12, 0.54, -0.34], BODY, 0.85);
  soup.box([-0.1, 0.5, 0.3], [0.1, 0.56, 0.32], BODY, -0.7);
  // A rider, since nothing else says what is moving at this scale.
  soup.box([-0.21, 0.58, -0.05], [0.21, 0.92, 0.17], 0.42);
  soup.box([-0.13, 0.92, -0.03], [0.13, 1, 0.13], 0.8);
  return soup.build();
}

/** For a track whose class is `vehicle` or `unknown`: nothing about its shape is claimed. */
function plain(): BufferGeometry {
  const soup = new Soup();
  soup.loft(
    [
      { z: -0.5, ring: ring(0.44, 0.1, 0.46, 0.5, 0.4, 0.86) },
      { z: -0.44, ring: ring(0.5, 0.1, 0.5, 0.55, 0.45, 1) },
      { z: 0.44, ring: ring(0.5, 0.1, 0.5, 0.55, 0.45, 1) },
      { z: 0.5, ring: ring(0.44, 0.1, 0.46, 0.5, 0.4, 0.86) },
    ],
    (band) => (band === 5 ? SKIRT : BODY),
    [0, 0.5, 0],
  );
  return soup.build();
}

/** In the order of FORMS in model.ts. */
export function buildForms(): BufferGeometry[] {
  return [car(), truck(), bus(), motorcycle(), plain()];
}
