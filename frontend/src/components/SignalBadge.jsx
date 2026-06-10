const colors = {
  strong_buy: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  buy: 'bg-green-500/20 text-green-400 border-green-500/30',
  neutral: 'bg-slate-500/20 text-slate-300 border-slate-500/30',
  sell: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  strong_sell: 'bg-red-500/20 text-red-400 border-red-500/30',
};

const labels = {
  strong_buy: 'Strong Buy',
  buy: 'Buy',
  neutral: 'Neutral',
  sell: 'Sell',
  strong_sell: 'Strong Sell',
};

export default function SignalBadge({ signal }) {
  const cls = colors[signal] || colors.neutral;
  const label = labels[signal] || signal;
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {label}
    </span>
  );
}
