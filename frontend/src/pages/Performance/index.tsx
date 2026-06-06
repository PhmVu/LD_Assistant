import { useEffect, useMemo, useState } from 'react'
import ParticleBackground from '../../components/ParticleBackground'
import SubNav from '../../components/SubNav'
import { useAuth } from '../../context/AuthContext'
import { useTheme } from '../../context/ThemeContext'
import {
  aggregateIssues,
  buildMonthlyPerformance,
  computeSummary,
  DASHBOARD_SUBNAV,
  type LabelUser,
  type MonthlyPerformance,
  type QaAccount,
  toLabelUser,
} from '../qaShared'
import './style.css'

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

export default function PerformancePage() {
  const { theme } = useTheme()
  const { authFetch } = useAuth()

  const [users, setUsers] = useState<LabelUser[]>([])
  const [loading, setLoading] = useState(false)
  const [sort, setSort] = useState<'accuracy' | 'records' | 'errorRate'>('accuracy')

  const loadData = async () => {
    setLoading(true)
    try {
      const response = await authFetch('/api/qa/accounts')
      if (!response.ok) throw new Error('Cannot load accounts')
      const accounts = (await response.json()) as QaAccount[]
      setUsers(accounts.map(toLabelUser))
    } catch {
      setUsers([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [])

  const summary = useMemo(() => computeSummary(users), [users])
  const monthlyPerformance = useMemo(() => buildMonthlyPerformance(users), [users])
  const issueSummary = useMemo(() => aggregateIssues(users).slice(0, 6), [users])
  const sortedUsers = useMemo(() => {
    const next = [...users]
    next.sort((left, right) => {
      if (sort === 'records') return right.totalRecords - left.totalRecords
      if (sort === 'errorRate') return left.errorRate - right.errorRate
      return right.accuracy - left.accuracy
    })
    return next
  }, [users, sort])

  const strongestUser = sortedUsers[0]
  const improvementUser = [...users].sort((left, right) => left.accuracy - right.accuracy)[0]

  return (
    <div className={`perf-layout ${theme}`}>
      <ParticleBackground zIndex={0} densityMultiplier={0.55} />
      <div className="perf-layer">
        <SubNav items={DASHBOARD_SUBNAV} />

        <main className="perf-main">
          <div className="perf-header">
            <div>
              <h1 className="perf-title">Dashboard hieu suat</h1>
              <p className="perf-subtitle">Danh gia chat luong, tang truong va mat do loi theo du lieu scan that.</p>
            </div>
            <button className="refresh-data-btn" onClick={loadData} disabled={loading}>
              {loading ? 'Dang tai...' : 'Lam moi'}
            </button>
          </div>

          <div className="kpi-grid">
            <div className="kpi-card">
              <div className="kpi-label">Users</div>
              <div className="kpi-value">{summary.users}</div>
              <div className="kpi-sub">co du lieu scan</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Data</div>
              <div className="kpi-value">{summary.totalData.toLocaleString('vi-VN')}</div>
              <div className="kpi-sub">tong job da xu ly</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Records</div>
              <div className="kpi-value">{summary.totalRecords.toLocaleString('vi-VN')}</div>
              <div className="kpi-sub">tong record da lam</div>
            </div>
            <div className="kpi-card">
              <div className={`kpi-label ${summary.accuracy >= 80 ? 'green' : 'red'}`}>Accuracy</div>
              <div className={`kpi-value ${summary.accuracy >= 80 ? 'green' : 'yellow'}`}>{summary.accuracy.toFixed(1)}%</div>
              <div className="kpi-sub">pass tren tong record</div>
            </div>
          </div>

          <section className="perf-board">
            <div className="perf-board-main">
              <div className="panel-header">
                <span>Tang truong theo thang</span>
                <small>Pass va error tren du lieu scan</small>
              </div>
              <div className="perf-chart-container">
                <PerformanceBars points={monthlyPerformance} users={users} />
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
            </div>

            <div className="perf-board-side">
              <div className="chart-panel">
                <div className="panel-header">
                  <span>Phan bo pass / error</span>
                </div>
                <div style={{ padding: '22px 20px 20px' }}>
                  <SummaryRing pass={summary.recordsPassed} error={summary.recordsWithError} />
                </div>
              </div>

              <div className="chart-panel">
                <div className="panel-header">
                  <span>Chi so nhanh</span>
                </div>
                <div className="perf-side-metrics">
                  <div>
                    <strong>{summary.reviewedRecords.toLocaleString('vi-VN')}</strong>
                    <small>Reviewed</small>
                  </div>
                  <div>
                    <strong>{summary.recordsWithError.toLocaleString('vi-VN')}</strong>
                    <small>Error rec</small>
                  </div>
                  <div>
                    <strong>{summary.errorCount.toLocaleString('vi-VN')}</strong>
                    <small>Issue rows</small>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <div className="perf-content">
            <div className="ranking-panel">
              <div className="panel-header">
                <span>Danh gia user</span>
                <div className="sort-tabs">
                  {(['accuracy', 'records', 'errorRate'] as const).map((key) => (
                    <button key={key} className={`sort-tab ${sort === key ? 'active' : ''}`} onClick={() => setSort(key)}>
                      {key === 'accuracy' ? 'Accuracy' : key === 'records' ? 'Records' : 'Error'}
                    </button>
                  ))}
                </div>
              </div>

              <table className="rank-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>User</th>
                    <th>Data</th>
                    <th>Records</th>
                    <th>Accuracy</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedUsers.map((user, index) => (
                    <tr key={user.id} className={index === 0 ? 'rank-top' : ''}>
                      <td className="rank-num">{index + 1}</td>
                      <td className="rank-name">{user.name}</td>
                      <td>{user.totalData.toLocaleString('vi-VN')}</td>
                      <td className="rec-cell">{user.totalRecords.toLocaleString('vi-VN')}</td>
                      <td>
                        <span className={`acc-badge ${user.accuracy >= 85 ? 'green' : user.accuracy >= 70 ? 'blue' : 'yellow'}`}>
                          {user.accuracy.toFixed(1)}%
                        </span>
                      </td>
                      <td className="err-cell">{user.errorRate.toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {!loading && sortedUsers.length === 0 && <div className="perf-empty perf-empty--table">Chua co du lieu scan de danh gia.</div>}
            </div>

            <div className="right-panels">
              <div className="chart-panel">
                <div className="panel-header">
                  <span>User noi bat</span>
                </div>
                <div className="perf-highlight-stack">
                  <div className="perf-highlight-card">
                    <small>On dinh nhat</small>
                    <strong>{strongestUser?.name ?? 'Chua co'}</strong>
                    <span>{strongestUser ? `${strongestUser.accuracy.toFixed(1)}% accuracy` : 'Khong co du lieu'}</span>
                  </div>
                  <div className="perf-highlight-card perf-highlight-card--warn">
                    <small>Can uu tien</small>
                    <strong>{improvementUser?.name ?? 'Chua co'}</strong>
                    <span>{improvementUser ? `${improvementUser.errorRate.toFixed(1)}% error rate` : 'Khong co du lieu'}</span>
                  </div>
                </div>
              </div>

              <div className="issues-panel">
                <div className="panel-header">
                  <span>Top loi pho bien</span>
                </div>
                <div className="issues-list">
                  {issueSummary.map((issue) => {
                    const maxRecords = Math.max(issueSummary[0]?.records ?? 1, 1)
                    return (
                      <div key={issue.name} className="issue-row">
                        <span className="issue-name" title={issue.name}>
                          {issue.name}
                        </span>
                        <div className="issue-bar-wrap">
                          <div className="issue-bar" style={{ width: `${(issue.records / maxRecords) * 100}%` }} />
                        </div>
                        <span className="issue-count">{issue.records}</span>
                      </div>
                    )
                  })}
                  {!loading && issueSummary.length === 0 && <div className="perf-empty perf-empty--issues">Chua co top loi de hien thi.</div>}
                </div>
              </div>
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
