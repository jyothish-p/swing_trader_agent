import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from 'lucide-react';
import { getQuotes } from '../lib/api';

const PAGE_SIZE = 20;

function VerdictBadge({ verdict, probability }) {
  const colors = {
    'STRONG BUY': 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
    'BUY': 'bg-green-500/20 text-green-300 border-green-500/40',
    'HOLD': 'bg-amber-500/20 text-amber-300 border-amber-500/40',
    'WAIT': 'bg-orange-500/20 text-orange-300 border-orange-500/40',
    'AVOID': 'bg-red-500/20 text-red-300 border-red-500/40',
    'SKIP': 'bg-red-500/20 text-red-300 border-red-500/40',
  };
  const cls = colors[verdict] || 'bg-slate-500/20 text-slate-300 border-slate-500/40';
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs font-bold border ${cls}`}>
      {verdict}
      {probability !== undefined && (
        <span className="font-mono opacity-75">{probability}%</span>
      )}
    </span>
  );
}

function ScoreBar({ label, score, max = 100, color }) {
  const pct = Math.min(100, Math.round((score / max) * 100));
  const barColor = score >= 75 ? 'bg-emerald-400' : score >= 55 ? 'bg-green-400' : score >= 40 ? 'bg-amber-400' : score >= 25 ? 'bg-orange-400' : 'bg-red-400';
  return (
    <div className="flex items-center gap-1.5 min-w-[96px] sm:min-w-[112px]">
      <span className="w-4 text-right text-[10px] font-medium text-slate-400">{label}</span>
      <div className="w-14 sm:w-20 bg-slate-700 rounded-full h-2 overflow-hidden">
        <div className={`h-full rounded-full ${color || barColor}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-6 text-right text-[11px] font-mono text-slate-300">{score}</span>
    </div>
  );
}

function ActionBadge({ action }) {
  if (!action) return <span className="text-slate-600">—</span>;
  const cls = action === 'TRADE' ? 'bg-emerald-500/20 text-emerald-300'
    : action === 'WAIT RETEST' ? 'bg-amber-500/20 text-amber-300'
    : 'bg-red-500/20 text-red-300';
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded ${cls}`}>{action}</span>
  );
}

function TitanMeta({ meta }) {
  if (!meta) return null;
  const gateCls = meta.liquidity_gate === 'PASS'
    ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
    : 'bg-red-500/15 text-red-300 border-red-500/30';
  const gradeCls = meta.selection_grade === 'A+' || meta.selection_grade === 'A'
    ? 'bg-blue-500/15 text-blue-300 border-blue-500/30'
    : meta.selection_grade === 'B'
      ? 'bg-amber-500/15 text-amber-300 border-amber-500/30'
      : 'bg-slate-700 text-slate-300 border-slate-600';
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {meta.liquidity_gate && <span className={`text-[10px] px-1.5 py-0.5 rounded border ${gateCls}`}>Gate {meta.liquidity_gate}</span>}
      {meta.selection_grade && <span className={`text-[10px] px-1.5 py-0.5 rounded border ${gradeCls}`}>Grade {meta.selection_grade}</span>}
      {meta.selection_action && <span className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600 text-slate-300">{meta.selection_action}</span>}
    </div>
  );
}

function TitanContextMeta({ meta }) {
  if (!meta) return null;
  const toneCls = meta.news_tone === 'Positive'
    ? 'text-emerald-300 border-emerald-500/30'
    : meta.news_tone === 'Negative'
      ? 'text-red-300 border-red-500/30'
      : 'text-slate-300 border-slate-600';
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {meta.sector_index && (
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600 text-slate-300">
          {meta.sector_index}
          {meta.sector_weekly_rsi != null ? ` RSI ${Number(meta.sector_weekly_rsi).toFixed(1)}` : ''}
        </span>
      )}
      {meta.sector_momentum_score != null && (
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600 text-slate-300">
          Sector {meta.sector_momentum_score}/10
        </span>
      )}
      {meta.news_tone && (
        <span className={`text-[10px] px-1.5 py-0.5 rounded border ${toneCls}`}>
          News {meta.news_tone}
        </span>
      )}
      {meta.market_mood && (
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600 text-slate-300">
          Market {meta.market_mood}
        </span>
      )}
      {meta.retail_psych && (
        <span className="text-[10px] px-1.5 py-0.5 rounded border border-slate-600 text-slate-300">
          Retail {meta.retail_psych}
        </span>
      )}
    </div>
  );
}

function Pagination({ page, totalPages, onPageChange, totalItems, pageSize }) {
  if (totalPages <= 1) return null;
  const start = page * pageSize + 1;
  const end = Math.min((page + 1) * pageSize, totalItems);

  return (
    <div className="flex items-center justify-between mt-3 px-1">
      <span className="text-xs text-slate-500">
        Showing {start}–{end} of {totalItems}
      </span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page === 0}
          className="p-1 rounded hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-slate-400"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        {Array.from({ length: totalPages }, (_, i) => (
          <button
            key={i}
            onClick={() => onPageChange(i)}
            className={`w-7 h-7 rounded text-xs font-medium ${
              i === page
                ? 'bg-blue-600 text-white'
                : 'text-slate-400 hover:bg-slate-700'
            }`}
          >
            {i + 1}
          </button>
        )).slice(
          Math.max(0, page - 3),
          Math.min(totalPages, page + 4)
        )}
        {page + 4 < totalPages && <span className="text-slate-500 text-xs px-1">...</span>}
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages - 1}
          className="p-1 rounded hover:bg-slate-700 disabled:opacity-30 disabled:cursor-not-allowed text-slate-400"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

function SortIndicator({ sortOrder }) {
  if (sortOrder === 'desc') {
    return <ArrowDown className="w-3.5 h-3.5" />;
  }
  if (sortOrder === 'asc') {
    return <ArrowUp className="w-3.5 h-3.5" />;
  }
  return <ArrowUpDown className="w-3.5 h-3.5" />;
}

export default function StockTable({ stocks, runId, sortOrder = 'desc', onToggleSort }) {
  const [page, setPage] = useState(0);
  const [liveMap, setLiveMap] = useState({});

  if (!stocks || stocks.length === 0) {
    return <p className="text-slate-400 text-sm">No stocks to display</p>;
  }

  const totalPages = Math.ceil(stocks.length / PAGE_SIZE);
  const pageStocks = stocks.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [stocks]);

  useEffect(() => {
    let mounted = true;

    async function fetchQuotes() {
      try {
        const syms = pageStocks.map(s => s.symbol);
        if (!syms.length) return;
        const res = await getQuotes(syms);
        const data = res.data || res;
        const map = {};
        syms.forEach(sym => {
          const q = data[sym] || data[sym.toUpperCase()];
          if (q) map[sym] = q;
        });
        if (mounted) setLiveMap(prev => ({ ...prev, ...map }));
      } catch (e) {
        console.error('Failed to fetch quotes', e);
      }
    }

    // Initial fetch + periodic refresh
    fetchQuotes();
    const iv = setInterval(fetchQuotes, 15000);
    return () => { mounted = false; clearInterval(iv); };
  }, [page, stocks]);

  return (
    <div>
      <div className="mb-3 flex flex-col gap-2 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
        <span>Showing the full 5-engine view for each screened stock.</span>
        <span className="rounded-full border border-slate-700 px-2 py-1 text-[11px] text-slate-400">
          Scroll sideways on smaller screens
        </span>
      </div>
      <div className="overflow-x-auto rounded-lg border border-slate-700/70">
        <table className="w-full min-w-[1080px] text-sm">
          <thead>
            <tr className="border-b border-slate-700 bg-slate-900/40 text-left text-[11px] uppercase tracking-wide text-slate-400">
              <th className="px-3 py-3 pr-2">#</th>
              <th className="px-3 py-3 pr-4">Stock</th>
              <th className="px-3 py-3 pr-4 text-right">CMP</th>
              <th className="px-2 py-3 text-center">TITAN v20</th>
              <th className="px-2 py-3 text-center">TITAN v19</th>
              <th className="px-2 py-3 text-center">Swing AI v12.2</th>
              <th className="px-2 py-3 text-center">Swing AI v12.1</th>
              <th className="px-2 py-3 text-center">KING v16</th>
              <th className="px-2 py-3 text-center">
                <button
                  type="button"
                  onClick={onToggleSort}
                  className="inline-flex items-center gap-1 text-slate-300 hover:text-white transition-colors"
                  title="Sort by composite score"
                >
                  <span>Composite</span>
                  <SortIndicator sortOrder={sortOrder} />
                </button>
              </th>
              <th className="px-2 py-3 text-center">Verdict</th>
              <th className="px-2 py-3 text-center">Action</th>
            </tr>
          </thead>
          <tbody>
            {pageStocks.map((s, i) => {
              const mp = s.mate_pro;
              const titan = mp?.model_scores?.TITAN ?? mp?.model_scores?.TITAN_v20;
              const titanV19 = mp?.model_scores?.TITAN_v19;
              const swingAi = mp?.model_scores?.Swing_AI;
              const swingAiHyper = mp?.model_scores?.Swing_AI_Hyper;
              const king = mp?.model_scores?.KING;
              const composite = mp?.composite_score;
              const titanMeta = mp?.titan_v20 || mp?.titan_v19;

              return (
                <tr
                  key={s.symbol}
                  className="border-b border-slate-800 hover:bg-slate-800/50 transition-colors cursor-pointer group"
                >
                  <td className="px-3 py-3 align-top text-slate-500">{page * PAGE_SIZE + i + 1}</td>
                  <td className="px-3 py-3 pr-4 align-top">
                    <Link
                      to={`/stock/${s.symbol}${runId ? `?run_id=${runId}` : ''}`}
                      className="text-blue-400 hover:text-blue-300 no-underline font-medium group-hover:underline"
                    >
                      {s.symbol}
                    </Link>
                    <TitanMeta meta={titanMeta} />
                    <TitanContextMeta meta={titanMeta} />
                    {mp?.one_line_verdict && (
                      <div
                        className="mt-2 max-w-[300px] xl:max-w-[360px] text-[11px] leading-relaxed text-slate-400"
                        style={{
                          display: '-webkit-box',
                          WebkitLineClamp: 4,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                        }}
                      >
                        <span className="font-semibold text-slate-300">Verdict:</span> {mp.one_line_verdict}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 pr-4 align-top text-right font-mono text-slate-200 whitespace-nowrap">
                    {(() => {
                      const live = liveMap[s.symbol];
                      const display = (live && live.last_price != null) ? live.last_price : s.cmp;
                      if (display == null) return <span className="text-slate-600">—</span>;
                      return `₹${display.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
                    })()}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {titan != null ? <ScoreBar label="T" score={titan} /> : <span className="text-slate-600 text-xs">—</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {titanV19 != null ? <ScoreBar label="T" score={titanV19} /> : <span className="text-slate-600 text-xs">—</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {swingAi != null ? <ScoreBar label="S" score={swingAi} /> : <span className="text-slate-600 text-xs">—</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {swingAiHyper != null ? <ScoreBar label="S" score={swingAiHyper} /> : <span className="text-slate-600 text-xs">—</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {king != null ? <ScoreBar label="K" score={king} /> : <span className="text-slate-600 text-xs">—</span>}
                  </td>
                  <td className="px-2 py-3 align-top text-center whitespace-nowrap">
                    {composite != null ? (
                      <span className={`text-base font-bold font-mono ${
                        composite >= 75 ? 'text-emerald-400' : composite >= 55 ? 'text-green-400' :
                        composite >= 40 ? 'text-amber-400' : 'text-red-400'
                      }`}>
                        {composite.toFixed(1)}
                      </span>
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-2 py-3 align-top text-center whitespace-nowrap">
                    {mp?.consensus_verdict ? (
                      <VerdictBadge verdict={mp.consensus_verdict} probability={mp.composite_probability} />
                    ) : <span className="text-slate-600">—</span>}
                  </td>
                  <td className="px-2 py-3 align-top text-center whitespace-nowrap">
                    <ActionBadge action={mp?.action} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <Pagination
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        totalItems={stocks.length}
        pageSize={PAGE_SIZE}
      />
    </div>
  );
}
