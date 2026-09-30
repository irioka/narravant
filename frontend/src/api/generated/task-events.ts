/* This file is generated from backend/openapi.json. Do not edit manually. */
import { z } from 'zod'

export const taskProgressEventSchema = z.object({
  "message": z.string(),
  "percentage": z.number().int().min(0).max(100),
  "phase": z.enum(["queued", "cancelling", "structuring", "classifying", "reading", "building_plot_graph", "planning", "writing", "verifying", "analyzing"]),
  "received_characters": z.number().int().min(0).nullable().optional(),
})

export const taskCompletedEventSchema = z.object({
  "document_id": z.string().nullable().optional(),
  "import_draft": z.object({ "analysis": z.object({ "characters": z.array(z.object({ "agency": z.string().nullable().optional(), "choice": z.string().nullable().optional(), "external_goal": z.string().nullable().optional(), "fear_or_cost": z.string().nullable().optional(), "goal_to_outcome": z.string().nullable().optional(), "internal_need": z.string().nullable().optional(), "name": z.string(), "obstacle": z.string().nullable().optional(), "related_turning_points": z.array(z.number().int()).optional(), "voice_traits": z.string().default("") })), "status": z.enum(["completed", "not_requested"]), "turning_points": z.array(z.union([z.object({ "availability": z.literal("identified").default("identified"), "change": z.string(), "involved_characters": z.array(z.object({ "action": z.string(), "change": z.string(), "choice": z.string(), "conflict": z.string(), "goal": z.string(), "name": z.string() })), "label": z.string(), "reason": z.null().optional(), "scene_number": z.number().int().min(1), "tp_number": z.number().int().min(1).max(5) }), z.object({ "availability": z.literal("not_applicable").default("not_applicable"), "change": z.null().optional(), "involved_characters": z.array(z.object({ "action": z.string(), "change": z.string(), "choice": z.string(), "conflict": z.string(), "goal": z.string(), "name": z.string() })).optional(), "label": z.string(), "reason": z.string(), "scene_number": z.null().optional(), "tp_number": z.number().int().min(1).max(5) })])) }), "document_id": z.string(), "emotion_arc": z.object({ "characters": z.record(z.string(), z.array(z.number().int())), "scene_mapping": z.array(z.object({ "end_scene_number": z.number().int().min(1), "point_number": z.number().int().min(1), "representative_scene_number": z.number().int().min(1), "start_scene_number": z.number().int().min(1) })), "tension": z.array(z.number().int()), "valence": z.array(z.number().int()), "valence_vector": z.array(z.number()) }), "expected_version": z.number().int().min(1).nullable().default(null), "metadata": z.record(z.string(), z.unknown()), "narrator": z.object({ "voice_traits": z.string().default("") }).optional(), "scenes": z.array(z.object({ "dialogues": z.array(z.object({ "character": z.string(), "line": z.string() })), "heading": z.string(), "scene_number": z.number().int(), "text": z.string() })), "source_fountain": z.string(), "title": z.string(), "version_id": z.number().int().min(1), "voice_assignments": z.array(z.object({ "speaker": z.string(), "voice_id": z.string().nullable().optional(), "voice_traits": z.string().default("") })).optional() }).nullable().optional(),
  "reanalysis": z.object({ "emotion_arc": z.object({ "characters": z.record(z.string(), z.array(z.number().int())), "scene_mapping": z.array(z.object({ "end_scene_number": z.number().int().min(1), "point_number": z.number().int().min(1), "representative_scene_number": z.number().int().min(1), "start_scene_number": z.number().int().min(1) })), "tension": z.array(z.number().int()), "valence": z.array(z.number().int()), "valence_vector": z.array(z.number()) }) }).nullable().optional(),
  "version_id": z.number().int().nullable().optional(),
})

export const taskErrorEventSchema = z.object({
  "code": z.string(),
  "message": z.string(),
  "retryable": z.boolean(),
})

export const taskCancelledEventSchema = z.object({
  "message": z.string(),
})
