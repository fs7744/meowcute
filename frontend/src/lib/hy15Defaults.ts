/** hy15 分窗阈值的默认值：41（单位帧；0 = 显式关闭，整段生成）。
 *
 * 依据 tools/hy15_identity_drift.py 的 121 帧对照（同参考图 / 同提示词 / 同种子 / 12 步）：
 *   window=21 → 6 段：instability 30.8%、颜色漂移 76.8、耗时 122.7s、峰值 18.31GB
 *   window=41 → 3 段：instability 23.4%（回到整段水平）、颜色漂移 57.3、
 *                     耗时 104.3s（更快）、峰值 18.76GB（24G 内仍安全）
 *
 * 机理：角色漂移随「段数」累积——每段都要独立采样一次并重新 conditioning，
 * 段越多越容易漂到另一个人；而真正的瓶颈是显存（≈81 帧以上整段顶破 24G），
 * 所以要在显存安全区内尽量少分段：41 帧/段实测 18.76GB 已经接近上限，
 * 再往上调只是把这点收益换成 OOM 风险。
 *
 * 另注：**不要**改成「所有窗共用同一个 seed」：61 帧单样本看着更好，
 * 121 帧会直接崩坏（instability 202.6%、漂移 153.4，背景出现大面积彩色杂块）。
 *
 * 必须与后端 backend/app/providers/hy15.py 的 DEFAULT_WINDOW 保持同值。
 */
export const HY15_DEFAULT_WINDOW = 41

/** 分窗阈值的一句话依据，给 UI 提示复用，避免两处文案写不一样。 */
export const HY15_WINDOW_RATIONALE =
  '默认 41：段数越少漂移越小——121 帧 6 段→3 段，instability 30.8%→23.4%、' +
  '颜色漂移 76.8→57.3、耗时 122.7s→104.3s，而 41 帧/段峰值 18.76GB 仍在 24G 内。'

/** 显存预算，必须与后端 backend/app/providers/hy15.py 的同名常量一致。
 *  peak ≈ 14.6GB + 5.97e-5 × 帧数 × (W/16) × (H/16)
 *  拟合自 4090 实测（I2V / offload / 12 步 / 480x848）：
 *  21/33/49/61 帧 = 16.6/17.4/18.9/20.4GB，误差约 ±2GB。
 *  只用于 UI 提前提示「这个分辨率+帧数装不下」，真正调度在后端。 */
export const HY15_VRAM_BASE_GB = 14.6
export const HY15_VRAM_SLOPE = 5.97e-5
/** 单段安全线 = 实测跑通过的最重峰值（61 帧 / 480x848 = 20.4GB）。 */
export const HY15_SOFT_LIMIT_GB = 20.5

/** 估算「一次前向 frames 帧」的峰值显存（GB）。 */
export function hy15EstPeakGB(frames: number, w: number, h: number): number {
  const tpf = Math.max(1, Math.floor(w / 16) * Math.floor(h / 16))
  return HY15_VRAM_BASE_GB + HY15_VRAM_SLOPE * frames * tpf
}
