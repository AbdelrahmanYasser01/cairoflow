import { useEffect, useMemo } from "react";
import {
  DataTexture,
  LinearFilter,
  RedFormat,
  ShaderMaterial,
  UnsignedByteType,
  Vector2,
  Vector4,
  type Texture,
} from "three";
import type { Coverage, TwinModel } from "../model";
import { ORDER, type Palette } from "./runtime";

const VERTEX = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

/**
 * The floor: dark ground, a metric grid, the surface vehicles actually crossed, and a haze where
 * the measured area ends. Haze and fade are depth cues only and carry no data.
 */
const FRAGMENT = /* glsl */ `
  varying vec3 vWorld;
  uniform sampler2D uCover;
  uniform vec4 uBox;
  uniform vec3 uRoom;
  uniform vec3 uSurface;
  uniform vec3 uMinor;
  uniform vec3 uMajor;
  uniform vec2 uCentre;
  uniform vec2 uRadius;
  uniform float uGrid;

  float gridLine(vec2 p, float spacing, float widthPx) {
    vec2 distanceToLine = abs(fract(p / spacing - 0.5) - 0.5) * spacing;
    vec2 pixel = fwidth(p);
    vec2 line = 1.0 - smoothstep(vec2(0.0), pixel * widthPx, distanceToLine);
    float fade = clamp(1.0 - max(pixel.x, pixel.y) / spacing * 3.0, 0.0, 1.0);
    return max(line.x, line.y) * fade;
  }

  void main() {
    vec2 p = vec2(vWorld.x, -vWorld.z);
    vec2 uv = (p - uBox.xy) / uBox.zw;
    float inside = step(0.0, uv.x) * step(uv.x, 1.0) * step(0.0, uv.y) * step(uv.y, 1.0);
    float cover = texture2D(uCover, uv).r * inside;

    vec3 colour = mix(uRoom, uSurface, smoothstep(0.08, 0.6, cover));
    float minor = gridLine(p, 10.0, 1.1);
    float major = gridLine(p, 50.0, 1.4);
    colour = mix(colour, uMinor, minor * 0.34 * uGrid);
    colour = mix(colour, uMajor, major * 0.3 * uGrid);

    float reach = length((p - uCentre) / uRadius);
    float haze = smoothstep(0.62, 0.98, reach) * (1.0 - smoothstep(0.98, 1.42, reach));
    colour += uMinor * haze * 0.16;
    colour = mix(colour, uRoom, smoothstep(0.86, 1.32, reach));

    gl_FragColor = vec4(colour, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

function coverageTexture(coverage: Coverage | null): Texture {
  const texture = coverage
    ? new DataTexture(coverage.data, coverage.cols, coverage.rows, RedFormat, UnsignedByteType)
    : new DataTexture(new Uint8Array(1), 1, 1, RedFormat, UnsignedByteType);
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.needsUpdate = true;
  return texture;
}

export function Ground({
  model,
  palette,
  showSurface,
  gridStrength,
}: {
  model: TwinModel;
  palette: Palette;
  showSurface: boolean;
  gridStrength: number;
}) {
  const material = useMemo(() => {
    const coverage = model.coverage;
    const { bounds } = model;
    return new ShaderMaterial({
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      depthWrite: false,
      uniforms: {
        uCover: { value: coverageTexture(coverage) },
        uBox: {
          value: coverage
            ? new Vector4(
                coverage.uMin,
                coverage.vMin,
                coverage.cols * coverage.cellM,
                coverage.rows * coverage.cellM,
              )
            : new Vector4(0, 0, 1, 1),
        },
        uRoom: { value: palette.room },
        uSurface: { value: palette.surface },
        uMinor: { value: palette.ruleStrong },
        uMajor: { value: palette.paintFaint },
        uCentre: {
          value: new Vector2((bounds.uMin + bounds.uMax) / 2, (bounds.vMin + bounds.vMax) / 2),
        },
        uRadius: {
          value: new Vector2(
            (bounds.uMax - bounds.uMin) / 2 + 70,
            (bounds.vMax - bounds.vMin) / 2 + 70,
          ),
        },
        uGrid: { value: gridStrength },
      },
    });
    // gridStrength and showSurface update the uniforms below without rebuilding the material.
  }, [model, palette]);

  useEffect(() => {
    material.uniforms.uGrid.value = gridStrength;
    material.uniforms.uSurface.value = showSurface ? palette.surface : palette.room;
  }, [material, gridStrength, showSurface, palette]);

  useEffect(
    () => () => {
      (material.uniforms.uCover.value as Texture).dispose();
      material.dispose();
    },
    [material],
  );

  return (
    <mesh rotation-x={-Math.PI / 2} material={material} renderOrder={ORDER.ground}>
      <planeGeometry args={[1600, 1600]} />
    </mesh>
  );
}
