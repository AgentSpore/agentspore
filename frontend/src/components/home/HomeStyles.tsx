/** Keyframes and motion rules used only by the home page. */
const HOME_CSS = `
@keyframes scanDown {
  0%, 100% { top: -2%; opacity: 0; }
  10% { opacity: 1; }
  90% { opacity: 1; }
  100% { top: 102%; opacity: 0; }
}
@keyframes fadeInUp {
  from { opacity: 0; transform: translateY(24px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes particle-float {
  0%, 100% { transform: translate(0, 0); opacity: var(--p-opacity, 0.1); }
  25% { transform: translate(10px, -20px); opacity: calc(var(--p-opacity, 0.1) * 1.5); }
  50% { transform: translate(-5px, -40px); opacity: var(--p-opacity, 0.1); }
  75% { transform: translate(15px, -20px); opacity: calc(var(--p-opacity, 0.1) * 0.5); }
}
.particle-float { animation: particle-float var(--duration, 8s) ease-in-out infinite; }
@keyframes ticker-scroll {
  0% { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
.ticker-track { animation: ticker-scroll 60s linear infinite; }
.strip-viewport { -webkit-mask-image: linear-gradient(to right, transparent, #000 6%, #000 94%, transparent); mask-image: linear-gradient(to right, transparent, #000 6%, #000 94%, transparent); }
[data-strip]:hover .strip-track, [data-strip]:focus-within .strip-track, [data-strip][data-paused="true"] .strip-track { animation-play-state: paused; }
[data-strip][data-reduced="true"] .strip-viewport { overflow-x: auto; }
@keyframes marquee-scroll {
  0% { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
.marquee-track { animation: marquee-scroll 40s linear infinite; }
.fade-in { animation: fadeInUp 0.8s ease-out both; }
.fade-in-d1 { animation: fadeInUp 0.8s ease-out 0.1s both; }
.fade-in-d2 { animation: fadeInUp 0.8s ease-out 0.2s both; }
.fade-in-d3 { animation: fadeInUp 0.8s ease-out 0.3s both; }
.fade-in-d4 { animation: fadeInUp 0.8s ease-out 0.4s both; }
.fade-in-d5 { animation: fadeInUp 0.8s ease-out 0.5s both; }
.card-glow:hover {
  box-shadow: 0 0 40px -12px rgba(139, 92, 246, 0.08);
}
@keyframes gradient-shift {
  0%, 100% { background-position: 0% 50%; }
  50% { background-position: 100% 50%; }
}
.gradient-text-animated {
  background-size: 200% 200%;
  animation: gradient-shift 6s ease-in-out infinite;
}
@keyframes pulse-ring {
  0% { transform: scale(1); opacity: 0.4; }
  50% { transform: scale(1.15); opacity: 0; }
  100% { transform: scale(1); opacity: 0; }
}
.hero-stat-card {
  transition: transform 0.3s, border-color 0.3s, box-shadow 0.3s;
}
.hero-stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 30px -8px rgba(139, 92, 246, 0.15);
}
@media (prefers-reduced-motion: reduce) {
  [data-home], [data-home] * {
    animation: none !important;
    transition: none !important;
  }
  [data-home] main, [data-home] [class*="fade-in"] { opacity: 1 !important; transform: none !important; }
}
`;

export function HomeStyles() {
  return <style>{HOME_CSS}</style>;
}
