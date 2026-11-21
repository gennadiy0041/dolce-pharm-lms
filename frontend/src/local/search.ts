// Поиск по библиотеке на устройстве — тот же алгоритм, что на сервере (backend/app/services/libsearch.py):
// слова запроса в любом порядке, без учёта регистра и «ё», с отбрасыванием окончаний у длинных слов,
// код СОП и слитно, и через дефисы; ищется и в тексте самого документа — поэтому работает без сети.

const WORD = /[0-9a-zа-я]+/g
const CODE = /[0-9a-zа-я]+(?:\s*[-‐–—/.]\s*[0-9a-zа-я]+)+/g
const FIELDS: [keyof SearchFields, number][] = [['title', 4], ['sop_code', 4], ['category', 2], ['description', 1.5], ['text', 1]]

export interface SearchFields { title: string; sop_code: string; category: string; description: string; text?: string }

export function norm(s: string | undefined | null): string {
  return (s ?? '').toLocaleLowerCase('ru').replace(/ё/g, 'е')
}

export function words(s: string | undefined | null): string[] {
  return norm(s).match(WORD) ?? []
}

function stem(t: string): string {
  return /^[a-zа-я]+$/.test(t) && t.length >= 5 ? t.slice(0, Math.max(4, t.length - 2)) : t
}

/** Разобранный запрос: коды через дефис («СОП-ОК-015», «ок-015») и отдельные слова. */
export interface Query { codes: string[]; terms: string[]; hints: string[] }

export function parseQuery(q: string): Query {
  const codes: string[] = []
  const firstWords: string[] = []
  const rest = norm(q).replace(CODE, (group) => {
    if (!/\d/.test(group)) return group
    codes.push(words(group).join(''))
    firstWords.push(words(group)[0])
    return ' '
  })
  const terms = words(rest).map(stem)
  return { codes, terms, hints: [...terms, ...firstWords] }
}

const isEmpty = (q: Query) => !q.codes.length && !q.terms.length

/** Вес совпадения или null, если хотя бы одно слово или код запроса не найден.
 *  Код через дефис сравнивается слитно с каждым полем; из отдельных слов короткие (1–2 буквы) ищутся
 *  только с начала слова, числа — только целиком: иначе «х» или «1» находились бы в любом тексте. */
export function score(fields: SearchFields, query: Query): number | null {
  if (isEmpty(query)) return 0
  const hay = Object.fromEntries(FIELDS.map(([k]) => [k, ' ' + words(fields[k]).join(' ') + ' '])) as Record<string, string>
  const solid = Object.fromEntries(FIELDS.map(([k]) => [k, words(fields[k]).join('')])) as Record<string, string>
  let total = 0
  for (const code of query.codes) {
    let best = 0
    for (const [k, w] of FIELDS) if (solid[k].includes(code)) best = Math.max(best, w)
    if (!best) return null
    total += best * 2
  }
  const terms = query.terms
  const joined = terms.join('')
  let whole = 0
  if (terms.length > 1 && /\d/.test(joined)) {
    for (const [k, w] of FIELDS) if (solid[k].includes(joined)) whole = Math.max(whole, w * terms.length)
  }
  let wordsTotal = 0
  for (const t of terms) {
    let best = 0
    const digits = /^\d+$/.test(t)
    const strict = digits || t.length <= 2
    for (const [key, weight] of FIELDS) {
      const h = hay[key]
      if (digits ? h.includes(' ' + t + ' ') : h.includes(' ' + t)) best = Math.max(best, weight)
      else if (!strict && (h.includes(t) || ((key === 'title' || key === 'sop_code') && solid[key].includes(t)))) best = Math.max(best, weight * 0.6)
    }
    if (!best) {
      if (!whole) return null
      wordsTotal = whole
      break
    }
    wordsTotal += best
  }
  return total + Math.max(wordsTotal, whole)
}

export interface Snippet { before: string; match: string; after: string }

/** Отрывок текста документа вокруг первого найденного слова запроса. */
export function snippet(text: string | undefined, query: Query, radius = 70): Snippet | null {
  if (!text) return null
  const low = norm(text)
  for (const t of query.hints) {
    const i = low.indexOf(t)
    if (i < 0) continue
    const start = Math.max(0, i - radius)
    const end = Math.min(text.length, i + t.length + radius)
    return {
      before: (start ? '…' : '') + text.slice(start, i).trimStart(),
      match: text.slice(i, i + t.length),
      after: text.slice(i + t.length, end).trimEnd() + (end < text.length ? '…' : ''),
    }
  }
  return null
}

/** Найденные материалы: сначала совпадения в названии и коде, затем в описании и тексте. */
export function searchItems<T extends SearchFields>(items: T[], q: string): { item: T; snippet: Snippet | null }[] {
  const query = parseQuery(q)
  if (isEmpty(query)) return items.map((item) => ({ item, snippet: null }))
  const found: { item: T; weight: number; snippet: Snippet | null; index: number }[] = []
  items.forEach((item, index) => {
    const weight = score(item, query)
    if (weight === null) return
    // отрывок показываем, только если слов нет ни в названии, ни в описании — иначе он лишний
    const visible = score({ title: item.title, sop_code: item.sop_code, category: item.category, description: item.description }, query)
    found.push({ item, weight, snippet: visible === null ? snippet(item.text, query) : null, index })
  })
  found.sort((a, b) => b.weight - a.weight || a.index - b.index)
  return found.map(({ item, snippet: s }) => ({ item, snippet: s }))
}
