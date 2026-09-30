import { z } from 'zod'

import {
  GetDocumentDetailApiV1DocumentsDocIdGetResponse,
  ListDocumentsApiV1DocumentsGetResponse,
} from './generated/contracts'

export const userSchema = z.object({
  user_id: z.string(),
  email: z.string().email(),
  display_name: z.string().optional(),
})

export const dialogueSchema = z.object({ character: z.string(), line: z.string() })
export const sceneSchema = z.object({
  scene_number: z.number().int(),
  heading: z.string(),
  text: z.string(),
  dialogues: z.array(dialogueSchema).default([]),
})
export const emotionArcPointMappingSchema = z.object({
  point_number: z.number().int().min(1),
  start_scene_number: z.number().int().min(1),
  end_scene_number: z.number().int().min(1),
  representative_scene_number: z.number().int().min(1),
}).strict()
export const emotionArcSchema = z.object({
  valence: z.array(z.number().int().min(1).max(7)),
  tension: z.array(z.number().int().min(-3).max(3)),
  characters: z.record(z.string(), z.array(z.number().int().min(0).max(7))),
  scene_mapping: z.array(emotionArcPointMappingSchema),
  valence_vector: z.array(z.number()).length(10),
})
export const documentItemSchema = z.object({
  document_id: z.string(),
  owner_user_id: z.string(),
  title: z.string(),
  current_version_id: z.number().int(),
  version_id: z.number().int(),
  expected_version: z.number().int(),
  is_saved: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
  gcs_uri: z.string().nullable().optional(),
  arc_distance: z.number().nullable().optional(),
  owner_email: z.string(),
  shared_count: z.number().int(),
})
export const documentListSchema = z.object({
  items: z.array(documentItemSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
})
export const turningPointCharacterSchema = z.object({
  name: z.string().min(1),
  goal: z.string().min(1),
  conflict: z.string().min(1),
  choice: z.string().min(1),
  action: z.string().min(1),
  change: z.string().min(1),
}).strict()

export const identifiedTurningPointSchema = z.object({
  tp_number: z.number().int().min(1).max(5),
  label: z.string(),
  availability: z.literal('identified'),
  scene_number: z.number().int().min(1),
  change: z.string().min(1),
  involved_characters: z.array(turningPointCharacterSchema).min(1),
  reason: z.null().optional(),
}).strict()

export const notApplicableTurningPointSchema = z.object({
  tp_number: z.number().int().min(1).max(5),
  label: z.string(),
  availability: z.literal('not_applicable'),
  scene_number: z.null().optional(),
  change: z.null().optional(),
  involved_characters: z.array(turningPointCharacterSchema).max(0).default([]).optional(),
  reason: z.string().min(1),
}).strict()

export const turningPointSchema = z.discriminatedUnion('availability', [
  identifiedTurningPointSchema,
  notApplicableTurningPointSchema,
])

export type IdentifiedTurningPoint = z.infer<typeof identifiedTurningPointSchema>
export type NotApplicableTurningPoint = z.infer<typeof notApplicableTurningPointSchema>
export type TurningPoint = z.infer<typeof turningPointSchema>
export const characterSchema = z.object({
  name: z.string(),
  external_goal: z.string().nullable().optional(),
  internal_need: z.string().nullable().optional(),
  fear_or_cost: z.string().nullable().optional(),
  obstacle: z.string().nullable().optional(),
  choice: z.string().nullable().optional(),
  agency: z.string().nullable().optional(),
  goal_to_outcome: z.string().nullable().optional(),
  related_turning_points: z.array(z.number().int().min(1).max(5)).default([]),
})
export const analysisSchema = z.object({
  status: z.enum(['completed', 'not_requested']),
  turning_points: z.array(turningPointSchema),
  characters: z.array(characterSchema),
})
export const documentDetailSchema = documentItemSchema.extend({
  generation: z.number().int().nullable().optional(),
  metadata: z.record(z.string(), z.unknown()).default({}),
  scenes: z.array(sceneSchema).default([]),
  emotion_arc: emotionArcSchema,
  source_fountain: z.string(),
  analysis: analysisSchema,
  capabilities: z.object({ can_edit: z.boolean(), can_share: z.boolean(), can_delete: z.boolean() }).optional(),
})
export const documentVersionSchema = z.object({
  version_id: z.number().int(),
  created_at: z.string(),
  is_current: z.boolean().optional(),
})
export const taskStartSchema = z.object({ task_id: z.string() })
export const valenceSimilaritySchema = z.object({
  items: z.array(z.object({ name: z.string(), description: z.string(), percentage: z.number() })),
})

export type User = z.infer<typeof userSchema>
export type DocumentItem = z.infer<typeof ListDocumentsApiV1DocumentsGetResponse>['items'][number]
export type DocumentDetail = z.infer<typeof GetDocumentDetailApiV1DocumentsDocIdGetResponse>
export type EmotionArc = DocumentDetail['emotion_arc']
export type Scene = DocumentDetail['scenes'][number]
export type Analysis = DocumentDetail['analysis']
export type Character = Analysis['characters'][number]
