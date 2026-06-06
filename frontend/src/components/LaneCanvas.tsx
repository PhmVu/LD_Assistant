import { useEffect, useRef } from 'react'

/** Shape coming from backend drawing_engine.py */
export interface DrawingLayer {
  style: {
    color: string
    width: number
    dash: number[]
    opacity: number
  }
  polyline4d: number[][]
}

export interface CameraConfig {
  vp_x_ratio: number   // vanishing point X as fraction of canvas W (0–1)
  vp_y_ratio: number   // horizon Y as fraction of canvas H (0–1)
  fov: number          // depth factor (0.5–1.0)
  scale_top: number    // road top width = W * scale_top
}

export interface DrawingInstruction {
  style: {
    color: string
    width: number
    dash: number[]
    opacity: number
  }
  polyline4d: number[][]
  layers?: DrawingLayer[]
  note?: string
  scene?: string
  error?: boolean
  camera_config?: CameraConfig
  road_config?: {
    lanes?: number
    direction?: string
    marking_note?: string
  }
}

interface Props {
  data: DrawingInstruction
  label?: string
  /** compact: 220×110, full: 440×190, panel: 100% width 155px */
  size?: 'compact' | 'full' | 'panel'
}

// ── Colour utilities ─────────────────────────────────────────────────────────
function hexToRgb(hex: string): [number, number, number] {
  const c = hex.replace('#', '')
  const n = parseInt(c.length === 3 ? c.split('').map(x => x + x).join('') : c, 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function lerpColor(near: string, far: string, t: number): string {
  // t=0 → near (close), t=1 → far (distant)
  const [r0, g0, b0] = hexToRgb(near)
  const [r1, g1, b1] = hexToRgb(far)
  const r = Math.round(r0 + (r1 - r0) * t)
  const g = Math.round(g0 + (g1 - g0) * t)
  const b = Math.round(b0 + (b1 - b0) * t)
  return `rgb(${r},${g},${b})`
}

// ── World-space perspective projector ────────────────────────────────────────
// polyline4d format: [x_travel, y_cross, z, t]
//   x_travel (0→160): road travel direction = depth (0=far/horizon, 160=near/camera)
//   y_cross  (0→120): cross-road lateral position
// We map:  world_z = pt[0] (depth), world_x = pt[1] (lateral)
const WORLD_DEPTH = 160   // max travel distance
const WORLD_CROSS = 120   // road cross width
const FAR_COLOR = '#b0bec5'

interface Cam {
  vp_x: number   // vanishing point X in canvas px
  vp_y: number   // horizon Y in canvas px
  fov: number
  scale_top: number
}

function buildCam(cfg: CameraConfig | undefined, W: number, H: number): Cam {
  const c = cfg ?? { vp_x_ratio: 0.5, vp_y_ratio: 0.07, fov: 0.72, scale_top: 0.60 }
  return {
    vp_x: W * c.vp_x_ratio,
    vp_y: H * c.vp_y_ratio,
    fov: c.fov,
    scale_top: c.scale_top,
  }
}

/** Project world point (depth wz, lateral wx) → canvas [cx, cy, depth_k].
 *  wz=0 → horizon (far, top of canvas), wz=WORLD_DEPTH → camera (near, bottom).
 *  wx=0 → left edge, wx=WORLD_CROSS → right edge.
 *  depth_k: 0=far … 1=near.
 */
function projectWS(wz: number, wx: number, cam: Cam, W: number, H: number): [number, number, number] {
  const nz = wz / WORLD_DEPTH                         // 0=far, 1=near
  const k = cam.scale_top + nz * (1 - cam.scale_top)  // road width scale
  const nx = (wx / WORLD_CROSS) - 0.5                 // -0.5 … +0.5 lateral
  const cx = cam.vp_x + nx * k * W
  const cy = cam.vp_y + nz * (H - cam.vp_y)
  return [cx, cy, k]
}

// ── Road surface ─────────────────────────────────────────────────────────────
function drawRoadSurface(ctx: CanvasRenderingContext2D, W: number, H: number, cam: Cam, isError: boolean) {
  ctx.clearRect(0, 0, W, H)

  // Background
  const bg = ctx.createLinearGradient(0, 0, 0, H)
  if (isError) {
    bg.addColorStop(0, '#1a1515'); bg.addColorStop(1, '#120e0e')
  } else {
    bg.addColorStop(0, '#1a1e26'); bg.addColorStop(1, '#11141c')
  }
  ctx.fillStyle = bg
  ctx.fillRect(0, 0, W, H)

  // Road trapezoid using cam VP
  const [tlx, tly] = [cam.vp_x - (cam.scale_top * W) / 2, cam.vp_y]
  const [trx, try_] = [cam.vp_x + (cam.scale_top * W) / 2, cam.vp_y]
  ctx.beginPath()
  ctx.moveTo(tlx, tly)
  ctx.lineTo(trx, try_)
  ctx.lineTo(W, H)
  ctx.lineTo(0, H)
  ctx.closePath()

  const surf = ctx.createLinearGradient(0, tly, 0, H)
  surf.addColorStop(0, 'rgba(52,58,68,0.55)')
  surf.addColorStop(0.5, 'rgba(45,51,60,0.75)')
  surf.addColorStop(1, 'rgba(38,44,52,0.92)')
  ctx.fillStyle = surf
  ctx.fill()

  // Subtle asphalt grain
  for (let i = 0; i < 80; i++) {
    const gx = Math.random() * W
    const gy = Math.random() * H
    ctx.fillStyle = `rgba(255,255,255,${Math.random() * 0.009})`
    ctx.fillRect(gx, gy, 1.2, 1.2)
  }
}



// ── Helper to extract polyline segment between z0 and z1 in world space ─────────
function getPolylineSegmentBetweenZ(pts: number[][], z0: number, z1: number): number[][] {
  const result: number[][] = []
  for (let i = 0; i < pts.length - 1; i++) {
    const [zA, xA] = pts[i]
    const [zB, xB] = pts[i + 1]
    const minZ = Math.min(zA, zB)
    const maxZ = Math.max(zA, zB)
    if (maxZ < z0 || minZ > z1) continue

    if (zA >= z0 && zA <= z1) {
      result.push([zA, xA])
    }
    if ((zA < z0 && zB > z0) || (zA > z0 && zB < z0)) {
      const t = (z0 - zA) / (zB - zA)
      const x = xA + t * (xB - xA)
      result.push([z0, x])
    }
    if ((zA < z1 && zB > z1) || (zA > z1 && zB < z1)) {
      const t = (z1 - zA) / (zB - zA)
      const x = xA + t * (xB - xA)
      result.push([z1, x])
    }
  }
  if (pts.length > 0) {
    const [lastZ, lastX] = pts[pts.length - 1]
    if (lastZ >= z0 && lastZ <= z1) {
      result.push([lastZ, lastX])
    }
  }
  const unique: number[][] = []
  const seen = new Set<string>()
  for (const pt of result) {
    const key = `${pt[0].toFixed(2)}_${pt[1].toFixed(2)}`
    if (!seen.has(key)) {
      seen.add(key)
      unique.push(pt)
    }
  }
  return unique.sort((a, b) => a[0] - b[0])
}

// ── Segment-by-segment perspective line draw ─────────────────────────────────
function drawLayerPerspective(
  ctx: CanvasRenderingContext2D,
  layer: DrawingLayer,
  cam: Cam,
  W: number,
  H: number,
) {
  if (!layer.polyline4d?.length) return
  const pts = layer.polyline4d
  const { color, width, dash, opacity } = layer.style
  if (!opacity) return

  const N = pts.length
  
  // Horizontal stripe: travel coords nearly equal (crosswalk / stop_line)
  const isHorizontal = N === 2 && Math.abs(pts[0][0] - pts[1][0]) < 5

  // Create a smooth vertical linear gradient
  const grad = ctx.createLinearGradient(0, cam.vp_y, 0, H)
  for (let i = 0; i <= 5; i++) {
    const t = i / 5
    const k = cam.scale_top + t * (1 - cam.scale_top)
    const alpha = opacity * Math.pow(k, 1.45)
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

  const drawSegmentAsPolygon = (segmentPts: number[][], isHoriz: boolean) => {
    if (segmentPts.length < 2) return
    ctx.beginPath()

    const leftPoints: Array<[number, number]> = []
    const rightPoints: Array<[number, number]> = []

    const wWorld_half = width * 5.4

    for (let i = 0; i < segmentPts.length; i++) {
      const [z, x] = segmentPts[i]
      const offX = isHoriz ? 0 : wWorld_half
      const offY = isHoriz ? wWorld_half : 0

      const [lpx, lpy] = projectWS(z - offY, x - offX, cam, W, H)
      const [rpx, rpy] = projectWS(z + offY, x + offX, cam, W, H)

      leftPoints.push([lpx, lpy])
      rightPoints.push([rpx, rpy])
    }

    ctx.moveTo(leftPoints[0][0], leftPoints[0][1])
    for (let i = 1; i < leftPoints.length; i++) {
      ctx.lineTo(leftPoints[i][0], leftPoints[i][1])
    }
    for (let i = rightPoints.length - 1; i >= 0; i--) {
      ctx.lineTo(rightPoints[i][0], rightPoints[i][1])
    }
    ctx.closePath()
    ctx.fill()
  }

  const hasDash = dash && dash.length >= 2

  if (!hasDash) {
    drawSegmentAsPolygon(pts, isHorizontal)
  } else {
    // Slicing polyline in world Z units
    const dashLen = dash[0]
    const gapLen = dash[1]
    
    const sortedPts = [...pts].sort((a, b) => a[0] - b[0])
    if (sortedPts.length >= 2) {
      const minZ = sortedPts[0][0]
      const maxZ = sortedPts[sortedPts.length - 1][0]
      
      let curZ = minZ
      while (curZ < maxZ) {
        const nextZ = Math.min(maxZ, curZ + dashLen)
        const segmentPts = getPolylineSegmentBetweenZ(sortedPts, curZ, nextZ)
        if (segmentPts.length >= 2) {
          drawSegmentAsPolygon(segmentPts, isHorizontal)
        }
        curZ = nextZ + gapLen
      }
    }
  }

  ctx.restore()
}

// ── Arrow head ───────────────────────────────────────────────────────────────
function drawArrowHead(
  ctx: CanvasRenderingContext2D,
  pts: Array<[number, number, number]>,
  color: string,
  k: number,
) {
  if (pts.length < 2) return
  const last = pts[pts.length - 1]
  const prev = pts[pts.length - 2]
  const angle = Math.atan2(last[1] - prev[1], last[0] - prev[0])
  const sz = Math.max(7, k * 14)
  ctx.save()
  ctx.setLineDash([])
  ctx.fillStyle = lerpColor(color, FAR_COLOR, 1 - k)
  ctx.globalAlpha = Math.min(1, Math.pow(k, 1.2))
  ctx.shadowColor = color
  ctx.shadowBlur = 10
  ctx.beginPath()
  ctx.moveTo(last[0], last[1])
  ctx.lineTo(last[0] - sz * Math.cos(angle - Math.PI / 6), last[1] - sz * Math.sin(angle - Math.PI / 6))
  ctx.lineTo(last[0] - sz * Math.cos(angle + Math.PI / 6), last[1] - sz * Math.sin(angle + Math.PI / 6))
  ctx.closePath()
  ctx.fill()
  ctx.restore()
}

// ── Scene label ──────────────────────────────────────────────────────────────
function drawSceneLabel(ctx: CanvasRenderingContext2D, scene: string, isError: boolean) {
  const labels: Record<string, string> = {
    dashed: 'Vạch đứt',
    solid: 'Vạch liền',
    edge: 'Lề đường',
    arrow: 'Mũi tên',
    crosswalk: 'Zebra',
    stop_line: 'Vạch dừng',
    double_yellow: 'Đôi vàng',
    yellow_solid_dash: 'Vàng liền+đứt',
    fishbone: 'Xương cá',
    stop_bar_double: 'Vạch dừng đôi',
    error_missing_lane: 'Thiếu vạch',
    error_wrong_color: 'Sai màu',
    error_wrong_type: 'Sai loại',
    error_wrong_arrow: 'Mũi tên sai',
    error_offset: 'Lệch vị trí',
  }
  const cleanLabels: Record<string, string> = {
    dashed: 'Vạch đứt',
    solid: 'Vạch liền',
    edge: 'Lề đường',
    arrow: 'Mũi tên',
    crosswalk: 'Zebra',
    stop_line: 'Vạch dừng',
    double_yellow: 'Đôi vàng',
    yellow_solid_dash: 'Vàng liền+đứt',
    fishbone: 'Xương cá',
    stop_bar_double: 'Vạch dừng đôi',
    error_missing_lane: 'Thiếu vạch',
    error_wrong_color: 'Sai màu',
    error_wrong_type: 'Sai loại',
    error_wrong_arrow: 'Mũi tên sai',
    error_offset: 'Lệch vị trí',
  }
  const label = cleanLabels[scene] || labels[scene] || scene
  ctx.save()
  ctx.font = '600 9.5px Inter,sans-serif'
  const tw = ctx.measureText(label).width
  ctx.fillStyle = isError ? 'rgba(239,68,68,0.18)' : 'rgba(45,212,191,0.14)'
  ctx.beginPath()
  if (ctx.roundRect) ctx.roundRect(6, 5, tw + 14, 17, 5)
  else ctx.rect(6, 5, tw + 14, 17)
  ctx.fill()
  ctx.fillStyle = isError ? '#fca5a5' : 'rgba(148,163,184,0.9)'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  ctx.fillText(label, 13, 13.5)
  ctx.restore()
}

// ── Error overlay — skewed with perspective ───────────────────────────────────
function drawErrorOverlay(ctx: CanvasRenderingContext2D, W: number, H: number) {
  // Subtle red tint over entire road
  ctx.save()
  ctx.globalAlpha = 0.05
  ctx.fillStyle = '#ef4444'
  ctx.fillRect(0, 0, W, H)
  ctx.globalAlpha = 1

  // Error badge top-right — skewed slightly with perspective angle
  const bw = 54, bh = 18
  const bx = W - bw - 6, by = 5
  ctx.fillStyle = 'rgba(239,68,68,0.88)'
  ctx.save()
  // slight skew matches road perspective
  ctx.transform(1, 0, -0.04, 1, 0, 0)
  if (ctx.roundRect) ctx.roundRect(bx, by, bw, bh, 6)
  else ctx.rect(bx, by, bw, bh)
  ctx.fill()
  ctx.fillStyle = '#fff'
  ctx.font = 'bold 8px Inter,sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText('✕ LỖI QA', bx + bw / 2, by + bh / 2)
  ctx.restore()
  ctx.restore()
}

export default function LaneCanvas({ data, label, size = 'compact' }: Props) {
  const ref = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const canvasW = size === 'full' ? 440 : size === 'panel' ? 0 : 220
  const canvasH = size === 'full' ? 190 : size === 'panel' ? 155 : 110

  useEffect(() => {
    const canvas = ref.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    const W = canvas.offsetWidth || canvasW || 320
    const H = canvasH
    canvas.width = W * dpr
    canvas.height = H * dpr
    ctx.scale(dpr, dpr)

    const isError = !!data.error
    const scene = data.scene || ''
    const cam = buildCam(data.camera_config, W, H)

    drawRoadSurface(ctx, W, H, cam, isError)

    const pad = 0  // world-space projection handles all coordinates
    const layers: DrawingLayer[] = data.layers?.length
      ? data.layers
      : [{ style: data.style, polyline4d: data.polyline4d }]

    layers.forEach(layer => drawLayerPerspective(ctx, layer, cam, W, H))

    // Arrow head for arrow/wrong_arrow scenes
    if (scene === 'arrow' || scene === 'error_wrong_arrow') {
      const mainLayer = layers.find(l => l.style.opacity > 0.85 && !l.style.dash?.length)
      if (mainLayer && mainLayer.polyline4d.length > 2) {
        // pt[0]=travel, pt[1]=cross
        const projected = mainLayer.polyline4d.map(p => projectWS(p[0], p[1], cam, W, H))
        const lastK = projected[projected.length - 1][2]
        drawArrowHead(ctx, projected, mainLayer.style.color, lastK)
      }
    }

    if (isError) drawErrorOverlay(ctx, W, H)
    if (scene) drawSceneLabel(ctx, scene, isError)

    ctx.globalAlpha = 1
    ctx.shadowBlur = 0
    void pad
  }, [data, canvasW, canvasH, size, label])

  const isError = data.error
  const borderColor = isError ? 'rgba(239,68,68,0.38)' : 'rgba(45,212,191,0.22)'
  const glowShadow = isError
    ? '0 2px 20px rgba(239,68,68,0.18), 0 0 1px rgba(239,68,68,0.4)'
    : '0 2px 20px rgba(45,212,191,0.10), 0 0 1px rgba(45,212,191,0.25)'

  return (
    <div
      ref={containerRef}
      style={{
        borderRadius: 12,
        overflow: 'hidden',
        border: `1px solid ${borderColor}`,
        background: '#11141c',
        boxShadow: glowShadow,
        transition: 'box-shadow 0.3s ease',
      }}
    >
      <canvas
        ref={ref}
        style={{
          display: 'block',
          width: size === 'panel' ? '100%' : canvasW,
          height: canvasH,
        }}
      />
      {(label || data.note) && (
        <div style={{
          padding: '5px 12px',
          fontSize: '0.67rem',
          color: isError ? 'rgba(252,165,165,0.88)' : 'rgba(148,163,184,0.82)',
          borderTop: `1px solid ${borderColor}`,
          lineHeight: 1.5,
          letterSpacing: '0.01em',
        }}>
          {label || data.note}
        </div>
      )}
    </div>
  )
}
