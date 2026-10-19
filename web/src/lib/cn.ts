import { clsx, type ClassValue } from "clsx";

/** Join class names. Tailwind classes are written so later ones never need to override earlier ones. */
export function cn(...inputs: ClassValue[]) {
  return clsx(inputs);
}
