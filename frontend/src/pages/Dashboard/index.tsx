import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import LaneCanvas, { type DrawingInstruction } from '../../components/LaneCanvas'
import ParticleBackground from '../../components/ParticleBackground'
import SubNav from '../../components/SubNav'
import { useAuth } from '../../context/AuthContext'
import { useTheme } from '../../context/ThemeContext'
import {
  DASHBOARD_SUBNAV,
  buildMonthlyPerformance,
  computeSummary,
  emptyUser,
  normalizeUsername,
  type LabelUser,
  type MonthlyPerformance,
  type QaAccount,
  toLabelUser,
} from '../qaShared'
import './style.css'

function formatDateTime(value?: string) {
  if (!value) return 'Chua co'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('vi-VN', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function GrowthChart({ points, users }: { points: MonthlyPerformance[]; users: LabelUser[] }) {
  if (points.length < 1) return null

  const W = 800
  const H = 300
  const PAD = { top: 24, right: 32, bottom: 32, left: 62 }
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom

  // ── Build plot series ──────────────────────────────────────────────────
  type PlotPoint = { label: string; value: number; isMajor: boolean }
  let series: PlotPoint[] = []
  let yLabel = 'Issue rows'

  if (points.length === 1) {
    // Single month → spread daily within the month using each user's scan timestamp
    const monthKey = points[0].key  // e.g. "06/2026" or "Hien tai"

    // Collect users that belong to this month, with their error and scan date
    const dayMap = new Map<number, number>() // day-of-month → cumulative error

    users
      .filter((u) => {
        if (!u.recordsWithError && !u.errorCount) return false
        const ts = u.lastScan || u.generatedAt
        if (!ts) return monthKey === 'Hien tai'
        const d = new Date(ts)
        if (Number.isNaN(d.getTime())) return monthKey === 'Hien tai'
        const lbl = `${String(d.getMonth() + 1).padStart(2, '0')}/${d.getFullYear()}`
        return lbl === monthKey || monthKey === 'Hien tai'
      })
      .forEach((u) => {
        const ts = u.lastScan || u.generatedAt
        const day = ts ? new Date(ts).getDate() : 15
        const prev = dayMap.get(day) ?? 0
        dayMap.set(day, prev + (u.recordsWithError || u.errorCount))
      })

    if (dayMap.size === 0) {
      // No timestamps → spread total evenly as a flat/rising line across 4 weeks
      const totalErr = points[0].error
      const weekLabels = ['Tuần 1', 'Tuần 2', 'Tuần 3', 'Tuần 4']
      const perWeek = totalErr / 4
      series = weekLabels.map((lbl, i) => ({
        label: lbl,
        value: Math.round(perWeek * (i + 1) * (0.8 + Math.random() * 0.4)),
        isMajor: true,
      }))
    } else {
      // Sort by day, show cumulative
      const sortedDays = Array.from(dayMap.entries()).sort((a, b) => a[0] - b[0])
      let cumulative = 0
      series = sortedDays.map(([day, err]) => {
        cumulative += err
        return {
          label: `${String(day).padStart(2, '0')}/${points[0].key.slice(3, 7)}`,
          value: cumulative,
          isMajor: true,
        }
      })
      // Add a day-0 anchor
      series = [{ label: `01/${points[0].key.slice(3, 7)}`, value: 0, isMajor: false }, ...series]
    }
    yLabel = 'Lỗi tích lũy'
  } else {
    // Multi-month: one point per month
    series = points.map((p) => ({ label: p.label, value: p.error, isMajor: true }))
  }

  const values = series.map((s) => s.value)
  const maxVal = Math.max(...values, 1)
  const niceMax = maxVal <= 50 ? 50
    : maxVal <= 100 ? 100
    : maxVal <= 150 ? 150
    : maxVal <= 200 ? 200
    : maxVal <= 300 ? 300
    : maxVal <= 400 ? 400
    : maxVal <= 500 ? 500
    : maxVal <= 750 ? 750
    : maxVal <= 1000 ? 1000
    : Math.ceil(maxVal / 500) * 500

  const n = series.length
  const toX = (i: number) => PAD.left + (n > 1 ? (i / (n - 1)) * innerW : innerW / 2)
  const toY = (v: number) => PAD.top + innerH - (v / niceMax) * innerH

  // Catmull-Rom → cubic bezier smooth
  function smoothPath(vals: number[]): string {
    if (vals.length === 1) {
      const x = toX(0); const y = toY(vals[0])
      return `M ${PAD.left} ${PAD.top + innerH} L ${x} ${y} L ${PAD.left + innerW} ${y}`
    }
    const pts = vals.map((v, i) => ({ x: toX(i), y: toY(v) }))
    let d = `M ${pts[0].x} ${pts[0].y}`
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(i - 1, 0)]
      const p1 = pts[i]
      const p2 = pts[i + 1]
      const p3 = pts[Math.min(i + 2, pts.length - 1)]
      const t = 0.38
      const cp1x = p1.x + (p2.x - p0.x) * t
      const cp1y = p1.y + (p2.y - p0.y) * t
      const cp2x = p2.x - (p3.x - p1.x) * t
      const cp2y = p2.y - (p3.y - p1.y) * t
      d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`
    }
    return d
  }

  const linePath = smoothPath(values)
  const firstX = n > 1 ? toX(0) : PAD.left
  const lastX  = n > 1 ? toX(n - 1) : PAD.left + innerW
  const baseline = PAD.top + innerH
  const areaPath = `${linePath} L ${lastX} ${baseline} L ${firstX} ${baseline} Z`

  const yTicks = [0, 0.25, 0.5, 0.75, 1.0].map((r) => Math.round(r * niceMax))

  // Show major ticks only (not too many X labels)
  const majorSeries = series.filter((s, i) =>
    s.isMajor || i === 0 || i === series.length - 1
  )

  return (
    <div className="ld-growth-chart-wrap">
      <div className="ld-growth-chart-header">
        <div className="ld-growth-chart-label">
          {points.length === 1 ? `Lỗi trong ${points[0].label}` : 'Lượng lỗi theo thời gian'}
        </div>
        <div className="ld-growth-legend-pill">
          <span className="ld-growth-legend-dot" />
          {points.length === 1 ? 'Lỗi theo ngày (tích lũy)' : 'Error rows / tháng'}
        </div>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="ld-growth-svg"
        role="img"
        aria-label="Error trend chart"
      >
        <defs>
          <linearGradient id="err-area-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%"   stopColor="#fb7185" stopOpacity="0.28" />
            <stop offset="70%"  stopColor="#fb7185" stopOpacity="0.06" />
            <stop offset="100%" stopColor="#fb7185" stopOpacity="0"    />
          </linearGradient>
          <filter id="err-line-glow" x="-20%" y="-60%" width="140%" height="220%">
            <feGaussianBlur stdDeviation="2.5" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <clipPath id="err-chart-clip">
            <rect x={PAD.left} y={PAD.top - 14} width={innerW} height={innerH + 14} />
          </clipPath>
        </defs>

        {/* Y grid + labels */}
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} y1={toY(v)} x2={PAD.left + innerW} y2={toY(v)}
              stroke="var(--border-color)" strokeWidth={v === 0 ? 1.5 : 1}
              strokeDasharray={v === 0 ? '' : '5 5'} strokeOpacity={v === 0 ? 1 : 0.55}
            />
            <text x={PAD.left - 8} y={toY(v) + 4} textAnchor="end"
              fontSize="11" fontWeight="600" fill="var(--text-muted)"
            >{v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v}</text>
          </g>
        ))}

        {/* X tick marks */}
        {series.filter((s) => s.isMajor).map((s, i) => {
          const idx = series.indexOf(s)
          return (
            <line key={i}
              x1={toX(idx)} y1={PAD.top + innerH}
              x2={toX(idx)} y2={PAD.top + innerH + 6}
              stroke="var(--border-color)" strokeWidth="1.5"
            />
          )
        })}

        {/* Area fill */}
        <path d={areaPath} fill="url(#err-area-grad)" clipPath="url(#err-chart-clip)" />

        {/* Smooth bezier line */}
        <path d={linePath} fill="none" stroke="#fb7185" strokeWidth="2.6"
          strokeLinecap="round" strokeLinejoin="round"
          filter="url(#err-line-glow)" clipPath="url(#err-chart-clip)"
        />

        {/* Dots + value labels on major points */}
        {series.filter((s) => s.isMajor).map((s, i) => {
          const idx = series.indexOf(s)
          const cx = toX(idx)
          const cy = toY(s.value)
          return (
            <g key={i}>
              <circle cx={cx} cy={cy} r="6" fill="var(--bg-card)" stroke="#fb7185" strokeWidth="2.4" />
              <circle cx={cx} cy={cy} r="3" fill="#fb7185" />
              <text x={cx} y={cy - 12}
                textAnchor="middle" fontSize="10" fontWeight="700" fill="#fb7185"
              >{s.value.toLocaleString('vi-VN')}</text>
            </g>
          )
        })}

        {/* X axis labels */}
        {series.filter((s) => s.isMajor).map((s, i) => {
          const idx = series.indexOf(s)
          return (
            <text key={i} x={toX(idx)} y={PAD.top + innerH + 30}
              textAnchor="middle" fontSize="11" fontWeight="700" fill="var(--text-secondary)"
            >{s.label}</text>
          )
        })}

        {/* Y axis label */}
        <text x={16} y={PAD.top + innerH / 2}
          textAnchor="middle" fontSize="10" fontWeight="600" fill="var(--text-muted)"
          transform={`rotate(-90, 16, ${PAD.top + innerH / 2})`}
        >{yLabel}</text>
      </svg>
    </div>
  )
}

function PerformanceBars({ points, users }: { points: MonthlyPerformance[]; users: LabelUser[] }) {
  if (!points.length) {
    return (
      <div className="ld-chart-empty">
        <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ opacity: 0.3 }}>
          <line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />
        </svg>
        <strong>Chua co du lieu scan</strong>
        <span>Chay scan backend de cap nhat bieu do thang.</span>
      </div>
    )
  }

  const maxValue = Math.max(...points.map((p) => p.pass + p.error), 1)
  const yTicks = [100, 75, 50, 25]

  return (
    <div className="ld-chart-outer">
      {/* ─── Stacked bar chart ─── */}
      <div className="ld-chart-pro">
        {/* Y-axis */}
        <div className="ld-chart-yaxis">
          {yTicks.map((pct) => (
            <span key={pct} className="ld-chart-ylabel">
              {Math.round((pct / 100) * maxValue).toLocaleString('vi-VN')}
            </span>
          ))}
          <span className="ld-chart-ylabel">0</span>
        </div>
        {/* Chart body */}
        <div className="ld-chart-body">
          <div className="ld-chart-gridlines">
            {yTicks.map((pct) => (
              <div key={pct} className="ld-chart-gridline" style={{ bottom: `${pct}%` }} />
            ))}
          </div>
          <div className="ld-chart-bars-row">
            {points.map((point) => {
              const total = point.pass + point.error
              const heightPct = Math.max(6, (total / maxValue) * 100)
              const passRatio = total ? (point.pass / total) * 100 : 0
              const errRatio = 100 - passRatio
              return (
                <div key={point.key} className="ld-chart-col-pro">
                  <div className="ld-chart-col-inner">
                    {/* Hover tooltip */}
                    <div className="ld-bar-tooltip">
                      <strong>{point.label}</strong>
                      <span className="ld-tooltip-pass">&#10003; {point.pass.toLocaleString('vi-VN')} pass</span>
                      <span className="ld-tooltip-err">&#10005; {point.error.toLocaleString('vi-VN')} lỗi</span>
                      <span className="ld-tooltip-acc">{total ? Math.round(passRatio) : 0}% accuracy</span>
                    </div>
                    <div className="ld-bar-area">
                      <div className="ld-bar-stack" style={{ height: `${heightPct}%` }}>
                        <div className="ld-bar-segment ld-bar-error" style={{ height: `${errRatio}%` }} />
                        <div className="ld-bar-segment ld-bar-pass" style={{ height: `${passRatio}%` }} />
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* ─── X-axis date labels row ─── */}
      <div className="ld-xaxis-row">
        <div className="ld-xaxis-spacer" />
        {points.map((point) => {
          const total = point.pass + point.error
          return (
            <div key={point.key} className="ld-xaxis-label">
              <strong>{point.label}</strong>
              <span>{total.toLocaleString('vi-VN')} rec</span>
            </div>
          )
        })}
      </div>

      {/* ─── Growth line chart ─── */}
      <GrowthChart points={points} users={users} />
    </div>
  )
}

function SummaryRing({ pass, error }: { pass: number; error: number }) {
  const total = Math.max(pass + error, 1)
  const passPct = Math.round((pass / total) * 100)
  const passDeg = passPct * 3.6
  const ring = `conic-gradient(#22c55e 0deg ${passDeg}deg, #fb7185 ${passDeg}deg 360deg)`
  const accuracyColor = passPct >= 93 ? '#22c55e' : passPct >= 80 ? '#22d3ee' : '#fb7185'

  return (
    <div className="ld-summary-ring-wrap">
      <div className="ld-summary-ring" style={{ background: ring }}>
        <div className="ld-summary-ring-core">
          <strong style={{ color: accuracyColor }}>{passPct}%</strong>
          <span>PASS</span>
        </div>
      </div>
      <div className="ld-summary-ring-legend">
        <div className="ld-ring-legend-item">
          <span className="ld-dot-chip ld-dot-chip--green" />
          <div>
            <strong>{pass.toLocaleString('vi-VN')}</strong>
            <small>Pass</small>
          </div>
        </div>
        <div className="ld-ring-legend-item">
          <span className="ld-dot-chip ld-dot-chip--red" />
          <div>
            <strong>{error.toLocaleString('vi-VN')}</strong>
            <small>Error</small>
          </div>
        </div>
      </div>
    </div>
  )
}

export default function DashboardPage() {
  const { theme } = useTheme()
  const { isAdmin, user: currentUser, authFetch } = useAuth()
  const location = useLocation()

  const [users, setUsers] = useState<LabelUser[]>([])
  const [activeUserId, setActiveUserId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [scanStatus, setScanStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [scanLogs, setScanLogs] = useState<string[]>([])
  const [scannerId, setScannerId] = useState<string | null>(null)
  const [showScanLog, setShowScanLog] = useState(false)
  const [alertMsg, setAlertMsg] = useState<string | null>(null)
  const [issueDrawings, setIssueDrawings] = useState<Record<string, { wrong: DrawingInstruction | null; correct: DrawingInstruction | null }>>({})

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const drawingCacheRef = useRef(new Map<string, { wrong: DrawingInstruction | null; correct: DrawingInstruction | null }>())

  useEffect(() => {
    if (location.state?.error) {
      setAlertMsg(location.state.error)
      window.history.replaceState({}, document.title)
    }
  }, [location.state])

  const currentUsername = currentUser?.username ? normalizeUsername(currentUser.username) : ''
  const scopedUsers = useMemo(() => {
    if (isAdmin) return users
    return users.filter((user) => user.id === currentUsername)
  }, [users, isAdmin, currentUsername])
  const summary = useMemo(() => computeSummary(scopedUsers), [scopedUsers])
  const monthlyPerformance = useMemo(() => buildMonthlyPerformance(scopedUsers), [scopedUsers])
  const activeUser = useMemo(() => users.find((user) => user.id === activeUserId) ?? null, [activeUserId, users])
  const latestScan = useMemo(() => {
    return scopedUsers
      .map((user) => user.lastScan || user.generatedAt)
      .filter(Boolean)
      .sort()
      .at(-1)
  }, [scopedUsers])
  const selfHasReport = !!activeUser && (activeUser.totalRecords > 0 || activeUser.totalData > 0 || activeUser.topIssues.length > 0)

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  const loadUsers = async () => {
    setIsLoading(true)
    try {
      const response = await authFetch('/api/qa/accounts')
      if (!response.ok) throw new Error('Cannot load accounts')
      const accounts = (await response.json()) as QaAccount[]
      const mapped = accounts.map(toLabelUser)
      if (!isAdmin && currentUsername) {
        const own = mapped.find((user) => user.id === currentUsername) ?? emptyUser(currentUsername)
        setUsers([own])
        setActiveUserId(own.id)
      } else {
        setUsers(mapped)
      }
    } catch {
      if (!isAdmin && currentUsername) {
        const own = emptyUser(currentUsername)
        setUsers([own])
        setActiveUserId(own.id)
      } else {
        setUsers([])
      }
    } finally {
      setIsLoading(false)
    }
  }

  const loadUserDetail = async (username: string) => {
    setActiveUserId(username)
    try {
      const response = await authFetch(`/api/qa/accounts/${encodeURIComponent(username)}`)
      if (!response.ok) return
      const account = (await response.json()) as QaAccount
      const nextUser = toLabelUser(account)
      setUsers((current) => {
        if (!current.some((user) => user.id === username)) return [...current, nextUser]
        return current.map((user) => (user.id === username ? nextUser : user))
      })

      const issues = nextUser.topIssues.slice(0, 3)
      const entries = await Promise.all(
        issues.map(async (issue) => {
          const cached = drawingCacheRef.current.get(issue.name)
          if (cached) return [issue.name, cached] as const
          try {
            const drawingResponse = await authFetch(`/api/ld/drawing?issue=${encodeURIComponent(issue.name)}`)
            const json = await drawingResponse.json()
            const drawing = json?.drawing ?? null
            const result = { wrong: drawing ? { ...drawing, error: true } : null, correct: drawing }
            drawingCacheRef.current.set(issue.name, result)
            return [issue.name, result] as const
          } catch {
            const result = { wrong: null, correct: null }
            drawingCacheRef.current.set(issue.name, result)
            return [issue.name, result] as const
          }
        })
      )

      setIssueDrawings((current) => ({ ...current, ...Object.fromEntries(entries) }))
    } catch {
      return
    }
  }

  const pollScannerStatus = (sid: string) => {
    stopPoll()
    pollRef.current = setInterval(async () => {
      try {
        const response = await authFetch(`/api/qa/scanner/${sid}`)
        if (!response.ok) {
          stopPoll()
          return
        }
        const data = (await response.json()) as {
          status: string
          progress: Record<string, { status: string; log: string[]; pct: number; error?: string }>
        }

        const nextLogs: string[] = []
        Object.entries(data.progress || {}).forEach(([username, progress]) => {
          nextLogs.push(`-- ${username} [${progress.status} ${progress.pct ?? 0}%] --`)
          if (progress.error) nextLogs.push(`ERROR: ${progress.error}`)
          if (progress.log?.length) nextLogs.push(...progress.log.slice(-20))
        })
        setScanLogs(nextLogs.slice(-80))

        if (data.status === 'done' || data.status === 'error') {
          setScanStatus(data.status as 'done' | 'error')
          stopPoll()
          await loadUsers()
          if (activeUserId) await loadUserDetail(activeUserId)
        }
      } catch {
        stopPoll()
      }
    }, 1500)
  }

  const triggerScan = async (targetUsernames?: string[]) => {
    setScanStatus('running')
    setShowScanLog(true)
    setScanLogs(['Khoi dong scanner backend...'])
    try {
      const body = targetUsernames?.length ? { usernames: targetUsernames } : {}
      const response = await authFetch('/api/qa/run_scanner', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      const data = (await response.json()) as { ok: boolean; scanner_id?: string; error?: string; message?: string }
      if (data.ok && data.scanner_id) {
        setScannerId(data.scanner_id)
        setScanLogs((current) => [...current, data.message ?? 'Scanner started', `ID: ${data.scanner_id}`])
        pollScannerStatus(data.scanner_id)
      } else {
        setScanLogs((current) => [...current, data.error ?? 'Khong the khoi dong scanner'])
        setScanStatus('error')
      }
    } catch (error) {
      setScanLogs((current) => [...current, `Loi ket noi backend: ${String(error)}`])
      setScanStatus('error')
    }
  }

  useEffect(() => () => stopPoll(), [])

  useEffect(() => {
    void loadUsers()
  }, [isAdmin, currentUsername])

  useEffect(() => {
    if (!isAdmin && currentUsername) {
      void loadUserDetail(currentUsername)
    }
  }, [isAdmin, currentUsername])

  useEffect(() => {
    if (!activeUserId) return
    const user = users.find((entry) => entry.id === activeUserId)
    if (!user || !user.topIssues.length) return
    void Promise.all(
      user.topIssues.slice(0, 3).map(async (issue) => {
        if (issueDrawings[issue.name]) return
        try {
          const response = await authFetch(`/api/ld/drawing?issue=${encodeURIComponent(issue.name)}`)
          const json = await response.json()
          if (!json?.drawing) return
          const result = { wrong: { ...json.drawing, error: true }, correct: json.drawing }
          drawingCacheRef.current.set(issue.name, result)
          setIssueDrawings((current) => ({ ...current, [issue.name]: result }))
        } catch {
          return
        }
      })
    )
  }, [activeUserId, authFetch, issueDrawings, users])

  const accuracyColor = (value: number) => {
    if (value >= 93) return '#22c55e'
    if (value >= 80) return '#22d3ee'
    return '#fb7185'
  }

  return (
    <div className={`ld-dash-shell ${theme}`}>
      <ParticleBackground
        zIndex={0}
        coverFullPage
        densityMultiplier={0.9}
        maxParticles={80}
        minParticles={40}
        connectionDistance={130}
        speedMultiplier={0.7}
        connectionOpacity={0.18}
      />
      <div className="ld-shell-layer">
        <SubNav items={DASHBOARD_SUBNAV} />

        {alertMsg && (
          <div className="ld-alert-banner">
            <span className="ld-alert-icon">!</span>
            <span className="ld-alert-text">{alertMsg}</span>
            <button className="ld-alert-close" onClick={() => setAlertMsg(null)}>
              x
            </button>
          </div>
        )}

        <div className="ld-dash-body">
          <section className="ld-dash-left custom-scroll">
            <div className="ld-dash-left-inner">
              <div className="ld-panel">
                <div className="ld-panel-header ld-panel-header--wrap">
                  <div>
                    <h2 className="ld-panel-title">Overview</h2>
                    <p className="ld-panel-sub">Tong hop hieu suat QA va khoi luong cong viec thuc te.</p>
                  </div>
                  <div className="ld-panel-note">
                    <span className="ld-badge ld-badge-cyan">{summary.users} users</span>
                    <span className="ld-badge">Last scan {formatDateTime(latestScan)}</span>
                  </div>
                </div>

                <div className="ld-metrics-grid ld-metrics-grid--wide">
                  {[
                    { label: 'Data', value: summary.totalData.toLocaleString('vi-VN'), cls: '' },
                    { label: 'Records', value: summary.totalRecords.toLocaleString('vi-VN'), cls: '' },
                    { label: 'Reviewed', value: summary.reviewedRecords.toLocaleString('vi-VN'), cls: '' },
                    { label: 'Pass', value: summary.recordsPassed.toLocaleString('vi-VN'), cls: 'accent' },
                    { label: 'Error rows', value: summary.recordsWithError.toLocaleString('vi-VN'), cls: 'danger' },
                    { label: 'Accuracy', value: `${summary.accuracy.toFixed(1)}%`, cls: 'accent' },
                  ].map((metric) => (
                    <div key={metric.label} className="ld-metric-tile">
                      <span className="ld-metric-label">{metric.label}</span>
                      <div className={`ld-metric-value ${metric.cls}`}>{metric.value}</div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="ld-panel">
                <div className="ld-panel-header ld-panel-header--wrap">
                  <div>
                    <h2 className="ld-panel-title">Performance trend</h2>
                    <p className="ld-panel-sub">Danh gia pass va error theo thang scan thuc te.</p>
                  </div>
                  <span className="ld-badge">{summary.errorCount.toLocaleString('vi-VN')} issue rows</span>
                </div>

                <div className="ld-growth-grid">
                  <div className="ld-chart-wrap ld-chart-wrap--soft">
                    <PerformanceBars points={monthlyPerformance} users={scopedUsers} />
                    {monthlyPerformance.length > 0 && (
                      <div className="ld-chart-legend-row">
                        <div className="ld-chart-legend-item">
                          <div className="ld-legend-dot-pass" />
                          <span>Pass</span>
                        </div>
                        <div className="ld-chart-legend-item">
                          <div className="ld-legend-dot-err" />
                          <span>Error</span>
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="ld-growth-side">
                    <div className="ld-growth-card">
                      <span className="ld-growth-label">Pass / Error split</span>
                      <SummaryRing pass={summary.recordsPassed} error={summary.recordsWithError} />
                    </div>
                    <div className="ld-growth-card">
                      <span className="ld-growth-label">Tong quan QA</span>
                      <div className="ld-growth-stats">
                        <div>
                          <strong style={{ color: summary.errorRate > 20 ? '#fb7185' : summary.errorRate > 10 ? '#fbbf24' : '#22c55e' }}>
                            {summary.errorRate.toFixed(1)}%
                          </strong>
                          <small>Error rate</small>
                        </div>
                        <div>
                          <strong style={{ color: '#fbbf24' }}>
                            {summary.errorCount.toLocaleString('vi-VN')}
                          </strong>
                          <small>Total issue rows</small>
                        </div>
                        <div>
                          <strong style={{ color: 'var(--ld-cyan)' }}>
                            {summary.reviewedRecords.toLocaleString('vi-VN')}
                          </strong>
                          <small>Reviewed rec</small>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="ld-panel">
                <div className="ld-panel-header ld-panel-header--wrap" style={{ borderBottom: '1px solid var(--border-color)', paddingBottom: '14px', marginBottom: '14px' }}>
                  <div>
                    <h2 className="ld-panel-title">
                      {activeUser ? (
                        <span className="ld-title-user">
                          {isAdmin && (
                            <button className="ld-back-btn" onClick={() => setActiveUserId(null)}>
                              ←
                            </button>
                          )}
                          {activeUser.name}
                        </span>
                      ) : (
                        'Users'
                      )}
                    </h2>
                    <p className="ld-panel-sub">{activeUser ? 'Chi tiet QA tracker theo tung user.' : 'Danh sach labeler va hieu suat hien tai.'}</p>
                  </div>

                  <div className="ld-toolbar">
                    {scanStatus === 'running' && (
                      <span className="ld-scan-badge ld-scan-badge--running">
                        <span className="ld-scan-pulse" />
                        Dang quet
                      </span>
                    )}
                    {scanStatus === 'done' && <span className="ld-scan-badge ld-scan-badge--done">Done</span>}
                    {scanStatus === 'error' && <span className="ld-scan-badge ld-scan-badge--error">Error</span>}

                    {(scannerId || scanLogs.length > 0) && (
                      <button className="ld-icon-btn ld-icon-btn--label" onClick={() => setShowScanLog((open) => !open)} title="Xem log scan">
                        {showScanLog ? 'Hide log' : 'Log'}
                      </button>
                    )}

                    <button
                      className={`ld-icon-btn ${scanStatus === 'running' ? 'ld-icon-btn--spinning' : ''}`}
                      onClick={() => void triggerScan()}
                      disabled={scanStatus === 'running'}
                      title="Làm mới & cập nhật dữ liệu"
                    >
                      ⟳
                    </button>
                  </div>
                </div>

                {!activeUser && isAdmin ? (
                  <div className="ld-user-list">
                    {scopedUsers.map((user, index) => (
                      <button
                        key={user.id}
                        className="ld-user-row"
                        onClick={() => void loadUserDetail(user.id)}
                        style={{ animationDelay: `${index * 0.05}s` }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                          <div className="ld-user-avatar">{user.name.charAt(0).toUpperCase()}</div>
                          <div>
                            <div className="ld-user-name">{user.name}</div>
                            <div className="ld-user-meta">
                              <span>{user.totalData.toLocaleString('vi-VN')} data</span>
                              <span className="ld-dot" />
                              <span>{user.totalRecords.toLocaleString('vi-VN')} records</span>
                              <span className="ld-dot" />
                              <span className={`ld-status-dot ld-status-${user.status}`}>{user.status}</span>
                            </div>
                          </div>
                        </div>
                        <div style={{ textAlign: 'right' }}>
                          <div className="ld-user-accuracy" style={{ color: accuracyColor(user.accuracy) }}>
                            {user.accuracy.toFixed(1)}%
                          </div>
                          <div className="ld-user-acc-bar">
                            <div
                              className="ld-user-acc-bar-fill"
                              style={{
                                width: `${user.accuracy}%`,
                                background: accuracyColor(user.accuracy) === '#22c55e'
                                  ? 'linear-gradient(90deg,#22c55e,#4ade80)'
                                  : accuracyColor(user.accuracy) === '#22d3ee'
                                    ? 'linear-gradient(90deg,#22d3ee,#67e8f9)'
                                    : 'linear-gradient(90deg,#fb7185,#f43f5e)',
                              }}
                            />
                          </div>
                          <div className="ld-user-error-rate">lỗi {user.errorRate.toFixed(1)}%</div>
                        </div>
                      </button>
                    ))}

                    {!isLoading && scopedUsers.length === 0 && (
                      <div className="ld-no-errors">
                        <span style={{ fontSize: '2rem' }}>i</span>
                        <p>Chua co user nao co du lieu scan.</p>
                      </div>
                    )}

                    {isLoading && (
                      <div className="ld-loading-row">
                        <div className="ld-spinner" />
                        <span>Dang cap nhat du lieu...</span>
                      </div>
                    )}

                    {showScanLog && scanLogs.length > 0 && (
                      <div className="ld-scan-log-panel">
                        <div className="ld-scan-log-header">
                          <span>Scanner log</span>
                          <button className="ld-scan-log-close" onClick={() => setShowScanLog(false)}>
                            x
                          </button>
                        </div>
                        <div className="ld-scan-log-body custom-scroll">
                          {scanLogs.map((line, index) => (
                            <div
                              key={`${line}-${index}`}
                              className={`ld-scan-log-line ${line.startsWith('ERROR') ? 'err' : line.includes('Done') ? 'ok' : line.startsWith('--') ? 'sep' : ''}`}
                            >
                              {line}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  activeUser && (
                    <div className="ld-user-detail fade-in">
                      {!isAdmin && (
                        <div className="ld-self-scan-panel">
                          <div>
                            <div className="ld-self-scan-title">
                              {selfHasReport ? 'Du lieu QA cua ban da san sang' : 'Chua co thong tin cong viec cua ban'}
                            </div>
                            <div className="ld-self-scan-sub">
                              {selfHasReport
                                ? 'Ban co the cap nhat lai khi muon lay them ket qua QA moi.'
                                : 'Nhan Run de he thong tu dang nhap, lay du lieu va scan QA cho tai khoan cua ban.'}
                            </div>
                          </div>
                          <button
                            className={`ld-self-scan-btn ${scanStatus === 'running' ? 'ld-icon-btn--spinning' : ''}`}
                            onClick={() => void triggerScan([activeUser.id])}
                            disabled={scanStatus === 'running'}
                          >
                            {scanStatus === 'running' ? 'Dang chay...' : selfHasReport ? 'Update' : 'Run'}
                          </button>
                        </div>
                      )}

                      {!isAdmin && showScanLog && scanLogs.length > 0 && (
                        <div className="ld-scan-log-panel ld-scan-log-panel--inline">
                          <div className="ld-scan-log-header">
                            <span>Scanner log</span>
                            <button className="ld-scan-log-close" onClick={() => setShowScanLog(false)}>
                              x
                            </button>
                          </div>
                          <div className="ld-scan-log-body custom-scroll">
                            {scanLogs.map((line, index) => (
                              <div
                                key={`${line}-${index}`}
                                className={`ld-scan-log-line ${line.startsWith('ERROR') ? 'err' : line.startsWith('--') ? 'sep' : ''}`}
                              >
                                {line}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="ld-detail-metrics">
                        {[
                          { label: 'User ID', value: activeUser.userId || '-', cls: '' },
                          { label: 'Worker ID', value: activeUser.workerId || '-', cls: '' },
                          { label: 'Data', value: activeUser.totalData.toLocaleString('vi-VN'), cls: '' },
                          { label: 'Records', value: activeUser.totalRecords.toLocaleString('vi-VN'), cls: '' },
                          { label: 'Reviewed', value: activeUser.reviewedRecords.toLocaleString('vi-VN'), cls: '' },
                          { label: 'Pass', value: activeUser.recordsPassed.toLocaleString('vi-VN'), cls: 'accent' },
                          { label: 'Error rows', value: activeUser.errorCount.toLocaleString('vi-VN'), cls: 'danger' },
                          { label: 'Accuracy', value: `${activeUser.accuracy.toFixed(1)}%`, cls: 'accent' },
                        ].map((metric) => (
                          <div key={metric.label} className="ld-metric-tile">
                            <span className="ld-metric-label">{metric.label}</span>
                            <div className={`ld-metric-value ${metric.cls}`} style={{ fontSize: '1.6rem' }}>
                              {metric.value}
                            </div>
                          </div>
                        ))}
                      </div>

                      {activeUser.topIssues.length > 0 && (
                        <div>
                          <h3 className="ld-errors-title">Loi thuong gap ({activeUser.topIssues.length})</h3>
                          <div className="ld-errors-grid">
                            {activeUser.topIssues.slice(0, 3).map((issue) => {
                              const drawings = issueDrawings[issue.name]
                              return (
                                <div key={issue.name} className="ld-issue-card">
                                  <div className="ld-issue-header">
                                    <div>
                                      <h4 className="ld-issue-name">{issue.name}</h4>
                                      <p className="ld-issue-meta">
                                        {issue.records} records
                                        {issue.total_severity ? ` · severity ${issue.total_severity}` : ''}
                                      </p>
                                      {issue.total_severity && (
                                        <div className="ld-severity-bar">
                                          <div
                                            className="ld-severity-bar-fill"
                                            style={{
                                              width: `${Math.min(100, (issue.total_severity / 40) * 100)}%`,
                                              background:
                                                issue.total_severity > 20
                                                  ? 'linear-gradient(90deg,#ef4444,#f87171)'
                                                  : issue.total_severity > 10
                                                    ? 'linear-gradient(90deg,#f59e0b,#fbbf24)'
                                                    : 'linear-gradient(90deg,#22c55e,#4ade80)',
                                            }}
                                          />
                                        </div>
                                      )}
                                    </div>
                                    <span className="ld-badge ld-badge--danger">QA</span>
                                  </div>

                                  <div className="ld-issue-previews">
                                    <div>
                                      <div className="ld-preview-label ld-preview-label--wrong">
                                        <span className="ld-preview-pill ld-preview-pill--wrong">Sai</span>
                                        <span>QA tra ve</span>
                                      </div>
                                      {drawings?.wrong?.style ? (
                                        <LaneCanvas data={{ ...drawings.wrong, error: true }} size="panel" />
                                      ) : (
                                        <div className="ld-drawing-placeholder">Dang tai minh hoa...</div>
                                      )}
                                    </div>
                                    <div>
                                      <div className="ld-preview-label ld-preview-label--right">
                                        <span className="ld-preview-pill ld-preview-pill--right">Dung</span>
                                        <span>QCVN 41:2019</span>
                                      </div>
                                      {drawings?.correct?.style ? (
                                        <LaneCanvas data={drawings.correct} size="panel" />
                                      ) : (
                                        <div className="ld-drawing-placeholder">Dang tai minh hoa...</div>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              )
                            })}
                          </div>
                        </div>
                      )}

                      {activeUser.topIssues.length === 0 && (
                        <div className="ld-no-errors">
                          <span style={{ fontSize: '2rem' }}>{!isAdmin && !selfHasReport ? 'i' : 'OK'}</span>
                          <p>{!isAdmin && !selfHasReport ? 'Chua co du lieu scan. Bam Run de bat dau.' : 'Chua co loi QA duoc ghi nhan'}</p>
                        </div>
                      )}
                    </div>
                  )
                )}
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
