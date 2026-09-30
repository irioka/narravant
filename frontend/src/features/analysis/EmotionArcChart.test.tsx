import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const lineProps: Array<Record<string, unknown>> = []
const lineChartProps: Array<Record<string, unknown>> = []
const referenceLineProps: Array<Record<string, unknown>> = []

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  const Container = ({ children }: { children?: React.ReactNode }) => <>{children}</>
  return {
    ...actual,
    CartesianGrid: () => null,
    Line: (props: Record<string, unknown>) => { lineProps.push(props); return null },
    LineChart: (props: Record<string, unknown>) => { lineChartProps.push(props); return <>{props.children as React.ReactNode}</> },
    ReferenceLine: (props: Record<string, unknown>) => { referenceLineProps.push(props); return null },
    ResponsiveContainer: Container,
    Tooltip: () => null,
    XAxis: () => null,
    YAxis: () => null,
  }
})

import { EmotionArcChart } from './EmotionArcChart'
import { buildEmotionArcChartData } from './emotionArcChartData'
import { toDisplayValence } from './valence'

describe('EmotionArcChart', () => {
  it('ValenceとTension、キャラクター線の凡例を表示する', () => {
    render(<EmotionArcChart arc={{ characters: { '真理': [0, 7, 4] }, valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1], scene_mapping: oneToOneMapping(3), valence: [1, 7, 4], tension: [-3, 3, 0] }} scenes={[]} />)

    expect(screen.getByRole('region', { name: 'Emotional Arc' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Valence' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tension' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '真理' })).toBeInTheDocument()
  })

  it('Valence 1-7を-3〜+3へ写像し、非登場シーン(0)はnullにしてラインを分断する', () => {
    expect(toDisplayValence(1)).toBe(-3)
    expect(toDisplayValence(4)).toBe(0)
    expect(toDisplayValence(7)).toBe(3)
    expect(toDisplayValence(0)).toBeNull()
    expect(toDisplayValence(null)).toBeNull()
  })

  it('孤立したキャラクター値にもdotを設定する', () => {
    const animationFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => { callback(0); return 1 })
    lineProps.length = 0
    render(<EmotionArcChart arc={{ characters: { 孤立: [0, 6, 0] }, valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1], scene_mapping: oneToOneMapping(3), valence: [4, 4, 4], tension: [0, 0, 0] }} scenes={[{ scene_number: 1, heading: 'INT. A', text: '', dialogues: [] }, { scene_number: 2, heading: 'INT. B', text: '', dialogues: [] }, { scene_number: 3, heading: 'INT. C', text: '', dialogues: [] }]} />)
    expect(lineProps.find((props) => props.dataKey === '孤立')).toMatchObject({ activeDot: { r: 5 }, connectNulls: false, dot: { r: 3 } })
    animationFrame.mockRestore()
  })

  it('100 sceneの最後のpointを代表元sceneへ選択し、active sceneとTPを所属pointへ表示する', () => {
    const animationFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => { callback(0); return 1 })
    const onSceneSelect = vi.fn()
    lineChartProps.length = 0
    referenceLineProps.length = 0
    const mapping = oneToOneMapping(36)
    mapping[17] = { point_number: 18, start_scene_number: 48, end_scene_number: 50, representative_scene_number: 49 }
    mapping[35] = { point_number: 36, start_scene_number: 98, end_scene_number: 100, representative_scene_number: 99 }
    const scenes = Array.from({ length: 100 }, (_, index) => ({ scene_number: index + 1, heading: `INT. ${index + 1}`, text: '', dialogues: [] }))
    const arc = { characters: {}, valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1], scene_mapping: mapping, valence: Array(36).fill(4), tension: Array(36).fill(0) }

    const data = buildEmotionArcChartData(arc, scenes)
    expect(data[35]).toMatchObject({ endSceneNumber: 100, representativeSceneNumber: 99, sceneTitle: 'INT. 99' })

    render(<EmotionArcChart activeSceneNumber={50} arc={arc} onSceneSelect={onSceneSelect} scenes={scenes} turningPointSceneNumbers={[100]} />)
    const onClick = lineChartProps[0]?.onClick as ((state: { activeTooltipIndex: number }) => void)
    onClick({ activeTooltipIndex: 35 })

    expect(onSceneSelect).toHaveBeenCalledWith(99)
    expect(referenceLineProps.some((props) => props.x === 18)).toBe(true)
    expect(referenceLineProps.some((props) => props.x === 36)).toBe(true)
    animationFrame.mockRestore()
  })
})

function oneToOneMapping(count: number) {
  return Array.from({ length: count }, (_, index) => ({
    point_number: index + 1,
    start_scene_number: index + 1,
    end_scene_number: index + 1,
    representative_scene_number: index + 1,
  }))
}
