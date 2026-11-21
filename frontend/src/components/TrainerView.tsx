// Диалоговый тренажёр: карточка собеседника на фоне цеха, эмоция меняется по ходу разговора,
// реплики сотрудника ведут к разным развязкам. Переходы заданы в сценарии, внешний ИИ не нужен.
import { useState } from 'react'
import { CircleAlert, CircleCheck, RotateCcw, TriangleAlert } from 'lucide-react'
import type { Scenario } from '../types'
import { Pill } from '../ui/kit'

export interface TrainerOutcome {
  endingId: string
  title: string
  outcome: string
  path: { node: string; choice: string; text: string }[]
}

const OUTCOME = {
  good: { label: 'Хороший исход', tone: 'ok' as const, Icon: CircleCheck },
  mixed: { label: 'Исход с потерями', tone: 'warn' as const, Icon: TriangleAlert },
  bad: { label: 'Плохой исход', tone: 'danger' as const, Icon: CircleAlert },
}

export function TrainerView({ scenario, onFinish }: { scenario: Scenario; onFinish?: (r: TrainerOutcome) => void }) {
  const [started, setStarted] = useState(false)
  const [nodeId, setNodeId] = useState(scenario.start)
  const [path, setPath] = useState<TrainerOutcome['path']>([])
  const node = scenario.nodes[nodeId]
  const emotion = node?.emotion ?? 'neutral'
  const image = scenario.character.images[emotion] ?? scenario.character.images.neutral
  const emotionLabel = scenario.emotion_labels?.[emotion]

  const restart = () => {
    setStarted(true)
    setNodeId(scenario.start)
    setPath([])
  }

  const choose = (choiceId: string, text: string, next: string) => {
    const nextPath = [...path, { node: nodeId, choice: choiceId, text }]
    setPath(nextPath)
    setNodeId(next)
    const target = scenario.nodes[next]
    if (target?.ending) {
      onFinish?.({ endingId: next, title: target.title ?? '', outcome: target.outcome ?? '', path: nextPath })
    }
  }

  if (!node) return null
  const out = node.ending ? OUTCOME[node.outcome ?? 'mixed'] : null

  return (
    <div className="stack">
      <div className="scene" style={{ backgroundImage: `url(${scenario.background})` }}>
        <img className="scene-char" src={image} alt={`${scenario.character.name}, ${emotionLabel ?? ''}`} />
        <div className="scene-tag">
          <b>{scenario.character.name}</b>
          {scenario.character.role}{emotionLabel ? ` · ${emotionLabel}` : ''}
        </div>
      </div>

      {!started ? (
        <>
          <div className="bubble narrator">{scenario.intro}</div>
          <button className="btn btn-primary btn-lg btn-block" onClick={restart}>Начать разговор</button>
        </>
      ) : node.ending && out ? (
        <div className={`card ending ${node.outcome ?? 'mixed'} stack-sm`} aria-live="polite">
          <Pill tone={out.tone} icon={<out.Icon aria-hidden />}>{out.label}</Pill>
          <h3>{node.title}</h3>
          <p>{node.text}</p>
          <div>
            <p className="label">К чему это привело</p>
            <ul style={{ margin: '0.3rem 0 0', paddingLeft: '1.3rem' }}>
              {(node.consequences ?? []).map((c, i) => <li key={i} style={{ margin: '0.25rem 0' }}>{c}</li>)}
            </ul>
          </div>
          {node.rule && <p className="quote"><b>Правило:</b> {node.rule}</p>}
          <p className="muted small">Ваш путь: {path.map((p) => `«${p.text}»`).join(' → ')}</p>
          <button className="btn btn-secondary btn-block" onClick={restart}><RotateCcw aria-hidden /> Пройти ещё раз и выбрать иначе</button>
        </div>
      ) : (
        <>
          <div className={`bubble ${node.speaker === 'character' ? 'character' : 'narrator'}`} aria-live="polite">{node.text}</div>
          <div className="stack-sm" role="group" aria-label="Ваш ответ">
            <p className="label">Ваш ответ:</p>
            {(node.choices ?? []).map((c, i) => (
              <button key={c.id} className="choice" onClick={() => choose(c.id, c.text, c.next)}>
                <span className="n" aria-hidden>{i + 1}</span>
                <span>{c.text}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
