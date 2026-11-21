// Ответ на вопрос теста — текстом: для карточки попытки у методиста и для разбора у сотрудника.

export interface AnswerItem {
  question_id: string; type: string; text: string; options: { id: string; text: string }[]; max_points: number
  answer: string | string[] | null; is_correct: boolean | null; points: number | null; comment: string
  correct?: string | string[] | null; criteria?: string
}

export function answerText(item: AnswerItem, value: string | string[] | null | undefined): string {
  const byId = new Map(item.options.map((o) => [o.id, o.text]))
  if (value === null || value === undefined || value === '') return '— нет ответа —'
  if (Array.isArray(value)) {
    if (!value.length) return '— нет ответа —'
    return value.map((id, i) => (item.type === 'order' ? `${i + 1}. ` : '') + (byId.get(id) ?? id)).join(item.type === 'order' ? ' → ' : '; ')
  }
  return item.type === 'open' ? value : byId.get(value) ?? value
}
