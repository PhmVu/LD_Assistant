import { useRef } from 'react';
import { Link, useLocation } from 'react-router-dom';
import gsap from 'gsap';
import { useGSAP } from '@gsap/react';
import { useTheme } from '../context/ThemeContext';
import './SubNav.css';

gsap.registerPlugin(useGSAP);

interface NavItem {
  label: string;
  to: string;
  icon?: string;
}

export default function SubNav({ items }: { items: NavItem[] }) {
  const location = useLocation();
  const { theme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const indicatorRef = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    if (!containerRef.current || !indicatorRef.current) return;
    const activeEl = containerRef.current.querySelector('.ld-subnav-link.active') as HTMLElement | null;
    if (!activeEl) return;
    gsap.to(indicatorRef.current, {
      x: activeEl.offsetLeft,
      width: activeEl.offsetWidth,
      duration: 0.35,
      ease: 'power2.out',
    });
  }, { dependencies: [location.pathname, items], scope: containerRef });

  return (
    <div className={`ld-subnav-container ${theme}`} ref={containerRef}>
      <nav className="ld-subnav">
        {items.map(item => (
          <Link
            key={item.to}
            to={item.to}
            className={`ld-subnav-link ${location.pathname === item.to ? 'active' : ''}`}
          >
            {item.icon && <span className="ld-subnav-icon">{item.icon}</span>}
            <span className="ld-subnav-label">{item.label}</span>
          </Link>
        ))}
        <div className="ld-subnav-indicator" ref={indicatorRef} />
      </nav>
    </div>
  );
}
