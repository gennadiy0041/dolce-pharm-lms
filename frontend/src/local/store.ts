// Крошечное хранилище состояния для React (useSyncExternalStore) — без сторонних библиотек.
import { useSyncExternalStore } from 'react'

export interface Store<T> {
  get(): T
  set(patch: Partial<T> | ((prev: T) => Partial<T>)): void
  subscribe(fn: () => void): () => void
}

export function createStore<T extends object>(initial: T): Store<T> {
  let state = initial
  const listeners = new Set<() => void>()
  return {
    get: () => state,
    set(patch) {
      const p = typeof patch === 'function' ? patch(state) : patch
      state = { ...state, ...p }
      listeners.forEach((fn) => fn())
    },
    subscribe(fn) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
  }
}

export function useStore<T extends object>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get)
}
