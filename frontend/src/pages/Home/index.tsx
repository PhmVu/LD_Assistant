import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTheme } from '../../context/ThemeContext'
import ParticleBackground from '../../components/ParticleBackground'
import './style.css'

const HomePage: React.FC = () => {
  const { theme } = useTheme()
  const navigate = useNavigate()
  const [showMenu, setShowMenu] = useState(false)

  return (
    <div className={`ld-home ${theme}`}>
      <ParticleBackground />

      {/* ── Hero Section ── */}
      <section className="ld-home-hero">
        {/* Background orbs */}
        <div className="ld-orb-bg">
          <div className="ld-orb ld-orb-1" />
          <div className="ld-orb ld-orb-2" />
          <div className="ld-orb ld-orb-3" />
          <div className="ld-orb ld-orb-4" />
        </div>

        <div className="ld-hero-content">
          <div className="ld-hero-badge">
            <span className="ld-badge ld-badge-cyan">🚦 AI-Powered</span>
          </div>

          <h1 className="ld-hero-title">
            <span className="gradient-text">traffic</span>
            {' '}labeling
          </h1>

          <p className="ld-hero-subtitle">
            Dashboard chất lượng annotation thông minh — theo dõi, phân tích và cải thiện chất lượng dữ liệu nhãn giao thông theo thời gian thực.
          </p>

          <div className="ld-hero-actions">
            <div 
              className="ld-start-wrapper"
              onMouseEnter={() => setShowMenu(true)}
              onMouseLeave={() => setShowMenu(false)}
            >
              <button
                className="ld-btn-primary ld-btn-hero-purple"
                onClick={() => setShowMenu(s => !s)}
              >
                <span>🚀 Bắt đầu ngay</span>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ marginLeft: '4px', transform: showMenu ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.25s ease' }}>
                  <polyline points="6 9 12 15 18 9"></polyline>
                </svg>
              </button>

              {showMenu && (
                <>
                  <div className="ld-start-menu">
                    <div className="ld-menu-item" onClick={() => { setShowMenu(false); navigate('/dashboard') }}>
                      <div className="ld-menu-icon" style={{ background: 'rgba(16, 185, 129, 0.15)', color: '#10b981' }}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>
                      </div>
                      <div className="ld-menu-text-content">
                        <div className="ld-menu-title">Dashboard</div>
                        <div className="ld-menu-desc">Quản lý hiệu suất labeler</div>
                      </div>
                    </div>
                    
                    <div className="ld-menu-item" onClick={() => { setShowMenu(false); navigate('/chat') }}>
                      <div className="ld-menu-icon" style={{ background: 'rgba(139, 92, 246, 0.15)', color: '#8b5cf6' }}>
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg>
                      </div>
                      <div className="ld-menu-text-content">
                        <div className="ld-menu-title">AI Chat</div>
                        <div className="ld-menu-desc">Phân tích lỗi & hỗ trợ AI</div>
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Stats */}
        <div className="ld-hero-stats">
          {[
            { icon: '👥', num: '10+', lbl: 'Labelers' },
            { icon: '📋', num: '5K+', lbl: 'Records' },
            { icon: '🎯', num: '93%', lbl: 'Accuracy' },
          ].map((s, i) => (
            <div key={i} className="ld-stat-card" style={{ animationDelay: `${i * 0.1}s` }}>
              <div className="ld-stat-icon">{s.icon}</div>
              <div className="ld-stat-num">{s.num}</div>
              <div className="ld-stat-lbl">{s.lbl}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features Strip ── */}
      <section className="ld-home-features">
        <div className="ld-features-inner">
          <div className="ld-section-label">Tính năng</div>
          <h2 className="ld-section-title">Quản lý chất lượng annotation toàn diện</h2>

          <div className="ld-features-grid">
            {[
              {
                icon: '📊', color: 'cyan',
                title: 'KPI Dashboard',
                desc: 'Số liệu tổng hợp, biểu đồ tăng trưởng theo tuần. Phân tích hiệu suất từng labeler chi tiết.',
              },
              {
                icon: '🤖', color: 'indigo',
                title: 'AI LD',
                desc: 'Chat AI hỗ trợ kiểm tra quy tắc labeling, phân tích lỗi và đề xuất sửa chữa tức thì.',
              },
              {
                icon: '🔍', color: 'teal',
                title: 'QA Tracker',
                desc: 'Theo dõi tỉ lệ lỗi, phân loại lỗi thường gặp và minh họa cách sửa chính xác.',
              },
              {
                icon: '⚡', color: 'violet',
                title: 'Real-time Scan',
                desc: 'Quét dữ liệu tự động, cập nhật số liệu chất lượng ngay khi có annotation mới.',
              },
            ].map((f, i) => (
              <div key={i} className={`ld-feature-box ld-feature-box-${f.color}`} style={{ animationDelay: `${i * 0.08}s` }}>
                <div className="ld-feature-icon">{f.icon}</div>
                <h3 className="ld-feature-title">{f.title}</h3>
                <p className="ld-feature-desc">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  )
}

export default HomePage
