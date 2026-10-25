import { useEffect, useState } from 'react'
import { api, loadPromptLimits } from '../api'
import type {
  PromptLimits, ResultImage, ViewGenNode as ViewGenNodeType,
} from '../types' 

type InstrPreview = Awaited<ReturnType<typeof api.threeviewInstruction>>

type Props = {
  node: ViewGenNodeType
  index: number
  running?: boolean
  /** 参考的正视图来自哪个节点（链条最上游那张） */
  sourceLabel?: string | null
  /** 复用的正视图参数摘要（画风/尺寸/步数/种子/背景）——有它就不用在
   *  本节点重复填写；null = 正视图不是生成卡（如选择图片），回退节点自身参数 */
  inherit?: {
    styleName: string | null
    width: number
    height: number
    steps: number | null
    seed: number | null
    maxSeqLen: number | null
    background: string | null
    /** 背景 id（gray/green/none/transparent）：预览提示词要用它，不能用展示用的标签 */
    backgroundId?: string | null
    /** 上游生成卡的角色描述原文：视角指令把它当「辨认清单」拼进去，
     *  预览要和真实生成一致，就必须拿到同一份文本 */
    character?: string | null
  } | null
  onChange: (patch: Partial<ViewGenNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const VIEW_CN = { side: '侧视图', side_r: '右视图', back: '背视图' } as const

/**
 * 视角生成节点（四视图的第 2/3/4 步）：以「正视图」为参考，用编辑底座指令接力
 * 生成侧视图、右视图或背视图。
 *
 * 三个关键设计：
 * 1. 参考永远是最上游那张正视图——链条中间隔着别的视角节点也会自动往前找，
 *    所以「生成图 → 侧视图 → 背视图」这条直线里，背视图不会拿侧视图当参考。
 * 2. 参数全部继承正视图（画风 / 尺寸 / 步数 / 种子 / 背景）：用户只在生成卡上
 *    填一次，侧/背/右自动复用——各张图底色一致，拼板抠底才不会咬出异色边。
 * 3. 指令只描述「视角怎么变」，不点名任何服装/配件/颜色：点名会直接变成
 *    「给角色加一件」的生成指令（归档项目踩过的坑）。
 */
export default function ViewGenNode({
  node, index, running, sourceLabel, inherit, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  // 老项目 / 旧模板里可能没有后加的字段，合并默认值避免控件拿到 undefined
  const DEFAULTS: ViewGenNodeType['viewgen'] = {
    enabled: true, view: 'side', styleId: null,
    steps: null, seed: null, width: 512, height: 768, maxSeqLen: null, pick: 0,
  }
  const v = { ...DEFAULTS, ...node.viewgen }
  const patch = (p: Partial<typeof v>) => onChange({ viewgen: { ...v, ...p } })

  // 四视图只把上游描述当前 N 字当「辨认清单」——超出部分读不到，
  // 这里必须把真实上限告诉用户，否则他会以为整段都进了模型。
  const [limits, setLimits] = useState<PromptLimits | null>(null)
  useEffect(() => {
    let alive = true
    loadPromptLimits().then((l) => { if (alive) setLimits(l) }).catch(() => undefined)
    return () => { alive = false }
  }, [])

  // ---- 视角指令：摊开给用户看 / 允许整段覆盖 --------------------------------
  // 以前这段 11 条条款整段硬编码在后端，卡片上只有一句「会做什么」的说明，
  // 用户既看不到程序给模型喂了什么，也没法改。现在默认值还在程序里（可覆盖的
  // 默认），卡片上展示拼装结果，一键就能改成自己的措辞。
  const [preview, setPreview] = useState<InstrPreview | null>(null)
  const [showPrompt, setShowPrompt] = useState(false)
  const customInstr = (node.viewgen?.instruction || '').trim()
  const customNeg = (node.viewgen?.negative || '').trim()
  const bgId = inherit?.backgroundId ?? 'gray'
  const charText = inherit?.character ?? ''
  useEffect(() => {
    let alive = true
    api.threeviewInstruction({
      view: v.view, character: charText, background: bgId,
    }).then((p) => { if (alive) setPreview(p) }).catch(() => undefined)
    return () => { alive = false }
  }, [v.view, charText, bgId])

  const meta = node.result?.viewMeta
  const isBack = v.view === 'back'
  const isRight = v.view === 'side_r'
  // 抽卡张数跟随正视图：正视图 n 张 → 本视角 n 张，逐张可看
  const results: ResultImage[] = node.images?.length
    ? node.images
    : (node.result ? [node.result] : [])
  const pick = Math.min(v.pick ?? 0, Math.max(0, results.length - 1))
  const shown = results[pick] ?? null
  const groupNo = (img: ResultImage, i: number) => (img.pairIndex ?? i) + 1
  const choose = (i: number) => onChange({
    viewgen: { ...v, pick: i },
    result: results[i],
  } as Partial<ViewGenNodeType>)

  return (
    <div className={`node viewgen ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 360 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">VIEW</span>
        <span className="node-title">生成{VIEW_CN[v.view]}</span>
        <span className="node-idx">V{index + 1}</span>
        <button className="node-x" title="删除这张视角卡"
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
            使用此节点{v.enabled ? '' : `（已关闭，直接透传正视图）`}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换视角生成节点">
            <i />
          </button>
        </div>

        <div className="hint">
          参考：{sourceLabel ? <b>{sourceLabel}</b> : '未连线（把「生成图」或「选择图片」连到左侧端口）'}
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          画风 / 尺寸 / 步数 / 种子 / 背景全部复用正视图，无需重复填写。
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          {isBack
            ? '角色旋转 180° 背对观众：脸部不可见，不新增任何衣物配件。'
            : isRight
              ? '角色整体转向画面右侧：展现完整侧面轮廓，造型以正视图为准。'
              : '角色整体转向画面左侧：展现完整侧面轮廓，造型以正视图为准。'}
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          单侧配件（腰挎刀 / 单肩包 / 单侧护具）按「同一侧、同一高度、同样佩戴方式」
          保留。上游的角色描述会作为「辨认清单」交给模型——只用来认出图里的部件，
          <b>不是增补依据</b>：图里没有的，描述写了也不画。
          {limits ? `最多读取前 ${limits.character.maxChars} 字。` : ''}
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          正视图抽卡几张，这里就对每张各生成一张（第 k 张 ↔ 第 k 组），
          角色不一致的那几张可以只在拼板里挑顺眼的一组。
        </div>

        <div style={{ marginTop: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className="gen-field-label" style={{ flex: 1 }}>
              视角提示词（{customInstr ? '已自定义' : '自动拼接'}）
            </span>
            <button className="ex-chip"
              onClick={() => setShowPrompt((s) => !s)}>
              {showPrompt ? '收起' : '查看 / 修改'}
            </button>
          </div>
          {!showPrompt ? (
            <div className="hint" style={{ marginTop: 2 }}>
              {preview
                ? `正文 ${preview.chars} 字（${preview.parts.length} 段条款）+ 负面词，点「查看」可读全文并改成自己的`
                : '读取中…'}
            </div>
          ) : (
            <>
              <div className="hint" style={{ marginTop: 4 }}>
                指令正文{customInstr ? '（自定义）' : '（程序拼接）'}
              </div>
              {customInstr ? (
                <>
                  <textarea className="textarea"
                    style={{ marginTop: 4, minHeight: 150, fontSize: 11 }}
                    value={node.viewgen?.instruction || ''}
                    onChange={(e) => patch({ instruction: e.target.value })} />
                  <div className="hint" style={{ marginTop: 2 }}>
                    {customInstr.length} 字
                    {preview ? ` / 建议不超过 ${preview.maxChars}` : ''}
                    {preview && customInstr.length > preview.maxChars
                      ? '（超出的部分编码器读不到）' : ''}
                    <br />
                    开头那句背景声明仍按背景设置自动追加。
                    <button className="ex-chip" style={{ marginLeft: 6 }}
                      onClick={() => patch({ instruction: '' })}>恢复默认</button>
                  </div>
                </>
              ) : (
                <>
                  <pre style={{
                    margin: '4px 0 0', padding: 6, maxHeight: 168, overflow: 'auto',
                    border: '1px solid var(--line)', background: 'rgba(0,0,0,0.25)',
                    fontSize: 11, lineHeight: 1.6, whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all', color: 'var(--muted)',
                  }}>{preview?.full ?? ''}</pre>
                  <div className="hint" style={{ marginTop: 2 }}>
                    上面就是送给模型的全部正文（含开头背景声明）。
                    <button className="ex-chip" style={{ marginLeft: 6 }}
                      onClick={() => patch({ instruction: preview?.body ?? '' })}>
                      改为自己写
                    </button>
                  </div>
                </>
              )}
              </>
          )}

          {/* 负面词：**挪到折叠面板外面**，且自动态也直接摆在输入框里。
              以前它藏在「查看 / 修改」面板里、自动态还是只读文字——想看默认是哪
              几个词得先展开，想改还得再点一下「改为自己写」预填。现在和生成卡
              同一个模式：看得见，直接改，一改就固定成自定义。 */}
          <div className="gen-field" style={{ marginTop: 8 }}>
            <div className="gen-field-head">
              <span className="gen-field-label">
                负面词{customNeg ? '（已自定义）' : '（自动）'}
              </span>
              {customNeg ? (
                <button className="ex-chip" type="button"
                  onClick={() => patch({ negative: '' })}>
                  恢复自动
                </button>
              ) : null}
            </div>
            <textarea className="textarea neg-textarea"
              style={{ minHeight: 56 }}
              title="真正会送进模型的负面词，可以直接改（一改就固定成自定义）"
              value={customNeg ? (node.viewgen?.negative || '') : (preview?.negative ?? '')}
              placeholder={preview === null ? '读取中…' : '（背景选「无」时不传负面词）'}
              onChange={(e) => patch({ negative: e.target.value })} />
            <div className="hint" style={{ lineHeight: 1.6 }}>
              {customNeg
                ? '已固定成这一段：换背景 / 换角色都不会再自动变。点「恢复自动」回到程序默认。'
                : '自动 = 程序按背景与画风拼的那串。直接改就固定成自定义。'}
            </div>
          </div>
        </div>

        {inherit ? (
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label">参数（复用正视图，不在此节点修改）</span>
              <div className="hint" style={{ lineHeight: 1.7 }}>
                画风：{inherit.styleName ?? '未选'} · {inherit.width}×{inherit.height}
                <br />
                步数：{inherit.steps ?? '自动'} · 种子：{inherit.seed ?? '随机'}
                <br />
                背景：{inherit.background ?? '中灰'} · 质量：
                {inherit.maxSeqLen ? { 512: '快速', 768: '标准', 1024: '高质量' }[inherit.maxSeqLen as 512 | 768 | 1024] ?? '自定义' : '自动'}
              </div>
            </label>
          </div>
        ) : (
          <div className="hint">
            上游不是「生成图」生成卡：本节点没有可继承的参数，执行时按默认值跑
            （尺寸跟随参考图、步数自动、种子随机、中灰底、画风不指定）。
            要让四视图参数统一，把「生成图」接到链条最上游，改它的参数即可。
          </div>
        )}

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 生成中…</> : `运行生成${VIEW_CN[v.view]}`}
        </button>

        {shown ? (
          <div style={{ marginTop: 10 }}>
            <button className="matte-thumb" title="点击放大"
              onClick={() => onPreview(shown.url, `生成${VIEW_CN[v.view]}`)}>
              <img src={shown.url} alt={VIEW_CN[v.view]} />
            </button>
            <div className="result-meta" style={{ borderTop: 0 }}>
              <span className="meta-pill good">{shown.width}×{shown.height}</span>
              {meta?.mode ? <span className="meta-pill">编辑接力</span> : null}
              {meta?.steps ? <span className="meta-pill">{meta.steps} 步</span> : null}
              {meta?.seed != null ? <span className="meta-pill">种子 {meta.seed}</span> : null}
              {(meta as any)?.check?.attempt
                ? <span className="meta-pill">重试 {(meta as any).check.attempt} 次</span>
                : null}
              {(meta as any)?.check && !(meta as any).check.ok
                ? <span className="meta-pill bad">配色未通过</span>
                : null}
              {results.length > 1
                ? <span className="meta-pill">第 {groupNo(shown, pick)}/{results.length} 组</span>
                : null}
            </div>
            {results.length > 1 ? (
              <>
                <div className="tv-pick-row">
                  {results.map((img, i) => (
                    <button
                      key={img.assetId}
                      className={`tv-pick ${i === pick ? 'on' : ''}`}
                      title={`第 ${groupNo(img, i)} 组（对应正视图第 ${groupNo(img, i)} 张）`}
                      onClick={() => choose(i)}
                    >
                      <img src={img.url} alt={`第 ${groupNo(img, i)} 组`} />
                      <span>{groupNo(img, i)}</span>
                    </button>
                  ))}
                </div>
                <div className="hint" style={{ marginTop: 2 }}>
                  正视图抽了 {results.length} 张，本视角也生成了 {results.length} 张（第 k 组 ↔
                  正视图第 k 张）。选哪张拼进四视图，在「四视图拼板」卡上逐格挑。
                </div>
              </>
            ) : null}
            {(meta as any)?.check && !(meta as any).check.ok ? (
              <div className="warn" style={{ marginTop: 4 }}>
                配色一致性未通过：{((meta as any).check.fails || []).join('；')}
                {(meta as any).check.attempt
                  ? '。已换种子重试仍不通过，建议重抽这一张。'
                  : ''}
              </div>
            ) : null}
            {(meta as any)?.bgWarning ? (
              <div className="warn" style={{ marginTop: 4 }}>{(meta as any).bgWarning}</div>
            ) : null}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有{VIEW_CN[v.view]}。连好正视图后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
