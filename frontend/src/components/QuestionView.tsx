// Четыре типа вопросов. Управление — касанием, мышью и с клавиатуры.
import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Check } from 'lucide-react'
import type { AnswerValue, ManifestQuestion } from '../types'

export const QUESTION_LABEL: Record<string, string> = {
  single: 'Один вариант', multiple: 'Несколько вариантов', order: 'Последовательность', open: 'Открытый ответ',
}
export const QUESTION_HINT: Record<string, string> = {
  single: 'Выберите один вариант.',
  multiple: 'Отметьте все верные варианты.',
  order: 'Расставьте по порядку кнопками «Выше» и «Ниже». С клавиатуры: выберите строку и нажимайте стрелки вверх и вниз.',
  open: 'Напишите ответ своими словами. Его проверит методист.',
}

export function isAnswered(q: ManifestQuestion, value: AnswerValue | undefined): boolean {
  if (q.type === 'single') return typeof value === 'string' && value !== ''
  if (q.type === 'multiple') return Array.isArray(value) && value.length > 0
  if (q.type === 'order') return Array.isArray(value) && value.length === q.options.length
  return typeof value === 'string' && value.trim().length > 0
}

export function QuestionView({ q, value, onChange, readOnly }: {
  q: ManifestQuestion; value: AnswerValue | undefined; onChange: (v: AnswerValue) => void; readOnly?: boolean
}) {
  if (q.type === 'single') {
    return (
      <div role="radiogroup" aria-label={q.text}>
        {q.options.map((o) => {
          const selected = value === o.id
          return (
            <button key={o.id} type="button" role="radio" aria-checked={selected} disabled={readOnly}
              className={`opt ${selected ? 'selected' : ''}`} onClick={() => onChange(o.id)}>
              <span className="mark radio" aria-hidden>{selected && <Check />}</span>
              <span>{o.text}</span>
            </button>
          )
        })}
      </div>
    )
  }
  if (q.type === 'multiple') {
    const set = new Set(Array.isArray(value) ? value : [])
    return (
      <div role="group" aria-label={q.text}>
        {q.options.map((o) => {
          const selected = set.has(o.id)
          return (
            <button key={o.id} type="button" role="checkbox" aria-checked={selected} disabled={readOnly}
              className={`opt ${selected ? 'selected' : ''}`}
              onClick={() => {
                const next = new Set(set)
                if (selected) next.delete(o.id)
                else next.add(o.id)
                onChange(q.options.map((x) => x.id).filter((id) => next.has(id)))
              }}>
              <span className="mark box" aria-hidden>{selected && <Check />}</span>
              <span>{o.text}</span>
            </button>
          )
        })}
      </div>
    )
  }
  if (q.type === 'order') return <OrderQuestion q={q} value={value} onChange={onChange} readOnly={readOnly} />
  const text = typeof value === 'string' ? value : ''
  return (
    <div className="field">
      <label htmlFor={`open-${q.id}`} className="sr-only">Ваш ответ</label>
      <textarea id={`open-${q.id}`} className="textarea" rows={5} value={text} maxLength={5000} readOnly={readOnly}
        placeholder="Ваш ответ" onChange={(e) => onChange(e.target.value)} />
      <span className="hint">{text.trim().length ? `Символов: ${text.length}` : 'Ответ сохраняется по мере ввода.'}</span>
    </div>
  )
}

function OrderQuestion({ q, value, onChange, readOnly }: {
  q: ManifestQuestion; value: AnswerValue | undefined; onChange: (v: AnswerValue) => void; readOnly?: boolean
}) {
  const ids = q.options.map((o) => o.id)
  const valid = Array.isArray(value) && value.length === ids.length && value.every((id) => ids.includes(id))
  const order = valid ? (value as string[]) : ids
  const byId = new Map(q.options.map((o) => [o.id, o.text]))
  const refs = useRef<Record<string, HTMLDivElement | null>>({})
  const [said, setSaid] = useState('')
  const focusId = useRef<string | null>(null)

  // показанный порядок сразу считается ответом: сотрудник видит ровно то, что будет отправлено
  useEffect(() => {
    if (!valid && !readOnly) onChange(ids)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.id])

  useEffect(() => {
    if (focusId.current) {
      refs.current[focusId.current]?.focus()
      focusId.current = null
    }
  })

  const move = (index: number, delta: number, keepFocus: boolean) => {
    const target = index + delta
    if (readOnly || target < 0 || target >= order.length) return
    const next = [...order]
    ;[next[index], next[target]] = [next[target], next[index]]
    if (keepFocus) focusId.current = order[index]
    setSaid(`«${byId.get(order[index])}» теперь на месте ${target + 1} из ${order.length}`)
    onChange(next)
  }

  return (
    <div>
      <div role="list" aria-label={q.text}>
        {order.map((id, i) => (
          <div key={id} role="listitem" className="order-item" tabIndex={0}
            ref={(el) => { refs.current[id] = el }}
            aria-label={`Место ${i + 1}: ${byId.get(id)}. Стрелки вверх и вниз меняют место.`}
            onKeyDown={(e) => {
              if (e.target !== e.currentTarget) return
              if (e.key === 'ArrowUp') { e.preventDefault(); move(i, -1, true) }
              if (e.key === 'ArrowDown') { e.preventDefault(); move(i, 1, true) }
            }}>
            <span className="order-num" aria-hidden>{i + 1}</span>
            <span className="grow">{byId.get(id)}</span>
            {!readOnly && (
              <span className="order-btns">
                <button type="button" className="btn btn-secondary" disabled={i === 0} onClick={() => move(i, -1, false)}
                  aria-label={`Выше: ${byId.get(id)}`}><ArrowUp aria-hidden /> Выше</button>
                <button type="button" className="btn btn-secondary" disabled={i === order.length - 1} onClick={() => move(i, 1, false)}
                  aria-label={`Ниже: ${byId.get(id)}`}><ArrowDown aria-hidden /> Ниже</button>
              </span>
            )}
          </div>
        ))}
      </div>
      <span className="sr-only" aria-live="polite">{said}</span>
    </div>
  )
}
