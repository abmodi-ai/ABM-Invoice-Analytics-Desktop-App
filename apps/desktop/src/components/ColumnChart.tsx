// Single-series column chart (magnitude over time): one hue, thin columns with 4px rounded tops
// anchored to the baseline, 2px gaps, recessive grid, per-column hover tooltip, and a table view.
import { useMemo, useState } from "react";
import { Button } from "./ui";

export interface Datum {
  label: string;
  value: number;
  detail?: string;
}

export function ColumnChart({ data, format, title, height = 180 }: { data: Datum[]; format: (v: number) => string; title: string; height?: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const max = useMemo(() => Math.max(1, ...data.map((d) => d.value)), [data]);
  const ticks = useMemo(() => {
    const step = niceStep(max / 3);
    return [0, step, step * 2, step * 3].filter((t) => t <= max * 1.05 || t === 0);
  }, [max]);
  const top = Math.max(max, ticks[ticks.length - 1]);
  const W = 640;
  const padL = 48;
  const padB = 22;
  const innerW = W - padL - 8;
  const innerH = height - padB - 8;
  const slot = innerW / Math.max(1, data.length);
  const bw = Math.max(2, Math.min(28, slot - 2));

  return (
    <figure className="viz-root" aria-label={title}>
      <div className="mb-1 flex items-center justify-between">
        <figcaption className="text-sm font-medium text-ink">{title}</figcaption>
        <Button size="sm" variant="ghost" onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}>
          {asTable ? "Chart" : "Table"}
        </Button>
      </div>
      {asTable ? (
        <table className="w-full text-sm">
          <tbody>
            {data.map((d) => (
              <tr key={d.label} className="border-b border-border">
                <td className="py-1 text-ink-2">{d.label}</td>
                <td className="num py-1 text-right text-ink">{format(d.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="relative">
          <svg viewBox={`0 0 ${W} ${height}`} className="w-full" role="img" aria-label={`${title}: ${data.length} periods`}>
            {ticks.map((t) => {
              const y = 8 + innerH - (t / top) * innerH;
              return (
                <g key={t}>
                  <line x1={padL} x2={W - 8} y1={y} y2={y} stroke="var(--color-border)" strokeWidth={1} />
                  <text x={padL - 6} y={y + 4} textAnchor="end" fontSize={11} fill="var(--color-ink-3)">
                    {format(t)}
                  </text>
                </g>
              );
            })}
            {data.map((d, i) => {
              const h = Math.max(d.value > 0 ? 2 : 0, (d.value / top) * innerH);
              const x = padL + i * slot + (slot - bw) / 2;
              const y = 8 + innerH - h;
              const r = Math.min(4, bw / 2, h);
              return (
                <g key={d.label} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                  {/* hit target larger than the mark */}
                  <rect x={padL + i * slot} y={8} width={slot} height={innerH} fill="transparent" />
                  <path
                    d={`M${x},${8 + innerH} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${8 + innerH} Z`}
                    fill="var(--series-1)"
                    opacity={hover === null || hover === i ? 1 : 0.55}
                  />
                  {labelAt(i, data.length) && (
                    <text x={x + bw / 2} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--color-ink-3)">
                      {d.label}
                    </text>
                  )}
                </g>
              );
            })}
          </svg>
          {hover !== null && data[hover] && (
            <div
              className="pointer-events-none absolute -translate-x-1/2 rounded-md border border-border bg-surface px-2 py-1 text-xs shadow"
              style={{ left: `${((padL + hover * slot + slot / 2) / W) * 100}%`, top: 0 }}
            >
              <div className="text-ink-2">{data[hover].label}</div>
              <div className="num font-medium text-ink">{format(data[hover].value)}</div>
              {data[hover].detail && <div className="text-ink-3">{data[hover].detail}</div>}
            </div>
          )}
        </div>
      )}
    </figure>
  );
}

/** Evenly spaced axis labels (~8), always including the last one without crowding its neighbour. */
function labelAt(i: number, n: number): boolean {
  const every = Math.ceil(n / 8);
  if (i === n - 1) return true;
  return i % every === 0 && n - 1 - i >= Math.max(2, Math.ceil(every * 0.75));
}

function niceStep(x: number): number {
  if (x <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  const n = x / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}
