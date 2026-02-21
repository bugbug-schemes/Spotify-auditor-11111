/**
 * SVG Radar Chart component for artist scoring dimensions.
 *
 * Renders a hexagonal radar chart with axes corresponding to the
 * scoring dimensions. Uses the artist's classification color for
 * the filled polygon.
 */

const DEFAULT_SIZE = 280;

export default function RadarChart({ scores, color, size = DEFAULT_SIZE }) {
  if (!scores || Object.keys(scores).length < 3) return null;

  const labels = Object.keys(scores);
  const values = labels.map(k => (scores[k] || 0) / 100);
  const n = labels.length;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 40; // leave room for labels

  function point(angleIdx, radiusFrac) {
    const angle = (2 * Math.PI * angleIdx / n) - Math.PI / 2;
    return [
      cx + radiusFrac * r * Math.cos(angle),
      cy + radiusFrac * r * Math.sin(angle),
    ];
  }

  // Grid rings at 25%, 50%, 75%, 100%
  const gridRings = [0.25, 0.5, 0.75, 1.0].map(frac => {
    const pts = Array.from({ length: n }, (_, i) => {
      const [x, y] = point(i, frac);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
    return (
      <polygon
        key={frac}
        points={pts}
        fill="none"
        stroke="#1a2332"
        strokeWidth="1"
      />
    );
  });

  // Spoke lines from center to each vertex
  const spokes = Array.from({ length: n }, (_, i) => {
    const [x, y] = point(i, 1.0);
    return (
      <line
        key={i}
        x1={cx}
        y1={cy}
        x2={x.toFixed(1)}
        y2={y.toFixed(1)}
        stroke="#1a2332"
        strokeWidth="1"
      />
    );
  });

  // Data polygon
  const dataPts = values.map((v, i) => {
    const [x, y] = point(i, Math.max(v, 0.03));
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');

  // Axis labels and score values
  const labelElems = labels.map((label, i) => {
    const [lx, ly] = point(i, 1.28);
    let anchor = 'middle';
    if (lx < cx - 10) anchor = 'end';
    else if (lx > cx + 10) anchor = 'start';
    const scoreVal = scores[label] || 0;

    return (
      <g key={i}>
        <text
          x={lx.toFixed(1)}
          y={ly.toFixed(1)}
          fill="#8899aa"
          fontSize="9"
          textAnchor={anchor}
          dominantBaseline="middle"
        >
          {label}
        </text>
        <text
          x={lx.toFixed(1)}
          y={(ly + 12).toFixed(1)}
          fill={color}
          fontSize="10"
          fontWeight="bold"
          textAnchor={anchor}
          dominantBaseline="middle"
        >
          {scoreVal}
        </text>
      </g>
    );
  });

  return (
    <div className="radar-chart-container">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        xmlns="http://www.w3.org/2000/svg"
        className="radar-chart-svg"
      >
        {gridRings}
        {spokes}
        <polygon
          points={dataPts}
          fill={color}
          fillOpacity="0.15"
          stroke={color}
          strokeWidth="2"
        />
        {labelElems}
      </svg>
    </div>
  );
}
