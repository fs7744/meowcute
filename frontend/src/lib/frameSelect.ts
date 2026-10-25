/**
 * 选帧的两把筛子：跳帧步长 + 近似度过滤。
 *
 * 放在浏览器里算而不是回后端，是因为这两件事是**拖动滑杆时的即时反馈**：
 * 用户在 0~30% 之间找「刚好不丢动作、又没留下重复姿势」的那个点，每动一下
 * 都要看到候选帧变化。每帧后端都给了 16×16 灰度指纹（base64，256 字节），
 * 120 帧一共 30KB——够小，可以整段留在内存里反复算。
 *
 * 判据沿用后端的口径（`clipframes.diff_percent`）：两张 16×16 灰度的平均
 * 绝对差 / 255 × 100。前端这份实现要和它一致，否则「节点上写的阈值」和
 * 「后端算的 diff」对不上，用户会觉得滑杆值没意义。
 */
import type { ClipFrame } from '../types'

/** base64 指纹 → 字节数组。同一帧会被反复比较（每次改滑杆重算一遍），
 *  所以解码结果缓存起来：120 帧 × N 次拖动不做缓存就是几万次 atob。 */
const cache = new Map<string, Uint8Array>()

function decode(hash: string): Uint8Array | null {
  if (!hash) return null
  const hit = cache.get(hash)
  if (hit) return hit
  try {
    const bin = atob(hash)
    const out = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
    // 指纹数量有上限（一次最多 600 帧），但用户会反复重拆：
    // 超过 4000 条就把缓存整个丢掉重来，比做 LRU 简单且不会长歪
    if (cache.size > 4000) cache.clear()
    cache.set(hash, out)
    return out
  } catch {
    return null
  }
}

/** 两个指纹的差异百分比（0~100）。指纹长度不一致时视为「完全不同」。 */
export function hashDiff(a: string, b: string): number {
  const ba = decode(a)
  const bb = decode(b)
  if (!ba || !bb) return 100
  if (ba.length !== bb.length) return 100
  let sum = 0
  for (let i = 0; i < ba.length; i++) sum += Math.abs(ba[i] - bb[i])
  return (sum / ba.length / 255) * 100
}

export type FilterOptions = {
  /** 跳帧步长：每 N 帧取 1 帧（1 = 全保留） */
  step: number
  /** 近似度阈值（%）：与上一张**保留帧**差异低于它就算重复，丢掉 */
  simPct: number
}

/**
 * 两道筛子：先按步长等间距取，再按近似度与前一张保留帧比。
 *
 * 「与前一张保留帧比」而不是「与前一帧比」是关键的：视频里常有连续 5 帧
 * 几乎不动（起步、停步、定格），逐帧比较只会丢掉其中 4 帧，留下 1 帧；
 * 而跟保留帧比可以把整段静止压成 1 帧——正是用户要的「去掉重复姿势」。
 *
 * 阈值 0 时不过滤（只按步长）。第一帧永远保留：没有「上一帧」可比，
 * 而且空序列会让下游报错。
 */
export function applyFilters(frames: ClipFrame[], opt: FilterOptions): ClipFrame[] {
  if (!frames.length) return []
  const step = Math.max(1, Math.floor(opt.step || 1))
  const sim = Math.max(0, Math.min(100, opt.simPct || 0))
  const out: ClipFrame[] = []
  let lastHash = ''
  for (let i = 0; i < frames.length; i += step) {
    const f = frames[i]
    if (!out.length) {
      out.push(f)
      lastHash = f.hash
      continue
    }
    if (sim > 0 && hashDiff(lastHash, f.hash) < sim) continue
    out.push(f)
    lastHash = f.hash
  }
  return out
}

/** 筛完之后「相邻两帧差异」的分布，用来看这套参数是不是筛过头了。 */
export function diffStats(frames: ClipFrame[]): {
  median: number
  min: number
  max: number
  /** 低于 1% 的相邻对：基本是同一姿势，循环里会卡 */
  nearDup: number
} {
  if (frames.length < 2) return { median: 0, min: 0, max: 0, nearDup: 0 }
  const ds: number[] = []
  let nearDup = 0
  for (let i = 1; i < frames.length; i++) {
    const d = hashDiff(frames[i - 1].hash, frames[i].hash)
    ds.push(d)
    if (d < 1) nearDup++
  }
  ds.sort((a, b) => a - b)
  return {
    median: ds[Math.floor(ds.length / 2)],
    min: ds[0],
    max: ds[ds.length - 1],
    nearDup,
  }
}

/** 首尾帧差异：循环动画里这个值太小会「卡一下」（末帧≈首帧）。 */
export function loopSeam(frames: ClipFrame[]): number {
  if (frames.length < 2) return 0
  return hashDiff(frames[frames.length - 1].hash, frames[0].hash)
}
