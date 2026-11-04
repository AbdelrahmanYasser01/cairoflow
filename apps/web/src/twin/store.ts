import { useSyncExternalStore } from "react";

/** A value with listeners. The twin keeps per-frame work out of React; this is the bridge back. */
export interface Store<T> {
  get(): T;
  set(next: T): void;
  update(change: (current: T) => T): void;
  subscribe(listener: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  const set = (next: T): void => {
    if (Object.is(next, state)) return;
    state = next;
    for (const listener of listeners) listener();
  };
  return {
    get: () => state,
    set,
    update: (change) => set(change(state)),
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}
