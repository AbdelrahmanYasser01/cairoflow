/**
 * DOM drawn over the canvas and placed from the scene each frame. Plain nodes, not React roots:
 * drei's Html mounts a root per label, and tearing those down inside a React commit logs an error
 * for each one on every analysis switch.
 */
import type { Camera, Vector3, WebGLRenderer } from "three";

/** Instruments placed over the stage carry this, so labels can stand aside for them. */
export const CHROME_ATTRIBUTE = "data-twin-chrome";

export function overlayNode<K extends keyof HTMLElementTagNameMap>(
  renderer: WebGLRenderer,
  tag: K,
  className: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  node.className = className;
  renderer.domElement.parentElement?.appendChild(node);
  return node;
}

/** Writes pixels from the canvas's top left into x and y, and leaves z in clip depth. */
export function toCanvasPixels(
  point: Vector3,
  camera: Camera,
  size: { width: number; height: number },
): Vector3 {
  point.project(camera);
  point.x = (point.x * 0.5 + 0.5) * size.width;
  point.y = (0.5 - point.y * 0.5) * size.height;
  return point;
}
