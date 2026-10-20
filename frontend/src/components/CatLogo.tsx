/**
 * 像素猫 logo：用本地模型生成的 32×32 像素图（tools/gen_icon.py 产出）。
 *
 * 为什么不再手搓 SVG 网格：手画 16×16 时眼睛要么太凶、要么像面具，
 * 而模型按像素契约画 + FFT 网格精修，出来的比例和神态明显更耐看。
 * 显示尺寸取 32 的整数倍，最近邻放大才不会糊。
 */
export default function CatLogo({ size = 32, title = '喵喵叫的可爱助手' }: { size?: number; title?: string }) {
  return (
    <img
      className="logo-mark"
      src="/favicon-192.png"
      width={size}
      height={size}
      alt={title}
      title={title}
      draggable={false}
    />
  )
}

/** 介绍页的大猫：同一只，放大展示（最近邻，保留像素块）。 */
export function HeroCat() {
  return (
    <img
      className="hero-cat-img"
      src="/favicon-192.png"
      width={192}
      height={192}
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  )
}

/** 模板卡图标：用像素方块拼的小图标（sprite / frames / views / layers）。 */
export function PixelIcon({ kind, color = '#00FF88' }: { kind: string; color?: string }) {
  const maps: Record<string, string[]> = {
    sprite: [
      '..XXXX..',
      '.XXXXXX.',
      'XX.XX.XX',
      'XXXXXXXX',
      'X.XXXX.X',
      '.X.XX.X.',
      '..X..X..',
      '........',
    ],
    frames: [
      'XXX.XXX.',
      'X.X.X.X.',
      'X.X.X.X.',
      'XXX.XXX.',
      '........',
      'XXX.XXX.',
      'X.X.X.X.',
      'XXX.XXX.',
    ],
    views: [
      '..XXX...',
      '.X...X..',
      'X..X..X.',
      'X.XXX.X.',
      'X..X..X.',
      'X..X..X.',
      '.X...X..',
      '..XXX...',
    ],
    layers: [
      '..XXXX..',
      '.X....X.',
      'X.XXXX.X',
      'X.X..X.X',
      'X.X..X.X',
      'X.XXXX.X',
      '.X....X.',
      '..XXXX..',
    ],
  }
  const grid = maps[kind] || maps.sprite
  const size = grid.length
  return (
    <svg viewBox={`0 0 ${size} ${size}`} shapeRendering="crispEdges" aria-hidden="true">
      {grid.flatMap((row, y) =>
        row.split('').map((ch, x) =>
          ch === 'X' ? <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={color} /> : null,
        ),
      )}
    </svg>
  )
}
