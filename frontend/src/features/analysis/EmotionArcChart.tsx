import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import type { EmotionArc, Scene } from '@/api/contracts'
import { Button } from '@/components/ui/button'
import { buildEmotionArcChartData, type ArcChartDatum } from './emotionArcChartData'

const characterColors = ['#D55E00', '#009E73', '#CC79A7', '#B8860B', '#56B4E9', '#332288', '#44AA99', '#AA4499']
const valenceColor = '#0e639c'
const tensionColor = '#7c3aed'
const VALENCE_DISPLAY_DOMAIN: [number, number] = [-3.5, 3.5]

interface EmotionArcChartProps {
  arc: EmotionArc
  scenes: Scene[]
  activeSceneNumber?: number
  onSceneSelect?: (sceneNumber: number) => void
  turningPointSceneNumbers?: number[]
}

function pointNumberForScene(arc: EmotionArc, sceneNumber: number): number | undefined {
  return arc.scene_mapping.find((mapping) => (
    mapping.start_scene_number <= sceneNumber && sceneNumber <= mapping.end_scene_number
  ))?.point_number
}

export function EmotionArcChart({ arc, scenes, activeSceneNumber, onSceneSelect, turningPointSceneNumbers = [] }: EmotionArcChartProps) {
  const { t } = useTranslation('analysis')
  const [visibleCharacters, setVisibleCharacters] = useState(() => new Set(Object.keys(arc.characters)))
  const [valenceVisible, setValenceVisible] = useState(true)
  const [tensionVisible, setTensionVisible] = useState(true)
  const [chartReady, setChartReady] = useState(false)
  useEffect(() => {
    const frame = requestAnimationFrame(() => setChartReady(true))
    return () => cancelAnimationFrame(frame)
  }, [])
  const data = useMemo(() => buildEmotionArcChartData(arc, scenes), [arc, scenes])
  const activePointNumber = useMemo(
    () => activeSceneNumber === undefined ? undefined : pointNumberForScene(arc, activeSceneNumber),
    [activeSceneNumber, arc],
  )
  const turningPointNumbers = useMemo(
    () => turningPointSceneNumbers.flatMap((sceneNumber) => {
      const pointNumber = pointNumberForScene(arc, sceneNumber)
      return pointNumber === undefined ? [] : [pointNumber]
    }),
    [arc, turningPointSceneNumbers],
  )
  const characterEntries = Object.keys(arc.characters)

  if (data.length === 0) return <p className="text-sm text-muted-foreground">{t('empty.arc')}</p>

  return (
    <section aria-label="Emotional Arc">
      <div className="mb-3 flex flex-wrap gap-1.5">
        <LegendButton active={valenceVisible} color={valenceColor} label="Valence" onClick={() => setValenceVisible((visible) => !visible)} />
        <LegendButton active={tensionVisible} color={tensionColor} label="Tension" onClick={() => setTensionVisible((visible) => !visible)} />
        {characterEntries.map((name, index) => <LegendButton active={visibleCharacters.has(name)} color={characterColors[index % characterColors.length]} key={name} label={name} onClick={() => setVisibleCharacters((current) => {
          const next = new Set(current)
          if (next.has(name)) next.delete(name)
          else next.add(name)
          return next
        })} />)}
      </div>
      <div className="h-48 min-h-48">
        {chartReady &&
          <ResponsiveContainer height="100%" minHeight={192} minWidth={0} width="100%">
            <LineChart data={data} margin={{ bottom: 2, left: -18, right: 8, top: 4 }} onClick={(state) => {
              const index = state.activeTooltipIndex
              if (typeof index === 'number') onSceneSelect?.(data[index]?.representativeSceneNumber)
            }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="pointNumber" fontSize={11} stroke="var(--muted-foreground)" tickLine={false} />
              <YAxis domain={VALENCE_DISPLAY_DOMAIN} fontSize={11} stroke="var(--muted-foreground)" tickLine={false} width={28} />
              <ReferenceLine stroke="var(--muted-foreground)" strokeDasharray="2 4" y={0} />
              <Tooltip content={<ArcTooltip />} cursor={{ stroke: 'var(--primary)', strokeDasharray: '4 4' }} />
              {activePointNumber !== undefined && <ReferenceLine stroke="var(--primary)" strokeDasharray="4 4" x={activePointNumber} />}
              {turningPointNumbers.map((pointNumber, index) => <ReferenceLine key={`${pointNumber}-${index}`} stroke="var(--muted-foreground)" strokeDasharray="1 3" x={pointNumber} />)}
              {valenceVisible && <Line activeDot={{ r: 5 }} connectNulls={false} dataKey="valence" dot={false} name="Valence" stroke={valenceColor} strokeWidth={2.5} type="monotone" />}
              {tensionVisible && <Line activeDot={{ r: 3 }} connectNulls={false} dataKey="tension" dot={false} name="Tension" stroke={tensionColor} strokeWidth={2} type="monotone" />}
              {characterEntries.filter((name) => visibleCharacters.has(name)).map((name) => <Line activeDot={{ r: 5 }} connectNulls={false} dataKey={name} dot={{ r: 3 }} key={name} name={name} stroke={characterColors[characterEntries.indexOf(name) % characterColors.length]} strokeWidth={1.5} type="monotone" />)}
            </LineChart>
          </ResponsiveContainer>
        }
      </div>
    </section>
  )
}

function LegendButton({ active, color, label, onClick }: { active: boolean; color: string; label: string; onClick: () => void }) {
  return <Button aria-pressed={active} className="h-7 gap-1.5 px-2 text-xs" onClick={onClick} size="sm" variant={active ? 'secondary' : 'ghost'}><span className="size-2 rounded-full" style={{ backgroundColor: color }} />{label}</Button>
}

function ArcTooltip({ active, payload }: { active?: boolean; payload?: Array<{ dataKey?: string; name?: string; value?: number | null; payload?: ArcChartDatum }> }) {
  const { t } = useTranslation('analysis')
  if (!active || !payload?.length) return null
  const scene = payload[0].payload
  const sceneRange = scene?.startSceneNumber === scene?.endSceneNumber
    ? String(scene?.startSceneNumber)
    : `${scene?.startSceneNumber}–${scene?.endSceneNumber}`
  return <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md"><p className="mb-1 font-medium">{t('chart.tooltip', { range: sceneRange, representative: scene?.representativeSceneNumber ?? '', title: scene?.sceneTitle ?? '' })}</p>{payload.map((item) => item.value !== null && item.value !== undefined && <p key={item.dataKey}>{item.name}: {item.value > 0 ? '+' : ''}{item.value.toFixed(1)}</p>)}</div>
}
