// Circular gauge for the verifier's confidence (supported factual claims / factual claims).

interface Props {
  value: number; // 0..1
  supported: number;
  factual: number;
}

export function ConfidenceGauge({ value, supported, factual }: Props) {
  const pct = Math.round(value * 100);
  const radius = 16;
  const circumference = 2 * Math.PI * radius;
  const colour = value >= 0.8 ? "text-emerald-500" : value >= 0.6 ? "text-amber-500" : "text-rose-500";
  return (
    <div className="flex items-center gap-2" title={`${supported} of ${factual} factual claims verified against their cited source`}>
      <svg width="40" height="40" viewBox="0 0 40 40" role="img" aria-label={`Confidence ${pct}%`}>
        <circle cx="20" cy="20" r={radius} fill="none" strokeWidth="4" className="stroke-slate-200" />
        <circle
          cx="20" cy="20" r={radius} fill="none" strokeWidth="4" strokeLinecap="round"
          className={`stroke-current ${colour}`}
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - value)}
          transform="rotate(-90 20 20)"
        />
        <text x="20" y="24" textAnchor="middle" className="fill-slate-800 text-[10px] font-semibold">
          {pct}%
        </text>
      </svg>
      <div className="text-xs leading-tight text-slate-600">
        <div className="font-medium text-slate-800">Confidence</div>
        <div>{supported}/{factual} claims verified</div>
      </div>
    </div>
  );
}
