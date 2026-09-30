import type { EmotionArc, Scene } from '@/api/contracts'
import { toDisplayValence } from './valence'

export type ArcChartDatum = {
  endSceneNumber: number
  pointNumber: number
  representativeSceneNumber: number
  sceneTitle: string
  startSceneNumber: number
  tension: number | null
  valence: number | null
} & Record<string, number | null | string>

export function buildEmotionArcChartData(arc: EmotionArc, scenes: Scene[]): ArcChartDatum[] {
  const scenesByNumber = new Map(scenes.map((scene) => [scene.scene_number, scene]))
  return arc.scene_mapping.map((mapping, index) => {
    const representativeScene = scenesByNumber.get(mapping.representative_scene_number)
    return {
      pointNumber: mapping.point_number,
      startSceneNumber: mapping.start_scene_number,
      endSceneNumber: mapping.end_scene_number,
      representativeSceneNumber: mapping.representative_scene_number,
      sceneTitle: representativeScene?.heading || `Scene ${mapping.representative_scene_number}`,
      valence: toDisplayValence(arc.valence[index]),
      tension: arc.tension[index] ?? null,
      ...Object.fromEntries(Object.entries(arc.characters).map(([name, values]) => [name, toDisplayValence(values[index])])),
    }
  })
}
