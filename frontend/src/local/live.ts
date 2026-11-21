// useLiveQuery, который не трогает базу, пока страница в фоне.
//
// Живой запрос Dexie перечитывает базу при каждом её изменении — в том числе изменении, сделанном
// другой копией платформы. Страница в фоне может быть заморожена Android посреди такого чтения и
// удерживать хранилище (см. lifecycle.ts). Поэтому в фоне хук отдаёт последнее прочитанное значение
// и к базе не обращается; на экране — обычный живой запрос, который сразу перечитывает данные.
import { useRef } from 'react'
import { useLiveQuery as useDexieLiveQuery } from 'dexie-react-hooks'
import { useActive } from './lifecycle'

export function useLiveQuery<T>(querier: () => Promise<T> | T, deps?: unknown[]): T | undefined
export function useLiveQuery<T, TDefault>(querier: () => Promise<T> | T, deps: unknown[], defaultResult: TDefault): T | TDefault
export function useLiveQuery<T, TDefault>(querier: () => Promise<T> | T, deps: unknown[] = [], defaultResult?: TDefault): T | TDefault | undefined {
  const active = useActive()
  const last = useRef<T | TDefault | undefined>(defaultResult)
  const value = useDexieLiveQuery<T | TDefault | undefined, TDefault | undefined>(
    active ? querier : () => last.current,
    [...deps, active],
    defaultResult,
  )
  if (active) last.current = value
  return value
}
