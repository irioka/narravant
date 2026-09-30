import type { DocumentDetail } from '@/api/contracts'

export const analysisDemoDocument: DocumentDetail = {
  document_id: 'demo-analysis', owner_user_id: 'u-demo', owner_email: 'dev-user@narravant.local', shared_count: 2, title: 'コンフェッションズ short', current_version_id: 7, version_id: 7, expected_version: 7, is_saved: true, created_at: '2026-09-08T09:00:00+09:00', updated_at: '2026-09-08T11:55:00+09:00',
  metadata: { synopsis: '高校三年生の名倉柚樹は、バンド仲間である春岡主人に想いを告白するが気まずい反応をされ、練習も避けられてしまう。柚樹は同性愛への告白について考え始める。' },
  scenes: [{ scene_number: 1, heading: 'INT. 生田高校・視聴覚室 - 放課後', text: 'ドラムセットに座る名倉柚樹。主人が譜面台を見つめる。', dialogues: [{ character: '柚樹', line: '主人、告白って、したことある？' }] }, { scene_number: 2, heading: 'EXT. 校舎裏 - 夕方', text: '二人の間に沈黙が落ちる。', dialogues: [] }, { scene_number: 3, heading: 'INT. 視聴覚室 - 夜', text: '主人はゆっくりと頷く。', dialogues: [{ character: '主人', line: '逃げない。ちゃんと聞く。' }] }],
  emotion_arc: { valence: [6, 3, 5], tension: [-2, 1, 0], characters: { 名倉柚樹: [5, 0, 4], 春岡主人: [6, 4, 4] }, scene_mapping: Array.from({ length: 3 }, (_, index) => ({ point_number: index + 1, start_scene_number: index + 1, end_scene_number: index + 1, representative_scene_number: index + 1 })), valence_vector: [0, 0, 0, 0, 0, 0, 0, 0, 0, 1] },
  source_fountain: 'Title: コンフェッションズ short\nAuthor: 浮田航太\n\n「好き」という感情の多様性と、他者との関係性をカテゴラリーに押し込めることの危うさを描く。\n\n# あらすじ\n高校三年生の名倉柚樹は、バンド仲間である春岡主人に想いを告白する。\n\nINT. 生田高校・視聴覚室 - 放課後\n\nドラムセットに座る名倉柚樹。\n\n@主人\n告白って、したことある？',
  analysis: {
    status: 'completed',
    turning_points: [
      { tp_number: 1, label: 'Opportunity', availability: 'identified', scene_number: 1, change: '柚樹が主人に告白の話を切り出す。', involved_characters: [{ name: '名倉柚樹', goal: '想いを伝える', conflict: '拒絶への恐れ', choice: '話を切り出す', action: '問いかける', change: '決意が固まる' }] },
      { tp_number: 2, label: 'Change of Plans', availability: 'identified', scene_number: 2, change: '告白後の距離が練習にも影響し始める。', involved_characters: [{ name: '名倉柚樹', goal: '関係の修復', conflict: '会えないこと', choice: '気持ちの整理を優先する', action: '練習を避ける', change: '後悔が強まる' }] },
      { tp_number: 3, label: 'Point of No Return', availability: 'identified', scene_number: 3, change: '主人が黙って聞く姿勢を示し、退路が消える。', involved_characters: [{ name: '春岡主人', goal: '関係を壊さないこと', conflict: '答えの不明確さ', choice: '逃げない', action: '頷く', change: '受け入れる覚悟が固まる' }] },
      { tp_number: 4, label: 'Major Setback', availability: 'identified', scene_number: 3, change: '会話が中断し、二人の間に沈黙が残る。', involved_characters: [{ name: '名倉柚樹', goal: '対話の続き', conflict: '沉默', choice: '待つ', action: '言葉を留める', change: '失望と希望の同居' }] },
      { tp_number: 5, label: 'Climax', availability: 'identified', scene_number: 3, change: '主人が「逃げない。ちゃんと聞く」と応える。', involved_characters: [{ name: '春岡主人', goal: '柚樹と向き合う', conflict: '拒絶か受容か', choice: '聞く', action: '答える', change: '関係が前進する' }] },
    ],
    characters: [
      { name: '名倉柚樹', voice_traits: '', external_goal: '想いを伝える', internal_need: '自分を理解されること', fear_or_cost: '関係の崩壊', obstacle: '気まずさ', choice: '告白する', agency: '自ら言葉にする', goal_to_outcome: '伝えたい→対話の入口に立つ', related_turning_points: [1, 2, 4] },
      { name: '春岡主人', voice_traits: '', external_goal: '関係を保つ', internal_need: '誠実でありたい', fear_or_cost: '傷つけること', obstacle: '即答できない心', choice: '聞く', agency: '自分の速度で応じる', goal_to_outcome: '逃げたい→向き合う', related_turning_points: [3, 5] },
    ],
  },
  narrator: { voice_traits: '' },
  voice_assignments: [],
  capabilities: { can_edit: true, can_share: false, can_delete: true },
}
