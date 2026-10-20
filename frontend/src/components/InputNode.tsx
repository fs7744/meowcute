import { useRef, useState } from 'react'
import { api } from '../api'
import type { InputNode as InputNodeType } from '../types'

type Props = {
  node: InputNodeType
  index: number
  projectId: string
  onChange: (patch: Partial<InputNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onStartLink: (id: string) => void
}

/** 选择图片节点：上传图片，作为下游「生成图 / 像素精修」的输入。 */
export default function InputNode({
  node, index, projectId, onChange, onDelete, onDragStart, onStartLink,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    try {
      const room = Math.max(0, 4 - node.refs.length)
      const picked = Array.from(files).slice(0, room)
      const added: any[] = []
      for (const f of picked) {
        const res = await api.uploadRef(projectId, f)
        added.push(res.ref)
      }
      if (added.length) onChange({ refs: [...node.refs, ...added] })
    } catch (e: any) {
      alert(`图片上传失败：${e.message || e}`)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="node input" style={{ left: node.x, top: node.y, width: 340 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">INPUT</span>
        <span className="node-title">选择图片{node.label ? ` · ${node.label}` : ''}</span>
        <span className="node-idx">I{index + 1}</span>
        <button className="node-x" title="删除这张输入卡"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port out" title="点击后，再点目标节点的输入端口连线"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onStartLink(node.id) }} />
      </div>

      <div className="node-body">
        <div className="hint">上传的图片会作为下游「生成图」的参考图，或直接交给「像素精修」。</div>
        <div className="ref-strip">
          {node.refs.map((r) => (
            <div className="ref-thumb" key={r.id} title={r.name}>
              <img src={r.url} alt={r.name} />
              <button className="kill" title="移除"
                onClick={() => onChange({ refs: node.refs.filter((x) => x.id !== r.id) })}>×</button>
            </div>
          ))}
          {node.refs.length < 4 ? (
            <button className="ref-add" title="添加图片" disabled={busy}
              onClick={() => fileRef.current?.click()}>
              {busy ? <span className="spin" /> : '+'}
            </button>
          ) : null}
        </div>
        <input ref={fileRef} type="file" accept="image/*" multiple hidden
          onChange={(e) => upload(e.target.files)} />
        <label className="field" style={{ marginTop: 8 }}>
          <span className="field-label">备注（可选）</span>
          <input className="input input-sm" placeholder="例：角色锚点图"
            value={node.label || ''}
            onChange={(e) => onChange({ label: e.target.value })} />
        </label>
        <div className="hint" style={{ marginTop: 6 }}>
          右侧 OUTPUT 端口 → 点「生成图」或「像素精修」的左侧端口即可连线。
        </div>
      </div>
    </div>
  )
}
