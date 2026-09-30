import { describe, expect, it } from 'vitest'
import { analysisSchema, turningPointSchema } from './contracts'

describe('turningPointSchema contracts', () => {
  it('accepts valid identified turning point', () => {
    const identified = {
      tp_number: 1,
      label: 'Opportunity',
      availability: 'identified',
      scene_number: 3,
      change: '主人公が機会を見出す。',
      involved_characters: [
        {
          name: 'アリス',
          goal: '冒険したい',
          conflict: '退屈な日常',
          choice: 'ウサギを追う',
          action: '穴に飛び込む',
          change: '不思議の国へ入る',
        },
      ],
      reason: null,
    }

    const result = turningPointSchema.safeParse(identified)
    expect(result.success).toBe(true)
  })

  it('accepts valid not_applicable turning point', () => {
    const notApplicable = {
      tp_number: 2,
      label: 'Change of Plans',
      availability: 'not_applicable',
      scene_number: null,
      change: null,
      involved_characters: [],
      reason: '原作内に計画変更に該当する転換点は確認できない。',
    }

    const result = turningPointSchema.safeParse(notApplicable)
    expect(result.success).toBe(true)
  })

  it('rejects unknown fields due to strict schema', () => {
    const tampered = {
      tp_number: 1,
      label: 'Opportunity',
      availability: 'identified',
      scene_number: 3,
      change: '主人公が機会を見出す。',
      involved_characters: [
        {
          name: 'アリス',
          goal: '冒険したい',
          conflict: '退屈な日常',
          choice: 'ウサギを追う',
          action: '穴に飛び込む',
          change: '不思議の国へ入る',
        },
      ],
      reason: null,
      unknown_field: 'malicious',
    }

    const result = turningPointSchema.safeParse(tampered)
    expect(result.success).toBe(false)
  })

  it('rejects invalid combinations for not_applicable', () => {
    const invalid = {
      tp_number: 2,
      label: 'Change of Plans',
      availability: 'not_applicable',
      scene_number: 5, // not_applicable must have null
      change: null,
      involved_characters: [],
      reason: '理由',
    }

    const result = turningPointSchema.safeParse(invalid)
    expect(result.success).toBe(false)
  })

  it('allows empty characters list in analysisSchema', () => {
    const analysis = {
      status: 'completed',
      turning_points: [
        {
          tp_number: 1,
          label: 'Opportunity',
          availability: 'identified',
          scene_number: 1,
          change: '開始',
          involved_characters: [
            {
              name: 'A',
              goal: 'g',
              conflict: 'c',
              choice: 'ch',
              action: 'ac',
              change: 'cg',
            },
          ],
          reason: null,
        },
        {
          tp_number: 2,
          label: 'Change of Plans',
          availability: 'not_applicable',
          scene_number: null,
          change: null,
          involved_characters: [],
          reason: 'なし',
        },
        {
          tp_number: 3,
          label: 'Point of No Return',
          availability: 'not_applicable',
          scene_number: null,
          change: null,
          involved_characters: [],
          reason: 'なし',
        },
        {
          tp_number: 4,
          label: 'Major Setback',
          availability: 'not_applicable',
          scene_number: null,
          change: null,
          involved_characters: [],
          reason: 'なし',
        },
        {
          tp_number: 5,
          label: 'Climax',
          availability: 'not_applicable',
          scene_number: null,
          change: null,
          involved_characters: [],
          reason: 'なし',
        },
      ],
      characters: [],
    }

    const result = analysisSchema.safeParse(analysis)
    expect(result.success).toBe(true)
  })
})
