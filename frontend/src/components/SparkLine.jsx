import React from 'react';

/**
 * Sparkline — lightweight inline SVG sparkline for Smart Money Score history.
 *
 * Renders a 30-day (default) polyline scaled to fit a fixed bounding box.
 * Auto-colors based on the trend of the last 7 points (slope):
 *   - emerald if positive slope (rising confidence)
 *   - red if negative slope (falling confidence)
 *   - amber if flat
 *
 * Props:
 *   points: [{date, score, ...}]   (chronological ascending)
 *   width / height: px dimensions
 *   className: extra wrapper classes
 */
export default function SparkLine({ points = [], width = 56, height = 16, className = '' }) {
  if (!points || points.length < 2) {
    return <div className={`w-[${width}px] h-[${height}px] ${className}`} aria-hidden="true" />;
  }
  const scores = points.map(p => p.score);
  const min = Math.max(0, Math.min(...scores) - 5);
  const max = Math.min(100, Math.max(...scores) + 5);
  const range = max - min || 1;
  const n = points.length;
  const stepX = width / (n - 1);

  const coords = points.map((p, i) => {
    const x = i * stepX;
    // Flip Y so higher score = higher visually
    const y = height - ((p.score - min) / range) * height;
    return [x, y];
  });
  const polyPath = coords.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ');

  // Slope of last 7 points (or available)
  const tail = coords.slice(-Math.min(7, coords.length));
  const slope = tail.length >= 2 ? (tail[tail.length - 1][1] - tail[0][1]) : 0;
  // Note: y is flipped, so NEGATIVE slope in SVG space = score going UP
  const trend = slope < -0.5 ? 'up' : slope > 0.5 ? 'down' : 'flat';
  const stroke = trend === 'up' ? '#34d399' : trend === 'down' ? '#f87171' : '#fbbf24';
  const fillColor = trend === 'up' ? 'rgba(52,211,153,0.15)' : trend === 'down' ? 'rgba(248,113,113,0.15)' : 'rgba(251,191,36,0.15)';

  // Build an area path for subtle fill
  const areaPath = `M ${coords[0][0]},${height} L ${coords.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' L ')} L ${coords[coords.length - 1][0]},${height} Z`;
  const lastY = coords[coords.length - 1][1];

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={`overflow-visible ${className}`}
      role="img"
      aria-label={`Smart Money Score trend over ${n} snapshots, trend ${trend}`}
    >
      <path d={areaPath} fill={fillColor} />
      <polyline points={polyPath} fill="none" stroke={stroke} strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={coords[coords.length - 1][0]} cy={lastY} r="1.6" fill={stroke} />
    </svg>
  );
}
