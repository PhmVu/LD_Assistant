import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { useTheme } from '../context/ThemeContext';
import './TopNav.css';

export default function TopNav() {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const location = useLocation();
  const isDark = theme === 'dark';

  const navLinks = [
    { to: '/home', label: 'Trang chủ', matchPrefix: ['/home'] },
    { to: '/about', label: 'Về chúng tôi', matchPrefix: ['/about'] },
  ];

  const isActive = (prefixes: string[]) =>
    prefixes.some(p => location.pathname === p);

  const getDisplayName = (username: string) => {
    if (!username) return '';
    let name = username.trim();
    if (name.startsWith('jr-')) {
      name = name.slice(3);
    }
    if (name.endsWith('-ty')) {
      name = name.slice(0, -3);
    }
    return name;
  };

  return (
    <header className={`ld-topnav ${theme}`}>
      <div className="ld-topnav-left">
        <Link to={user ? "/dashboard" : "/home"} className="ld-topnav-brand">
          <div className="ld-logo-mark">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M3 17l4-8 4 5 3-3 4 6"/>
            </svg>
          </div>
          <span className="ld-logo-text">AI LD</span>
        </Link>
      </div>
      <div className="ld-topnav-right">
        <nav className="ld-topnav-links">
          {navLinks.map(n => (
            <Link
              key={n.to}
              to={n.to}
              className={isActive(n.matchPrefix) ? 'active' : ''}
            >
              {n.label}
            </Link>
          ))}
        </nav>

        {/* Dashboard toggle button shown only when entered into dashboard (not on home page) */}
        {user && location.pathname !== '/home' && (
          <Link to="/dashboard" className="ld-nav-toggle-btn" title="Quay lại Dashboard">
            <span className="ld-nav-toggle-btn-icon">📊</span>
            <span>Dashboard</span>
          </Link>
        )}

        {user ? (
          <div className="ld-user-profile">
            <span className="ld-user-name">{getDisplayName(user.display_name || user.username)}</span>
            <span className="ld-user-role">{user.role}</span>
            <button onClick={logout} className="ld-logout-btn" title="Đăng xuất">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
                <polyline points="16 17 21 12 16 7"/>
                <line x1="21" y1="12" x2="9" y2="12"/>
              </svg>
            </button>
          </div>
        ) : (
          <Link to="/login" className="ld-login-btn-header" title="Đăng nhập">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: '6px' }}>
              <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"></path>
              <polyline points="10 17 15 12 10 7"></polyline>
              <line x1="15" y1="12" x2="3" y2="12"></line>
            </svg>
            <span>Đăng nhập</span>
          </Link>
        )}

        <div className="ld-topnav-divider" style={{ width: '1px', height: '24px', background: 'var(--border-color)', margin: '0 4px' }} />

        <button className="ld-theme-toggle" onClick={toggleTheme} title={isDark ? "Chuyển sang Light Mode" : "Chuyển sang Dark Mode"}>
          {isDark ? (
            /* Dark mode: hiện Moon icon → click sẽ chuyển sang Light */
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="moon-icon">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
            </svg>
          ) : (
            /* Light mode: hiện Sun icon → click sẽ chuyển sang Dark */
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
      </div>
    </header>
  );
}
