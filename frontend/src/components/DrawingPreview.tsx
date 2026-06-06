import { useEffect, useRef, useCallback } from 'react'

export type MarkingType =
  | 'white-solid' | 'white-dashed' | 'yellow-solid' | 'yellow-dashed'
  | 'double-yellow' | 'yellow-solid-dash' | 'missing' | 'offset'
  | 'wrong-color' | 'wrong-arrow' | 'fishbone'

export interface LaneConfig {
  laneCount: number
  markings: MarkingType[]
  direction?: 'one-way' | 'two-way'
}

export interface DrawingPreviewProps {
  mode?: 'wrong' | 'correct'
  title?: string
  subtitle?: string
  errorType?: 'missing-lane' | 'wrong-color' | 'wrong-type' | 'offset' | 'wrong-arrow' | 'fishbone' | 'correct'
  config?: Partial<LaneConfig>
}

// ── Scenario presets ─────────────────────────────────────────────────────────
function getScenario(mode: 'wrong' | 'correct', errorType: string): LaneConfig {
  if (mode === 'correct') {
    switch (errorType) {
      case 'missing-lane': return { laneCount: 3, markings: ['white-solid', 'white-dashed', 'white-dashed', 'white-solid'] }
      case 'wrong-color':  return { laneCount: 2, markings: ['white-solid', 'yellow-solid', 'white-solid'], direction: 'two-way' }
      case 'fishbone':     return { laneCount: 3, markings: ['white-solid', 'fishbone', 'white-dashed', 'white-solid'] }
      default:             return { laneCount: 2, markings: ['white-solid', 'white-dashed', 'white-solid'] }
    }
  } else {
    switch (errorType) {
      case 'missing-lane': return { laneCount: 3, markings: ['white-solid', 'missing', 'white-dashed', 'white-solid'] }
      case 'wrong-color':  return { laneCount: 2, markings: ['white-solid', 'wrong-color', 'white-solid'], direction: 'two-way' }
      case 'wrong-type':   return { laneCount: 2, markings: ['white-solid', 'white-solid', 'white-solid'] }
      case 'offset':       return { laneCount: 2, markings: ['white-solid', 'offset', 'white-solid'] }
      case 'wrong-arrow':  return { laneCount: 2, markings: ['white-solid', 'wrong-arrow', 'white-solid'] }
      case 'fishbone':     return { laneCount: 3, markings: ['white-solid', 'missing', 'white-dashed', 'white-solid'] }
      default:             return { laneCount: 2, markings: ['white-solid', 'missing', 'white-solid'] }
    }
  }
}

// ── Projector class ──────────────────────────────────────────────────────────
// Canvas coords: x in [0,W], y in [0,H]. y=0 is far (horizon), y=H is near.
class Projector {
  vp_x: number   // vanishing point X (dynamic, can be offset by mouse)
  vp_y: number   // horizon Y
  scaleTop: number
  W: number
  H: number

  constructor(W: number, H: number, vp_x_ratio = 0.5, vp_y_ratio = 0.07, scaleTop = 0.60) {
    this.W = W
    this.H = H
    this.vp_x = W * vp_x_ratio
    this.vp_y = H * vp_y_ratio
    this.scaleTop = scaleTop
  }

  /** Project canvas-space (x, y) → [px, py, k].
   *  k=0 at horizon (far), k=1 at bottom (near).
   */
  project(x: number, y: number): [number, number, number] {
    const k = this.scaleTop + (y / this.H) * (1 - this.scaleTop)
    const px = this.vp_x + (x - this.vp_x) * k
    const py = this.vp_y + (y - this.vp_y) // y is already canvas y
    return [px, py, k]
  }

  /** Depth factor at canvas y — 0 far, 1 near. */
  depthAt(y: number): number {
    return (y - this.vp_y) / (this.H - this.vp_y)
  }

  /** Return a Path2D trapezoid for lane [laneIdx/(laneCount)] of the road. */
  laneQuad(laneIdx: number, laneCount: number): Path2D {
    const laneW = this.W / laneCount
    const xl = laneIdx * laneW
    const xr = xl + laneW
    const [tlx, tly] = this.project(xl, this.vp_y)
    const [trx, try_] = this.project(xr, this.vp_y)
    const [blx, bly] = this.project(xl, this.H)
    const [brx, bry] = this.project(xr, this.H)
    const p = new Path2D()
    p.moveTo(tlx, tly); p.lineTo(trx, try_)
    p.lineTo(brx, bry); p.lineTo(blx, bly)
    p.closePath()
    return p
  }

  /** Badge center position at given lane center x and canvas y. */
  badgePos(laneX: number, atY: number): [number, number, number] {
    return this.project(laneX, atY)
  }
}

// ── Color utilities ───────────────────────────────────────────────────────────
const FAR_COLOR = '#8899aa'

function hexToRgb(hex: string): [number, number, number] {
  const c = hex.replace('#', '').replace(/^(.)(.)(.)$/, '$1$1$2$2$3$3')
  const n = parseInt(c, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function lerpColor(near: string, far: string, t: number): string {
  const clamp = Math.min(1, Math.max(0, t))
  const [r0, g0, b0] = hexToRgb(near)
  const [r1, g1, b1] = hexToRgb(far)
  return `rgb(${Math.round(r0+(r1-r0)*clamp)},${Math.round(g0+(g1-g0)*clamp)},${Math.round(b0+(b1-b0)*clamp)})`
}

// ── Road surface with dynamic VP ─────────────────────────────────────────────
function drawRoadSurface(ctx: CanvasRenderingContext2D, W: number, H: number, proj: Projector, isError: boolean) {
  ctx.clearRect(0, 0, W, H)
  const bg = ctx.createLinearGradient(0, 0, 0, H)
  if (isError) { bg.addColorStop(0, '#1c1717'); bg.addColorStop(1, '#110d0d') }
  else          { bg.addColorStop(0, '#1a1e28'); bg.addColorStop(1, '#111520') }
  ctx.fillStyle = bg
  ctx.fillRect(0, 0, W, H)

  // Road shape from projector VP
  const topHalfW = proj.scaleTop * W / 2
  ctx.beginPath()
  ctx.moveTo(proj.vp_x - topHalfW, proj.vp_y)
  ctx.lineTo(proj.vp_x + topHalfW, proj.vp_y)
  ctx.lineTo(W, H); ctx.lineTo(0, H)
  ctx.closePath()
  const surf = ctx.createLinearGradient(0, proj.vp_y, 0, H)
  surf.addColorStop(0, 'rgba(50,56,66,0.55)')
  surf.addColorStop(0.5, 'rgba(44,50,60,0.74)')
  surf.addColorStop(1, 'rgba(38,43,52,0.92)')
  ctx.fillStyle = surf
  ctx.fill()

  // Grain
  for (let i = 0; i < 90; i++) {
    ctx.fillStyle = `rgba(255,255,255,${Math.random() * 0.009})`
    ctx.fillRect(Math.random() * W, Math.random() * H, 1.2, 1.2)
  }
}

// ── Segment-perspective line draw ─────────────────────────────────────────────
function drawPerspLine(
  ctx: CanvasRenderingContext2D,
  proj: Projector,
  x: number,            // canvas-space fixed X position for a vertical line
  color: string,
  baseWidth: number,
  dash: number[],
  baseOpacity: number,
) {
  const yStart = proj.vp_y + 1
  const yEnd = proj.H
  const hasDash = dash.length >= 2

  // Create a smooth vertical linear gradient
  const grad = ctx.createLinearGradient(0, yStart, 0, yEnd)
  
  // Sample 6 stops along the gradient to apply depth fade and color shift
  for (let i = 0; i <= 5; i++) {
    const t = i / 5
    const y = yStart + t * (yEnd - yStart)
    const [, , k] = proj.project(x, y)
    const alpha = baseOpacity * Math.pow(k, 1.45)
    
    // Lerp colors
    const rgb = hexToRgb(color)
    const farRgb = hexToRgb(FAR_COLOR)
    const mixedRgb = [
      Math.round(rgb[0] + (farRgb[0] - rgb[0]) * (1 - k)),
      Math.round(rgb[1] + (farRgb[1] - rgb[1]) * (1 - k)),
      Math.round(rgb[2] + (farRgb[2] - rgb[2]) * (1 - k))
    ]
    
    grad.addColorStop(t, `rgba(${mixedRgb[0]},${mixedRgb[1]},${mixedRgb[2]},${Math.min(1, alpha)})`)
  }

  ctx.save()
  ctx.fillStyle = grad
  ctx.shadowColor = color
  ctx.shadowBlur = 3
  
  if (!hasDash) {
    // Solid line - draw single trapezoid
    const [px0, py0, k0] = proj.project(x, yStart)
    const [px1, py1, k1] = proj.project(x, yEnd)
    
    const w0 = Math.max(0.8, baseWidth * k0)
    const w1 = Math.max(0.8, baseWidth * k1)
    
    ctx.beginPath()
    ctx.moveTo(px0 - w0 / 2, py0)
    ctx.lineTo(px0 + w0 / 2, py0)
    ctx.lineTo(px1 + w1 / 2, py1)
    ctx.lineTo(px1 - w1 / 2, py1)
    ctx.closePath()
    ctx.fill()
  } else {
    // Dashed line - draw individual trapezoids
    // Normalise dash to travel-space ratio
    const totalHeight = yEnd - yStart
    const dashRatio = dash[0] / totalHeight
    const gapRatio = dash[1] / totalHeight
    
    let t = 0.03 // start slightly below horizon to avoid division/scale issues
    while (t < 1.0) {
      const tEnd = Math.min(1.0, t + dashRatio)
      const y0 = yStart + t * totalHeight
      const y1 = yStart + tEnd * totalHeight
      
      const [px0, py0, k0] = proj.project(x, y0)
      const [px1, py1, k1] = proj.project(x, y1)
      
      const w0 = Math.max(0.8, baseWidth * k0)
      const w1 = Math.max(0.8, baseWidth * k1)
      
      ctx.beginPath()
      ctx.moveTo(px0 - w0 / 2, py0)
      ctx.lineTo(px0 + w0 / 2, py0)
      ctx.lineTo(px1 + w1 / 2, py1)
      ctx.lineTo(px1 - w1 / 2, py1)
      ctx.closePath()
      ctx.fill()
      
      t = tEnd + gapRatio
    }
  }
  ctx.restore()
}

// ── Draw each marking type ────────────────────────────────────────────────────
function drawMarking(ctx: CanvasRenderingContext2D, proj: Projector, x: number, type: MarkingType) {
  switch (type) {
    case 'white-solid':
      drawPerspLine(ctx, proj, x, '#e8e8e8', 3.8, [], 1.0)
      break
    case 'white-dashed':
      drawPerspLine(ctx, proj, x, '#e4e4e4', 2.8, [20, 14], 0.95)
      break
    case 'yellow-solid':
      drawPerspLine(ctx, proj, x, '#facc15', 3.2, [], 1.0)
      break
    case 'yellow-dashed':
      drawPerspLine(ctx, proj, x, '#facc15', 2.6, [20, 14], 0.90)
      break
    case 'double-yellow':
      drawPerspLine(ctx, proj, x - 4, '#facc15', 2.6, [], 1.0)
      drawPerspLine(ctx, proj, x + 4, '#facc15', 2.6, [], 1.0)
      break
    case 'yellow-solid-dash':
      drawPerspLine(ctx, proj, x - 4, '#facc15', 2.6, [], 1.0)
      drawPerspLine(ctx, proj, x + 4, '#facc15', 2.2, [20, 12], 0.88)
      break

    case 'fishbone': {
      // Spine
      drawPerspLine(ctx, proj, x, '#facc15', 2.0, [], 0.80)
      // Ribs — projected properly
      const H = proj.H
      for (let i = 0; i < 5; i++) {
        const fy = proj.vp_y + (i / 4) * (H - proj.vp_y)
        const [spx, spy, k] = proj.project(x, fy)
        const ribLen = k * 20
        const ribH  = k * 14
        const alpha = Math.min(1, Math.pow(k, 1.3) * 0.9)
        const lw = Math.max(1, k * 2.2)
        ctx.save()
        ctx.globalAlpha = alpha
        ctx.strokeStyle = lerpColor('#facc15', FAR_COLOR, 1 - k)
        ctx.lineWidth = lw
        ctx.shadowColor = '#facc15'; ctx.shadowBlur = 5
        ctx.beginPath(); ctx.moveTo(spx, spy); ctx.lineTo(spx - ribLen, spy + ribH); ctx.stroke()
        ctx.beginPath(); ctx.moveTo(spx, spy); ctx.lineTo(spx + ribLen, spy + ribH); ctx.stroke()
        ctx.restore()
      }
      break
    }

    case 'missing':
      drawPerspLine(ctx, proj, x, '#ef4444', 5, [8, 9], 0.30)
      break

    case 'offset': {
      // Correct pos (ghost green)
      drawPerspLine(ctx, proj, x, '#22c55e', 3, [6, 9], 0.28)
      // Wrong pos shifted
      drawPerspLine(ctx, proj, x + 18, '#e8e8e8', 2.8, [20, 14], 0.92)
      break
    }

    case 'wrong-color':
      drawPerspLine(ctx, proj, x, '#e8e8e8', 3.2, [], 0.95)
      break

    case 'wrong-arrow': {
      // Downward arrow projected through VP
      const midY = (proj.vp_y + proj.H) / 2
      const [ax, ay, k] = proj.project(x, midY)
      const len = k * 26
      const hw  = k * 10
      ctx.save()
      ctx.globalAlpha = Math.min(1, Math.pow(k, 1.2))
      ctx.strokeStyle = lerpColor('#ef4444', FAR_COLOR, 1 - k)
      ctx.fillStyle   = lerpColor('#ef4444', FAR_COLOR, 1 - k)
      ctx.lineWidth = Math.max(1.5, k * 2.8)
      ctx.shadowColor = '#ef4444'; ctx.shadowBlur = 8
      ctx.setLineDash([])
      ctx.beginPath(); ctx.moveTo(ax, ay - len); ctx.lineTo(ax, ay + len); ctx.stroke()
      ctx.beginPath()
      ctx.moveTo(ax, ay + len)
      ctx.lineTo(ax - hw, ay + len - hw * 0.7)
      ctx.lineTo(ax + hw, ay + len - hw * 0.7)
      ctx.closePath(); ctx.fill()
      ctx.restore()
      break
    }
  }
}

// ── Overlays ─────────────────────────────────────────────────────────────────
function drawWrongOverlay(
  ctx: CanvasRenderingContext2D, proj: Projector,
  config: LaneConfig, W: number
) {
  const laneW = W / config.laneCount
  config.markings.forEach((type, idx) => {
    if (!['missing', 'offset', 'wrong-color', 'wrong-arrow'].includes(type)) return
    // Clip to lane trapezoid
    const quad = proj.laneQuad(idx, config.laneCount)
    ctx.save()
    ctx.clip(quad)
    ctx.fillStyle = 'rgba(239,68,68,0.07)'
    ctx.fillRect(0, 0, W, proj.H)
    ctx.restore()

    // Badge at center of lane — projected
    const lx = (idx + 0.5) * laneW
    const [bx, by, k] = proj.badgePos(lx, proj.H * 0.52)
    const sym = type === 'missing' ? '✕' : type === 'offset' ? '~' : type === 'wrong-arrow' ? '↓' : '?'
    const bw = Math.max(18, k * 28), bh = Math.max(13, k * 18)

    ctx.save()
    ctx.globalAlpha = Math.min(1, Math.pow(k, 1.1) * 0.92)
    ctx.fillStyle = 'rgba(239,68,68,0.90)'
    // skew badge to match road perspective angle
    ctx.transform(1, 0, -0.05 * (1 - k), 1, 0, 0)
    if (ctx.roundRect) ctx.roundRect(bx - bw / 2, by - bh / 2, bw, bh, 4)
    else ctx.rect(bx - bw / 2, by - bh / 2, bw, bh)
    ctx.fill()
    ctx.fillStyle = '#fff'
    ctx.font = `bold ${Math.max(8, k * 11)}px Inter,sans-serif`
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    ctx.fillText(sym, bx, by)
    ctx.restore()
  })
}

function drawCorrectOverlay(
  ctx: CanvasRenderingContext2D, proj: Projector,
  config: LaneConfig, W: number
) {
  const laneW = W / config.laneCount
  config.markings.forEach((type, idx) => {
    if (['missing', 'offset'].includes(type)) return
    const quad = proj.laneQuad(idx, config.laneCount)
    ctx.save()
    ctx.clip(quad)
    ctx.fillStyle = 'rgba(34,197,94,0.05)'
    ctx.fillRect(0, 0, W, proj.H)
    ctx.restore()

    const lx = (idx + 0.5) * laneW
    const [bx, by, k] = proj.badgePos(lx, proj.H * 0.52)
    const bw = Math.max(16, k * 22), bh = Math.max(13, k * 18)

    ctx.save()
    ctx.globalAlpha = Math.min(1, Math.pow(k, 1.1) * 0.88)
    ctx.fillStyle = 'rgba(34,197,94,0.88)'
    ctx.transform(1, 0, -0.05 * (1 - k), 1, 0, 0)
    if (ctx.roundRect) ctx.roundRect(bx - bw / 2, by - bh / 2, bw, bh, 4)
    else ctx.rect(bx - bw / 2, by - bh / 2, bw, bh)
    ctx.fill()
    ctx.fillStyle = '#fff'
    ctx.font = `bold ${Math.max(8, k * 11)}px Inter,sans-serif`
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
    ctx.fillText('✓', bx, by)
    ctx.restore()
  })
}

function drawModeLabel(ctx: CanvasRenderingContext2D, W: number, mode: 'wrong' | 'correct', title?: string, subtitle?: string) {
  const isWrong = mode === 'wrong'
  const bColor  = isWrong ? 'rgba(239,68,68,0.20)' : 'rgba(34,197,94,0.16)'
  const bBorder = isWrong ? 'rgba(239,68,68,0.55)' : 'rgba(34,197,94,0.50)'
  const bText   = isWrong ? '#fca5a5' : '#86efac'
  const badge   = isWrong ? '✕ Sai' : '✓ Đúng'

  ctx.save()
  ctx.font = 'bold 10px Inter,sans-serif'
  const bw = ctx.measureText(badge).width + 18
  if (ctx.roundRect) ctx.roundRect(W - bw - 8, 6, bw, 18, 7)
  else ctx.rect(W - bw - 8, 6, bw, 18)
  ctx.fillStyle = bColor; ctx.fill()
  ctx.strokeStyle = bBorder; ctx.lineWidth = 1; ctx.setLineDash([]); ctx.stroke()
  ctx.fillStyle = bText; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
  ctx.fillText(badge, W - bw / 2 - 8, 15)
  ctx.restore()

  if (title) {
    ctx.save()
    ctx.font = '600 10.5px Inter,sans-serif'; ctx.fillStyle = '#e2e8f0'
    ctx.textAlign = 'left'; ctx.textBaseline = 'top'
    ctx.shadowColor = 'rgba(0,0,0,0.7)'; ctx.shadowBlur = 5
    ctx.fillText(title.slice(0, 28), 9, 8)
    ctx.restore()
  }
  if (subtitle) {
    ctx.save()
    ctx.font = '400 8.5px Inter,sans-serif'; ctx.fillStyle = 'rgba(148,163,184,0.85)'
    ctx.textAlign = 'left'; ctx.textBaseline = 'top'
    ctx.fillText(subtitle.slice(0, 36), 9, 23)
    ctx.restore()
  }
}

function resolveErrorType(issueName: string | undefined, mode: 'wrong' | 'correct'): string {
  if (!issueName) return mode === 'correct' ? 'correct' : 'missing-lane'
  const n = issueName.toLowerCase()
  if (n.includes('missing') && (n.includes('lane') || n.includes('line'))) return 'missing-lane'
  if (n.includes('color') || n.includes('colour') || n.includes('màu')) return 'wrong-color'
  if (n.includes('solid') || n.includes('dashed') || n.includes('type') || n.includes('loại')) return 'wrong-type'
  if (n.includes('offset') || n.includes('curve') || n.includes('lệch')) return 'offset'
  if (n.includes('arrow') || n.includes('mũi tên') || n.includes('direction')) return 'wrong-arrow'
  if (n.includes('fishbone') || n.includes('xương cá')) return 'fishbone'
  return mode === 'correct' ? 'correct' : 'missing-lane'
}

function hashText(s: string) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function DrawingPreview({
  mode = 'correct', title, subtitle, errorType, config: customConfig,
}: DrawingPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  // track mouse for parallax
  const vpOffsetRef  = useRef(0)
  const rafRef       = useRef<number | null>(null)

  const render = useCallback((vpOffset = 0) => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const W = container.offsetWidth || 280
    const H = 145

    canvas.width  = W * dpr
    canvas.height = H * dpr
    canvas.style.width  = `${W}px`
    canvas.style.height = `${H}px`
    ctx.scale(dpr, dpr)

    const et = errorType ?? resolveErrorType(title, mode)
    const scenario = getScenario(mode, et)
    const finalConfig: LaneConfig = { ...scenario, ...(customConfig ?? {}) }

    // Build projector — vp_x shifted by mouse parallax
    const proj = new Projector(W, H, 0.5 + vpOffset / W, 0.07, 0.60)

    void hashText(`${mode}:${title ?? ''}:${et}`)
    drawRoadSurface(ctx, W, H, proj, mode === 'wrong')

    // Draw markings
    const laneW = W / finalConfig.laneCount
    for (let i = 0; i <= finalConfig.laneCount; i++) {
      const x = i * laneW
      if (finalConfig.markings[i] !== undefined) {
        drawMarking(ctx, proj, x, finalConfig.markings[i])
      }
    }

    if (mode === 'wrong') drawWrongOverlay(ctx, proj, finalConfig, W)
    else drawCorrectOverlay(ctx, proj, finalConfig, W)

    drawModeLabel(ctx, W, mode, title, subtitle)
  }, [mode, title, subtitle, errorType, customConfig])

  useEffect(() => { render(0) }, [render])

  // Hover parallax
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const cx = e.clientX - rect.left - rect.width / 2
    const target = cx * 0.04  // ±~8px max parallax
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    const animate = () => {
      vpOffsetRef.current += (target - vpOffsetRef.current) * 0.15
      render(vpOffsetRef.current)
      if (Math.abs(vpOffsetRef.current - target) > 0.3) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }
    rafRef.current = requestAnimationFrame(animate)
  }, [render])

  const handleMouseLeave = useCallback(() => {
    const animate = () => {
      vpOffsetRef.current *= 0.85
      render(vpOffsetRef.current)
      if (Math.abs(vpOffsetRef.current) > 0.2) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(animate)
  }, [render])

  const borderColor  = mode === 'wrong' ? 'rgba(239,68,68,0.32)' : 'rgba(34,197,94,0.26)'
  const glowShadow   = mode === 'wrong'
    ? '0 2px 16px rgba(239,68,68,0.14), 0 0 1px rgba(239,68,68,0.3)'
    : '0 2px 16px rgba(34,197,94,0.10), 0 0 1px rgba(34,197,94,0.25)'

  return (
    <div
      ref={containerRef}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={{
        borderRadius: 12, overflow: 'hidden',
        border: `1px solid ${borderColor}`,
        boxShadow: glowShadow,
        width: '100%',
        cursor: 'default',
        transition: 'box-shadow 0.3s ease',
      }}
    >
      <canvas
        ref={canvasRef}
        style={{ display: 'block', width: '100%', height: 145 }}
      />
    </div>
  )
}
