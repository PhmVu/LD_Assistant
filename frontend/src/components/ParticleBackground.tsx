import React, { useEffect, useRef } from 'react';
import { useTheme } from '../context/ThemeContext';

interface ParticleBackgroundProps {
  densityMultiplier?: number;
  minParticles?: number;
  maxParticles?: number;
  connectionDistance?: number;
  speedMultiplier?: number;
  zIndex?: number;
  ambientGlow?: boolean;
  enableConnections?: boolean;
  connectionOpacity?: number;
  coverFullPage?: boolean;
}

const ParticleBackground: React.FC<ParticleBackgroundProps> = ({
  densityMultiplier = 1,
  minParticles = 20,
  maxParticles = 50,
  connectionDistance = 110,
  speedMultiplier = 1,
  zIndex = 1,
  ambientGlow = true,
  enableConnections = true,
  connectionOpacity = 0.22,
  coverFullPage = false,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animationRef = useRef<number | null>(null);
  const particlesRef = useRef<any[] | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const prefersReducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
    if (prefersReducedMotion) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    if (animationRef.current) cancelAnimationFrame(animationRef.current);

    const getPageHeight = () =>
      coverFullPage
        ? Math.max(
            document.body.scrollHeight,
            document.documentElement.scrollHeight,
            document.body.offsetHeight,
            window.innerHeight,
          )
        : window.innerHeight;

    const resizeCanvas = () => {
      const oldW = canvas.width;
      const oldH = canvas.height;

      canvas.width = window.innerWidth;
      canvas.height = getPageHeight();

      if (particlesRef.current && oldW > 0 && oldH > 0) {
        const rx = canvas.width / oldW;
        const ry = canvas.height / oldH;
        particlesRef.current.forEach(p => { p.x *= rx; p.y *= ry; });
      } else {
        particlesRef.current = [];
      }
    };
    resizeCanvas();

    const area = canvas.width * canvas.height;
    const baseCount = Math.floor(area / 50000);
    const particleCount = Math.max(minParticles, Math.min(maxParticles, Math.floor(baseCount * densityMultiplier)));

    // LD color palette — cyan/teal/indigo/violet
    const colors = theme === 'dark'
      ? [
          [34, 211, 238],   // cyan
          [45, 212, 191],   // teal
          [129, 140, 248],  // indigo
          [192, 132, 252],  // violet
          [56, 189, 248],   // sky
        ]
      : [
          [6, 182, 212],    // cyan-600
          [15, 118, 110],   // teal-700
          [79, 70, 229],    // indigo-600
          [124, 58, 237],   // violet-600
        ];

    if (!particlesRef.current || particlesRef.current.length === 0) {
      particlesRef.current = [];
      for (let i = 0; i < particleCount; i++) {
        particlesRef.current.push({
          x: Math.random() * canvas.width,
          y: Math.random() * canvas.height,
          vx: (Math.random() - 0.5) * 0.4 * speedMultiplier,
          vy: (Math.random() - 0.5) * 0.4 * speedMultiplier,
          baseSize: Math.random() * 3.2 + 1.1,
          pulsePhase: Math.random() * Math.PI * 2,
          pulseSpeed: 0.01 + Math.random() * 0.02,
          baseOpacity: theme === 'dark' ? Math.random() * 0.3 + 0.55 : Math.random() * 0.2 + 0.7,
          colorIndex: Math.floor(Math.random() * colors.length),
          flickerPhase: Math.random() * Math.PI * 2,
          flickerSpeed: 0.02 + Math.random() * 0.03,
        });
      }
    }

    const particles = particlesRef.current;
    const maxDist2 = connectionDistance * connectionDistance;

    const animate = () => {
      // When coverFullPage, sync canvas height each frame in case DOM grows
      if (coverFullPage) {
        const newH = getPageHeight();
        if (Math.abs(canvas.height - newH) > 10) {
          const ry = newH / canvas.height;
          canvas.height = newH;
          particles.forEach(p => { p.y *= ry; });
        }
      }

      ctx.clearRect(0, 0, canvas.width, canvas.height);

      particles.forEach((p, i) => {
        p.x += p.vx; p.y += p.vy;
        if (p.x < -50) p.x = canvas.width + 50;
        if (p.x > canvas.width + 50) p.x = -50;
        if (p.y < -50) p.y = canvas.height + 50;
        if (p.y > canvas.height + 50) p.y = -50;

        p.pulsePhase += p.pulseSpeed;
        p.flickerPhase += p.flickerSpeed;

        const pulse = Math.sin(p.pulsePhase) * 0.3 + 1;
        const size = p.baseSize * pulse;
        const flicker = Math.sin(p.flickerPhase) * (theme === 'dark' ? 0.2 : 0.1) + 0.85;
        const color = colors[p.colorIndex];
        const opacity = p.baseOpacity * flicker;

        if (theme === 'dark' && ambientGlow) {
          ctx.beginPath();
          ctx.arc(p.x, p.y, size * 3, 0, Math.PI * 2);
          ctx.fillStyle = `rgba(${color[0]},${color[1]},${color[2]},${opacity * 0.15})`;
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${color[0]},${color[1]},${color[2]},${opacity})`;
        ctx.fill();

        if (enableConnections) {
          for (let j = i + 1; j < particles.length; j++) {
            const o = particles[j];
            const dx = p.x - o.x, dy = p.y - o.y;
            const d2 = dx * dx + dy * dy;
            if (d2 >= maxDist2) continue;
            const d = Math.sqrt(d2);
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(o.x, o.y);
            const lo = connectionOpacity * (1 - d / connectionDistance) * flicker;
            ctx.strokeStyle = `rgba(${color[0]},${color[1]},${color[2]},${lo})`;
            ctx.lineWidth = 0.6;
            ctx.stroke();
          }
        }
      });

      animationRef.current = requestAnimationFrame(animate);
    };

    animate();
    window.addEventListener('resize', resizeCanvas);
    return () => {
      window.removeEventListener('resize', resizeCanvas);
      if (animationRef.current) cancelAnimationFrame(animationRef.current);
    };
  }, [theme, densityMultiplier, minParticles, maxParticles, connectionDistance, speedMultiplier, ambientGlow, enableConnections, connectionOpacity, coverFullPage]);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: coverFullPage ? 'absolute' : 'fixed',
        top: 0,
        left: 0,
        width: '100%',
        height: coverFullPage ? 'auto' : '100%',
        pointerEvents: 'none',
        zIndex,
      }}
    />
  );
};

export default ParticleBackground;
