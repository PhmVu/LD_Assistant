import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTheme } from '../../context/ThemeContext'
import ParticleBackground from '../../components/ParticleBackground'
import './style.css'

const tabs = [
  { id: 'ai', label: 'AI LD', icon: '🤖' },
  { id: 'dashboard', label: 'Dashboard', icon: '📊' },
]

const AboutPage: React.FC = () => {
  const { theme } = useTheme()
  const [activeTab, setActiveTab] = useState('ai')
  const [scrolled, setScrolled] = useState(false)

  React.useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20)
    }
    window.addEventListener('scroll', handleScroll)
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  return (
    <div className={`ld-about ${theme}`}>
      <ParticleBackground densityMultiplier={0.6} />

      {/* Tab bar */}
      <div className={`ld-about-tabs ${scrolled ? 'scrolled' : ''}`}>
        {tabs.map(t => (
          <button
            key={t.id}
            className={`ld-about-tab${activeTab === t.id ? ' active' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >
            <span>{t.icon}</span>
            <span>{t.label}</span>
          </button>
        ))}
      </div>

      {/* Content */}
      <main className="ld-about-main">
        {/* ── AI LD Tab ── */}
        {activeTab === 'ai' && (
          <section className="ld-about-section fade-in">
            <div className="ld-orb-bg">
              <div className="ld-orb ld-orb-2" /><div className="ld-orb ld-orb-4" />
            </div>
            <div className="ld-about-inner">
              <div className="ld-about-hero">
                <span className="ld-badge ld-badge-violet" style={{ fontSize: '0.8rem', padding: '6px 14px' }}>
                  🤖 Autonomous Intelligence
                </span>
                <h1 className="ld-about-title">
                  <span className="gradient-text">AI LD</span>
                </h1>
                <p className="ld-about-desc">
                  Trợ lý AI tích hợp — phân tích lỗi labeling, kiểm tra quy tắc annotation và đề xuất sửa chữa thông minh trực tiếp trong dashboard.
                </p>
              </div>

              <div className="ld-about-features-grid">
                {[
                  {
                    color: 'cyan', icon: '💬',
                    title: 'Chat thông minh',
                    desc: 'Hỏi bất kỳ câu hỏi nào về quy tắc labeling lane, road edge, hay turn arrow. AI trả lời tức thì dựa trên guidelines.',
                  },
                  {
                    color: 'indigo', icon: '🔍',
                    title: 'Phân tích lỗi',
                    desc: 'Upload ảnh annotation — AI nhận diện lỗi sai, giải thích nguyên nhân và chỉ ra cách sửa chuẩn xác.',
                  },
                  {
                    color: 'teal', icon: '🎯',
                    title: 'QA Coaching',
                    desc: 'Xem lại lịch sử lỗi của từng labeler, AI tổng hợp pattern lỗi thường gặp và gợi ý cải thiện cụ thể.',
                  },
                  {
                    color: 'violet', icon: '⚡',
                    title: 'Real-time Support',
                    desc: 'Hỗ trợ trong lúc annotate — kiểm tra tính hợp lệ của nhãn ngay lập tức, không cần chờ QA review.',
                  },
                ].map((f, i) => (
                  <div key={i} className={`ld-feature-box ld-feature-box-${f.color}`}>
                    <div className="ld-feature-icon">{f.icon}</div>
                    <h3 className="ld-feature-title">{f.title}</h3>
                    <p className="ld-feature-desc">{f.desc}</p>
                  </div>
                ))}
              </div>

              {/* Stats row */}
              <div className="ld-about-stats">
                {[
                  { val: '<1s', lbl: 'Thời gian phản hồi' },
                  { val: '95%', lbl: 'Độ chính xác phân tích' },
                  { val: '∞', lbl: 'Câu hỏi không giới hạn' },
                  { val: '24/7', lbl: 'Sẵn sàng hoạt động' },
                ].map((s, i) => (
                  <div key={i} className="ld-about-stat">
                    <div className="ld-about-stat-val gradient-text">{s.val}</div>
                    <div className="ld-about-stat-lbl">{s.lbl}</div>
                  </div>
                ))}
              </div>

              <div style={{ display: 'flex', justifyContent: 'center', marginTop: '2.5rem' }}>
                <Link to="/dashboard" className="ld-btn-primary" style={{ fontSize: '1rem', padding: '15px 36px' }}>
                  <span>🚀</span><span>Mở Dashboard với AI Chat</span>
                </Link>
              </div>
            </div>
          </section>
        )}

        {/* ── Dashboard Tab ── */}
        {activeTab === 'dashboard' && (
          <section className="ld-about-section fade-in">
            <div className="ld-orb-bg">
              <div className="ld-orb ld-orb-1" /><div className="ld-orb ld-orb-3" />
            </div>
            <div className="ld-about-inner">
              <div className="ld-about-hero">
                <span className="ld-badge ld-badge-cyan" style={{ fontSize: '0.8rem', padding: '6px 14px' }}>
                  📊 Analytics & Tracking
                </span>
                <h1 className="ld-about-title">
                  <span className="gradient-text">Dashboard</span>
                </h1>
                <p className="ld-about-desc">
                  Trung tâm phân tích chất lượng annotation — theo dõi KPI tổng quan, biểu đồ tăng trưởng và hiệu suất chi tiết từng labeler.
                </p>
              </div>

              <div className="ld-about-features-grid">
                {[
                  {
                    color: 'cyan', icon: '📈',
                    title: 'Biểu đồ Plotly',
                    desc: 'Biểu đồ cột tăng trưởng tuần — visualize số record pass và returned rõ ràng, trực quan.',
                  },
                  {
                    color: 'teal', icon: '👥',
                    title: 'User List',
                    desc: 'Danh sách labeler với accuracy % trực tiếp. Click vào để xem chi tiết đầy đủ từng người.',
                  },
                  {
                    color: 'indigo', icon: '🗂️',
                    title: 'QA Tracker tích hợp',
                    desc: 'Xem chi tiết tổng data, records, tỉ lệ lỗi và top 3 lỗi thường gặp của từng labeler.',
                  },
                  {
                    color: 'violet', icon: '🎨',
                    title: 'Drawing Preview',
                    desc: 'Minh họa trực quan Wrong vs Fixed — AI vẽ ví dụ sai và cách sửa cho từng loại lỗi.',
                  },
                ].map((f, i) => (
                  <div key={i} className={`ld-feature-box ld-feature-box-${f.color}`}>
                    <div className="ld-feature-icon">{f.icon}</div>
                    <h3 className="ld-feature-title">{f.title}</h3>
                    <p className="ld-feature-desc">{f.desc}</p>
                  </div>
                ))}
              </div>

              {/* Layout preview */}
              <div className="ld-layout-preview">
                <h3 className="ld-layout-title">Cấu trúc giao diện & Điều hướng</h3>
                <div className="ld-layout-diagram" style={{ flexDirection: 'column', gap: '8px' }}>
                  <div className="ld-layout-block" style={{ height: '38px', background: 'rgba(34,211,238,0.12)', borderColor: 'rgba(34,211,238,0.3)', justifyContent: 'center' }}>
                    🌐 TopNav (Menu chính: Trang chủ, Giới thiệu, Dashboard)
                  </div>
                  <div className="ld-layout-block" style={{ height: '34px', background: 'rgba(45,212,191,0.1)', borderColor: 'rgba(45,212,191,0.25)', justifyContent: 'center', fontSize: '0.72rem' }}>
                    🗂️ SubNav (Thanh tab phụ: 📊 Dashboard | 💬 Chat AI | 📚 Tri thức | 📈 Hiệu suất)
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 0.8fr', gap: '8px', width: '100%' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div className="ld-layout-block" style={{ height: '44px', background: 'rgba(255,255,255,0.03)', borderColor: 'var(--border-color)' }}>📊 Overview & KPI (Chỉ số tổng quan)</div>
                      <div className="ld-layout-block" style={{ height: '64px', background: 'rgba(255,255,255,0.03)', borderColor: 'var(--border-color)' }}>📈 Performance trend (Cột + Đường tăng trưởng)</div>
                      <div className="ld-layout-block" style={{ height: '54px', background: 'rgba(255,255,255,0.03)', borderColor: 'var(--border-color)' }}>👥 Users List (Danh sách học viên & hiệu suất)</div>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                      <div className="ld-layout-block" style={{ height: '84px', background: 'rgba(129,140,248,0.08)', borderColor: 'rgba(129,140,248,0.25)', flexDirection: 'column', justifyContent: 'center', gap: '4px', textAlign: 'center', padding: '10px' }}>
                        <strong style={{ color: 'var(--ld-indigo)' }}>🍩 Pass/Error Split</strong>
                        <small style={{ color: 'var(--text-muted)', fontSize: '0.62rem', fontWeight: 500 }}>Vòng tròn tỉ lệ lỗi của QA</small>
                      </div>
                      <div className="ld-layout-block" style={{ height: '84px', background: 'rgba(192,132,252,0.08)', borderColor: 'rgba(192,132,252,0.2)', flexDirection: 'column', justifyContent: 'center', gap: '4px', textAlign: 'center', padding: '10px' }}>
                        <strong style={{ color: 'var(--ld-violet)' }}>💬 Chat AI Tab</strong>
                        <small style={{ color: 'var(--text-muted)', fontSize: '0.62rem', fontWeight: 500 }}>Hỏi đáp quy tắc riêng biệt</small>
                      </div>
                    </div>
                  </div>
                </div>
                <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: '14px', textAlign: 'center' }}>
                  Giao diện phân vùng thông minh: Cố định các thanh điều hướng trên cùng, chỉ cuộn phần nội dung bên dưới.
                </p>
              </div>

              <div style={{ display: 'flex', justifyContent: 'center', marginTop: '2.5rem' }}>
                <Link to="/dashboard" className="ld-btn-primary" style={{ fontSize: '1rem', padding: '15px 36px' }}>
                  <span>📊</span><span>Mở Dashboard</span>
                </Link>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  )
}

export default AboutPage
