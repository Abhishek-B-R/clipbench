import { useSyncExternalStore } from "react";

/**
 * Open state of the Cmd+K menu, kept apart from the menu itself so the shell and any button can open it
 * without loading cmdk. The menu chunk loads on the first open.
 */
let open = false;
let wanted = false;
const subs = new Set<() => void>();
const emit = () => subs.forEach((s) => s());

export function setCommandMenuOpen(v: boolean) {
  open = v;
  if (v) wanted = true;
  emit();
}
export const isCommandMenuOpen = () => open;

const subscribe = (cb: () => void) => {
  subs.add(cb);
  return () => subs.delete(cb);
};
export const useCommandMenuOpen = () => useSyncExternalStore(subscribe, () => open, () => false);
/** True once the menu has been asked for, so its chunk is loaded only then. */
export const useCommandMenuWanted = () => useSyncExternalStore(subscribe, () => wanted, () => false);
