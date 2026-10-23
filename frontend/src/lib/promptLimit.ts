import type { PromptLimits } from '../types'

/**
 * 生成卡提示词上限：token 预算 − 画风词/像素契约的固定开销。
 *
 * 抽出来是因为有两个执行入口都要用：生成卡自己的按钮，和画布的「运行工作流」。
 * 只在前端某一处拦，另一处照样能把超长提示词发出去——用户看到的就是
 * 「我删了也不行 / 我没删却也能跑」。
 */
export type LimitInput = {
  styleId?: string | null
  maxSeqLen?: number | null
  /** 只有「像素类画风 + 像素精修开启」才真的会追加契约句 */
  pixelOn?: boolean
}

export const promptLimitFor = (limits: PromptLimits | null, input: LimitInput): number => {
  const seqLen = input.maxSeqLen || limits?.seqLenMax || 1920
  const fixed = limits?.styles[input.styleId || '']?.[input.pixelOn ? 'pixel' : 'plain'] ?? 0
  const budget = Math.floor(
    (seqLen - (limits?.overheadTokens ?? 64)) / (limits?.charsPerToken ?? 1.3))
  return Math.max(40, budget - fixed)
}
