import React, { useRef } from 'react';
import { useLocation } from 'react-router-dom';
import gsap from 'gsap';
import { useGSAP } from '@gsap/react';

gsap.registerPlugin(useGSAP);

export default function GSAPPageTransition({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const wrapperRef = useRef<HTMLDivElement>(null);

  useGSAP(() => {
    if (!wrapperRef.current) return;
    
    gsap.fromTo(wrapperRef.current,
      { opacity: 0, y: 15, filter: 'blur(4px)' },
      { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.4, ease: 'power2.out' }
    );
  }, { dependencies: [location.pathname], scope: wrapperRef });

  return (
    <div ref={wrapperRef} style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
      {children}
    </div>
  );
}
