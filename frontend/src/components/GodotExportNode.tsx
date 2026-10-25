import type { Godot2DNode as Godot2DNodeType } from '../types'
import AdvancedSection from './AdvancedSection'

type Props = {
  node: Godot2DNodeType
  index: number
  running?: boolean
  error?: string | null
  /** 上游精灵图（没连线 / 没生成时为 null） */
  upstream: { count: number; cell: number; cols: number } | null
  onChange: (patch: Partial<Godot2DNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
}

/**
 * Godot 导出节点（素材链第 5 步）：精灵图 → SpriteFrames + AnimatedSprite2D 场景。
 *
 * 参数只有两类，都影响「交付形态」而不是画质：
 *
 * - **图集 / 逐帧**：一张图集 + AtlasTexture 切格（文件少、draw call 少）还是
 *   每帧一个 PNG（切片直观、便于单独替换）。市面两个流派都有人用，不给选项
 *   会逼用户自己拿图集去切。
 * - **res:// 目录 + 原点**：Godot 的资源引用是绝对路径，所以目录必须由用户
 *   指定、导出物整体落进去；原点决定 `set_position()` 到地面线时角色是站着
 *   还是埋进地里（像素角色默认脚底）。
 *
 * 「纹理过滤」默认最近邻：像素素材被线性采样一糊，前面几步的功夫全白费。
 * 这一条写进 .tscn（texture_filter = 1），所以不用改项目设置。
 */
export default function GodotExportNode({
  node, index, running, error, upstream,
  onChange, onDelete, onDragStart, onRun, onStartLink, onFinishLink,
}: Props) {
  const DEFAULTS: Godot2DNodeType['godot2d'] = {
    enabled: true, animName: 'default', name: 'walk', fps: 12, loop: true,
    mode: 'atlas', folder: 'sprites', origin: 'bottom', textureFilter: 'nearest',
  }
  const v = { ...DEFAULTS, ...node.godot2d }
  const patch = (p: Partial<typeof v>) => onChange({ godot2d: { ...v, ...p } })

  const meta = node.meta
  const checksOk = meta ? (meta.checks?.pathsMatch && meta.checks?.framesMatch) : false
  const noUpstream = !!upstream && upstream.count === 0

  const kb = (n: number) => (n > 1024 * 1024
    ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`)

  return (
    <div className={`node godot2d ${v.enabled ? '' : 'off'} ${running ? 'busy' : ''}`}
      style={{ left: node.x, top: node.y, width: 380 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">GODOT</span>
        <span className="node-title">Godot 导出</span>
        <span className="node-idx">E{index + 1}</span>
        <button className="node-x" title="删除这张节点"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（先点上游节点的输出端口）"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onFinishLink(node.id) }} />
        <div className="port out" title="按住拖到目标节点即可连线"
          onMouseDown={(e) => {
            e.stopPropagation()
            onStartLink(node.id, { clientX: e.clientX, clientY: e.clientY })
          }} />
      </div>

      <div className="node-body">
        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>
            使用此节点{v.enabled ? '' : '（已关闭）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换 Godot 导出">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{upstream
            ? <b>{upstream.count} 帧 · {upstream.cell}px 单元格</b>
            : '还没有精灵图（把「精灵图」或「动画预览」连到左侧端口）'}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="Godot 里的 animation 名，可以中文">
              动画名
            </span>
            <input className="input" value={v.animName}
              onChange={(e) => patch({ animName: e.target.value })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label"
              title="文件名主干（只用 ASCII）：tres / tscn / png 都用它">
              文件名
            </span>
            <input className="input" value={v.name}
              onChange={(e) => patch({ name: e.target.value })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="写进 SpriteFrames 的 speed">帧率</span>
            <input className="input" type="number" min={1} max={60} step={1}
              value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label"
              title="循环动画（行走 / 待机）开；一次性动作（攻击 / 死亡）关">
              循环
            </span>
            <button className={`switch ${v.loop ? 'on' : ''}`}
              onClick={() => patch({ loop: !v.loop })} aria-label="切换循环">
              <i />
            </button>
          </label>
        </div>

        <AdvancedSection title="更多设置"
          hint="图集/逐帧 · res:// 目录 · 原点 · 纹理过滤">
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label"
                title="图集：一张 PNG + AtlasTexture 切格（文件少）；逐帧：每帧一个 PNG">
                交付形态
              </span>
              <select className="input" value={v.mode}
                onChange={(e) => patch({ mode: e.target.value as typeof v.mode })}>
                <option value="atlas">图集 + AtlasTexture</option>
                <option value="files">逐帧 PNG</option>
              </select>
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="节点原点对齐：脚底 / 居中 / 顶部。像素角色一般用脚底">
                原点
              </span>
              <select className="input" value={v.origin}
                onChange={(e) => patch({ origin: e.target.value as typeof v.origin })}>
                <option value="bottom">脚底</option>
                <option value="center">居中</option>
                <option value="top">顶部</option>
              </select>
            </label>
          </div>

          <label className="gen-field" style={{ marginTop: 6 }}>
            <span className="gen-field-label"
              title="res:// 下的相对目录：导出物必须整体放在这里，.tres 里的路径才成立。会自动转小写">
              目标目录（res://）
            </span>
            <input className="input" value={v.folder}
              onChange={(e) => patch({ folder: e.target.value })} />
          </label>

          <label className="gen-field" style={{ marginTop: 6 }}>
            <span className="gen-field-label"
              title="最近邻 = 像素不糊（写进 .tscn，不用改项目设置）">
              纹理过滤
            </span>
            <select className="input" value={v.textureFilter}
              onChange={(e) => patch({ textureFilter: e.target.value as typeof v.textureFilter })}>
              <option value="nearest">最近邻（像素）</option>
              <option value="linear">线性（插画）</option>
            </select>
          </label>
        </AdvancedSection>

        <div className="hint" style={{ fontSize: 11 }}>
          目标路径 <code>res://{v.folder}/</code>（解压后把该文件夹放到 Godot 工程根目录）
        </div>

        <button className="btn btn-sm" onClick={onRun}
          disabled={running || !upstream || noUpstream}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 打包中…</> : '导出 Godot 4 工程包'}
        </button>
        {error ? <div className="node-error">{error}</div> : null}

        {meta ? (
          <>
            <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
              <span className="meta-pill">{meta.count} 帧</span>
              <span className="meta-pill">{meta.fps} fps</span>
              <span className="meta-pill">{meta.mode === 'files' ? '逐帧' : '图集'}</span>
              <span className={`meta-pill ${checksOk ? 'good' : 'bad'}`}
                title={checksOk
                  ? `包内 ${meta.checks.refsChecked} 条 res:// 引用全部命中，帧数与资源一致`
                  : (meta.checks?.missing?.[0] || '自检未通过')}>
                {checksOk ? '自检通过' : '自检异常'}
              </span>
            </div>

            <div className="godot-paths">
              <div><span>场景</span><code>{meta.resPaths?.scene ?? `res://${v.folder}/${v.name}.tscn`}</code></div>
              <div><span>动画资源</span><code>{meta.resPaths?.tres ?? `res://${v.folder}/${v.name}.tres`}</code></div>
            </div>

            {meta.warnings?.length ? (
              <div className="hint warn" style={{ marginTop: 6 }}>
                {meta.warnings.slice(0, 3).map((w) => <div key={w}>· {w}</div>)}
              </div>
            ) : null}

            <div className="grid-actions wrap">
              {meta.zip ? (
                <a className="btn btn-xs" href={meta.zip.url} download
                  title="整个工程包（含 .tres / .tscn / PNG / README），解到工程根目录即用">
                  下载 zip（{kb(meta.zip.bytes)}）
                </a>
              ) : null}
              {(meta.files || []).map((f) => (
                <a key={f.name} className="btn btn-xs btn-ghost" href={f.url} download>
                  {f.name}
                </a>
              ))}
            </div>
            <div className="hint" style={{ fontSize: 11 }}>
              导出于 {meta.seconds ? new Date(meta.seconds * 1000).toLocaleTimeString() : '—'} ·
              解压后把 <code>{v.folder.split('/').pop()}</code> 放进 Godot 工程根目录
            </div>
          </>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有导出包。上游精灵图跑出来之后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
