/**
 * 参数视图模式：简略 / 专业。
 *
 * 为什么需要「模式」而不是把所有参数都折叠起来：成熟产品的结论是分层的——
 * 深度如果只是「额外任务参数」（步数、种子、CFG 这类），用视图内渐进披露就够；
 * 但如果两类用户看到的是**不同的工作表面**（小白要的是「选个档位、点运行」），
 * 就必须有明确的模式切换，否则小白会在十几个数字输入框前直接放弃。
 *
 * 这里两者结合：
 * - 全局开关（本文件）：决定节点卡片默认长什么样；
 * - `AdvancedSection`：简略模式下把高级项折成一行「更多设置 ▾」，想看得点开，
 *   但**点了就能看**——不把功能藏到找不到的地方（那是另一种失败）。
 *
 * 存 localStorage：这是「我用这个工具的习惯」，不是项目数据，不该跟着项目走，
 * 也不该让每个新项目都重新选一遍。
 */
import { useSyncExternalStore } from 'react'

export type ViewMode = 'simple' | 'pro'

const KEY = 'pixelforge.viewMode'
const listeners = new Set<() => void>()

function read(): ViewMode {
  try {
    const v = localStorage.getItem(KEY)
    // 默认简略：新用户的第一次体验必须能「只填一句话就跑起来」
    return v === 'pro' ? 'pro' : 'simple'
  } catch {
    return 'simple'
  }
}

let mode: ViewMode = read()

export function getViewMode(): ViewMode {
  return mode
}

export function setViewMode(next: ViewMode): void {
  if (next === mode) return
  mode = next
  try {
    localStorage.setItem(KEY, next)
  } catch {
    /* 隐私模式下 localStorage 会抛，内存里生效就够 */
  }
  listeners.forEach((f) => f())
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

/** 组件里读当前模式；切换时同一棵树上所有节点一起变。 */
export function useViewMode(): ViewMode {
  return useSyncExternalStore(subscribe, getViewMode, getViewMode)
}

export const isPro = (m: ViewMode) => m === 'pro'
