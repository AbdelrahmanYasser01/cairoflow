import { useThree } from "@react-three/fiber";
import { useEffect } from "react";
import {
  Color,
  DoubleSide,
  Mesh,
  MeshBasicMaterial,
  PlaneGeometry,
  PMREMGenerator,
  Scene,
} from "three";

/** Colour, brightness, position, size. Long soft panels: a night sky and a low warm horizon. */
const PANELS = [
  { colour: "#a9bccf", level: 1.3, at: [0, 40, 0], size: [160, 160] },
  { colour: "#f2d3a0", level: 0.9, at: [70, 10, 40], size: [80, 10] },
  { colour: "#7f93a8", level: 0.5, at: [-70, 12, -30], size: [80, 14] },
] as const;

/**
 * Something for vehicle bodies to reflect, generated here at start-up. No image is loaded, so the
 * app needs no HDRI and no network; drei's Environment is avoided because it ships a CDN table.
 */
export function Reflections() {
  const renderer = useThree((state) => state.gl);
  const scene = useThree((state) => state.scene);

  useEffect(() => {
    const room = new Scene();
    room.background = new Color("#0b0e12");
    const geometry = new PlaneGeometry(1, 1);
    const materials = PANELS.map(({ colour, level, at, size }) => {
      const material = new MeshBasicMaterial({
        color: new Color(colour).multiplyScalar(level),
        side: DoubleSide,
      });
      const panel = new Mesh(geometry, material);
      panel.position.set(at[0], at[1], at[2]);
      panel.scale.set(size[0], size[1], 1);
      panel.lookAt(0, 0, 0);
      room.add(panel);
      return material;
    });
    const generator = new PMREMGenerator(renderer);
    const target = generator.fromScene(room, 0.04, 0.1, 400);
    scene.environment = target.texture;
    return () => {
      scene.environment = null;
      target.dispose();
      generator.dispose();
      geometry.dispose();
      for (const material of materials) material.dispose();
    };
  }, [renderer, scene]);

  return null;
}
