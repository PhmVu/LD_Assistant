import React, { useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import gsap from 'gsap';
import { useGSAP } from '@gsap/react';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';
import ParticleBackground from '../../components/ParticleBackground';
import './style.css';

gsap.registerPlugin(useGSAP);

const EMPTY_USERNAME_MESSAGE = 'Hãy nhập username của bạn';
const INVALID_LD_MEMBER_MESSAGE = 'Bạn không phải là thành viên LD, bạn không thể đăng nhập';

async function readJsonSafely(response: Response): Promise<any> {
  const text = await response.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}

export default function LoginPage() {
  const [usernameInput, setUsernameInput] = useState('');
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [loading, setLoading] = useState(false);

  const { login } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const containerRef = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
    tl.fromTo('.brand-title', { y: 40, opacity: 0 }, { y: 0, opacity: 1, duration: 0.7 })
      .fromTo('.feature-item', { x: -30, opacity: 0 }, { x: 0, opacity: 1, stagger: 0.15 }, '-=0.4')
      .fromTo('.login-form-container', { x: 40, opacity: 0 }, { x: 0, opacity: 1, duration: 0.6 }, '-=0.5');
  }, { scope: containerRef });

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');

    const rawUsername = usernameInput.trim();
    if (!rawUsername) {
      setError(EMPTY_USERNAME_MESSAGE);
      return;
    }

    setLoading(true);

    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: rawUsername }),
      });
      const data = await readJsonSafely(response);

      if (!response.ok) {
        throw new Error(data.detail || INVALID_LD_MEMBER_MESSAGE);
      }
      if (data?.ok === false) {
        throw new Error(data.detail || INVALID_LD_MEMBER_MESSAGE);
      }
      if (!data?.token || !data?.user) {
        throw new Error(INVALID_LD_MEMBER_MESSAGE);
      }

      login(data.token, data.user);
      setSuccess('Đăng nhập thành công. Đang chuyển hướng...');

      const from = (location.state as any)?.from?.pathname || '/dashboard';
      setTimeout(() => navigate(from, { replace: true }), 700);
    } catch (err: any) {
      setError(err?.message || INVALID_LD_MEMBER_MESSAGE);
      setLoading(false);
    }
  };

  return (
    <div className={`login-page-wrapper ${theme}`} ref={containerRef}>
      <ParticleBackground zIndex={0} densityMultiplier={0.7} />

      <Link to="/home" className="back-to-home-btn" aria-label="Back to Home">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ marginRight: '4px' }}>
          <line x1="19" y1="12" x2="5" y2="12" />
          <polyline points="12 19 5 12 12 5" />
        </svg>
        <span>Back</span>
      </Link>

      <button className="login-theme-toggle" onClick={toggleTheme} aria-label="Toggle Theme" title={theme === 'dark' ? 'Chuyển sang Light Mode' : 'Chuyển sang Dark Mode'}>
        {theme === 'dark' ? (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="moon-icon">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
          </svg>
        ) : (
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="sun-icon">
            <circle cx="12" cy="12" r="5"></circle>
            <line x1="12" y1="1" x2="12" y2="3"></line>
            <line x1="12" y1="21" x2="12" y2="23"></line>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
            <line x1="1" y1="12" x2="3" y2="12"></line>
            <line x1="21" y1="12" x2="23" y2="12"></line>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
          </svg>
        )}
      </button>

      <div className="hero-background">
        <div className="gradient-orb orb-1" />
        <div className="gradient-orb orb-2" />
        <div className="gradient-orb orb-3" />
        <div className="gradient-orb orb-4" />
        <div className="gradient-orb orb-5" />
      </div>

      <div className="login-split-container">
        <div className="login-left-panel">
          <div className="branding-content">
            <h1 className="brand-title">
              Lane Design <span className="brand-highlight">Intelligence</span>
            </h1>

            <div className="feature-list">
              <div className="feature-item">
                <div className="feature-bullet">
                  <span className="bullet-circle" />
                </div>
                <div className="feature-text">
                  <h3>QA Analytics</h3>
                  <p>Thống kê chất lượng và tỷ lệ lỗi annotation trực quan</p>
                </div>
              </div>

              <div className="feature-item">
                <div className="feature-bullet">
                  <span className="bullet-circle" />
                </div>
                <div className="feature-text">
                  <h3>AI LD</h3>
                  <p>Tự động phân tích lỗi vạch đường và đề xuất cách sửa</p>
                </div>
              </div>

              <div className="feature-item">
                <div className="feature-bullet">
                  <span className="bullet-circle" />
                </div>
                <div className="feature-text">
                  <h3>Knowledge Tree</h3>
                  <p>Hệ thống hướng dẫn và quy tắc dán nhãn chuẩn xác</p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="login-right-panel">
          <div className="login-form-container">
            <div className="form-header">
              <h2>Welcome Back</h2>
              <p>Chỉ cần nhập username LD, hệ thống sẽ tự xác thực ngầm</p>
            </div>

            <form className="modern-form" onSubmit={handleLogin} autoComplete="off">
              <div className="input-group-modern">
                <label htmlFor="username">Username</label>
                <div className="input-wrapper">
                  <input
                    type="text"
                    id="username"
                    name="username"
                    placeholder="Ví dụ: nguyenthanhtuan"
                    value={usernameInput}
                    onChange={(e) => setUsernameInput(e.target.value)}
                  />
                </div>
              </div>

              {error && <div className="status-message error">{error}</div>}
              {success && <div className="status-message success">{success}</div>}

              <button type="submit" className="btn-modern-submit" disabled={loading}>
                {loading ? 'Đang xác thực...' : 'Đăng nhập'}
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
