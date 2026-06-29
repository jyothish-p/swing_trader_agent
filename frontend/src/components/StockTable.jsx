import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from 'lucide-react';
import { getQuotes } from '../lib/api';

const PAGE_SIZE = 20;

function VerdictBadge({ verdict, probability }) {
  const colors = {
    'STRONG BUY': 'bg-emerald-500/20 text-emerald-300 border-emerald-500/40',
    BUY: 'bg-green-500/20 text-green-300 border-green-500/40',
    HOLD: 'bg-amber-500/20 text-amber-300 border-amber-500/40',
    WAIT: 'bg-orange-500/20 text-orange-300 border-orange-500/40',
    AVOID: 'bg-red-500/20 text-red-300 border-red-500/40',
    SKIP: 'bg-red-500/20 text-red-300 border-red-500/40',
  };
  const cls = colors[verdict] || 'bg-slate-500/20 text-slate-300 border-slate-500/40';

  return (
    <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs font-bold ${cls}`}>
      {verdict}
      {probability !== undefined && (
        <span className="font-mono opacity-75">{probability}%</span>
      )}
    </span>
  );
}

function ScoreBar({ label, score, max = 100, color }) {
  const pct = Math.min(100, Math.round((score / max) * 100));
  const barColor = score >= 75 ? 'bg-emerald-400'
    : score >= 55 ? 'bg-green-400'
      : score >= 40 ? 'bg-amber-400'
        : score >= 25 ? 'bg-orange-400'
          : 'bg-red-400';

  return (
    <div className="flex min-w-[96px] items-center gap-1.5 sm:min-w-[112px]">
      <span className="w-4 text-right text-[10px] font-medium text-slate-400">{label}</span>
      <div className="h-2 w-14 overflow-hidden rounded-full bg-slate-700 sm:w-20">
        <div className={`h-full rounded-full ${color || barColor}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-6 text-right font-mono text-[11px] text-slate-300">{score}</span>
    </div>
  );
}

function ActionBadge({ action }) {
  if (!action) return <span className="text-slate-600">-</span>;

  const cls = action === 'TRADE'
    ? 'bg-emerald-500/20 text-emerald-300'
    : action === 'WAIT RETEST'
      ? 'bg-amber-500/20 text-amber-300'
      : 'bg-red-500/20 text-red-300';

  return (
    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${cls}`}>{action}</span>
  );
}

function TitanMeta({ meta }) {
  if (!meta) return null;

  const gateCls = meta.liquidity_gate === 'PASS'
    ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
    : 'border-red-500/30 bg-red-500/15 text-red-300';
  const gradeCls = meta.selection_grade === 'A+' || meta.selection_grade === 'A'
    ? 'border-blue-500/30 bg-blue-500/15 text-blue-300'
    : meta.selection_grade === 'B'
      ? 'border-amber-500/30 bg-amber-500/15 text-amber-300'
      : 'border-slate-600 bg-slate-700 text-slate-300';

  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {meta.liquidity_gate && <span className={`rounded border px-1.5 py-0.5 text-[10px] ${gateCls}`}>Gate {meta.liquidity_gate}</span>}
      {meta.selection_grade && <span className={`rounded border px-1.5 py-0.5 text-[10px] ${gradeCls}`}>Grade {meta.selection_grade}</span>}
      {meta.selection_action && <span className="rounded border border-slate-600 px-1.5 py-0.5 text-[10px] text-slate-300">{meta.selection_action}</span>}
    </div>
  );
}

function TitanContextMeta({ meta }) {
  if (!meta) return null;

  const toneCls = meta.news_tone === 'Positive'
    ? 'border-emerald-500/30 text-emerald-300'
    : meta.news_tone === 'Negative'
      ? 'border-red-500/30 text-red-300'
      : 'border-slate-600 text-slate-300';

  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {meta.sector_index && (
        <span className="rounded border border-slate-600 px-1.5 py-0.5 text-[10px] text-slate-300">
          {meta.sector_index}
          {meta.sector_weekly_rsi != null ? ` RSI ${Number(meta.sector_weekly_rsi).toFixed(1)}` : ''}
        </span>
      )}
      {meta.sector_momentum_score != null && (
        <span className="rounded border border-slate-600 px-1.5 py-0.5 text-[10px] text-slate-300">
          Sector {meta.sector_momentum_score}/10
        </span>
      )}
      {meta.news_tone && (
        <span className={`rounded border px-1.5 py-0.5 text-[10px] ${toneCls}`}>
          News {meta.news_tone}
        </span>
      )}
      {meta.market_mood && (
        <span className="rounded border border-slate-600 px-1.5 py-0.5 text-[10px] text-slate-300">
          Market {meta.market_mood}
        </span>
      )}
      {meta.retail_psych && (
        <span className="rounded border border-slate-600 px-1.5 py-0.5 text-[10px] text-slate-300">
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
  const firstPage = Math.max(0, page - 3);
  const lastPage = Math.min(totalPages, page + 4);

  return (
    <div className="mt-3 flex items-center justify-between px-1">
      <span className="text-xs text-slate-500">
        Showing {start}-{end} of {totalItems}
      </span>
      <div className="flex items-center gap-1">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page === 0}
          className="rounded p-1 text-slate-400 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        {Array.from({ length: totalPages }, (_, index) => index)
          .slice(firstPage, lastPage)
          .map((index) => (
            <button
              key={index}
              onClick={() => onPageChange(index)}
              className={`h-7 w-7 rounded text-xs font-medium ${
                index === page ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-slate-700'
              }`}
            >
              {index + 1}
            </button>
          ))}
        {lastPage < totalPages && <span className="px-1 text-xs text-slate-500">...</span>}
        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages - 1}
          className="rounded p-1 text-slate-400 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function SortIndicator({ sortOrder }) {
  if (sortOrder === 'desc') return <ArrowDown className="h-3.5 w-3.5" />;
  if (sortOrder === 'asc') return <ArrowUp className="h-3.5 w-3.5" />;
  return <ArrowUpDown className="h-3.5 w-3.5" />;
}

function formatPrice(value) {
  if (value == null) return '-';
  return `Rs ${value.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

export default function StockTable({ stocks, runId, sortOrder = 'desc', onToggleSort }) {
  const safeStocks = Array.isArray(stocks) ? stocks : [];
  const [page, setPage] = useState(0);
  const [liveMap, setLiveMap] = useState({});

  const totalPages = Math.max(1, Math.ceil(safeStocks.length / PAGE_SIZE));
  const pageStocks = useMemo(
    () => safeStocks.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [page, safeStocks]
  );

  useEffect(() => {
    setPage(0);
  }, [runId, safeStocks.length]);

  useEffect(() => {
    if (page < totalPages) return;
    setPage(0);
  }, [page, totalPages]);

  useEffect(() => {
    setLiveMap({});
  }, [runId]);

  useEffect(() => {
    if (!pageStocks.length) return undefined;

    let mounted = true;

    async function fetchQuotes() {
      try {
        const symbols = pageStocks.map((stock) => stock.symbol);
        const res = await getQuotes(symbols);
        const data = res.data || res;
        const nextQuotes = {};

        symbols.forEach((symbol) => {
          const quote = data[symbol] || data[symbol.toUpperCase()];
          if (quote) nextQuotes[symbol] = quote;
        });

        if (mounted && Object.keys(nextQuotes).length > 0) {
          setLiveMap((prev) => ({ ...prev, ...nextQuotes }));
        }
      } catch (error) {
        console.error('Failed to fetch quotes', error);
      }
    }

    fetchQuotes();
    const intervalId = setInterval(fetchQuotes, 15000);

    return () => {
      mounted = false;
      clearInterval(intervalId);
    };
  }, [pageStocks]);

  if (!safeStocks.length) {
    return <p className="text-sm text-slate-400">No stocks to display</p>;
  }

  return (
    <div>
      <div className="mb-3 flex flex-col gap-2 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
        <span>Showing the full 5-engine view for each screened stock. Live quotes refresh every 15 seconds.</span>
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
                  className="inline-flex items-center gap-1 text-slate-300 transition-colors hover:text-white"
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
            {pageStocks.map((stock, index) => {
              const matePro = stock.mate_pro;
              const titan = matePro?.model_scores?.TITAN ?? matePro?.model_scores?.TITAN_v20;
              const titanV19 = matePro?.model_scores?.TITAN_v19;
              const swingAi = matePro?.model_scores?.Swing_AI;
              const swingAiHyper = matePro?.model_scores?.Swing_AI_Hyper;
              const king = matePro?.model_scores?.KING;
              const composite = matePro?.composite_score;
              const titanMeta = matePro?.titan_v20 || matePro?.titan_v19;
              const placeholder = '-';
              const liveQuote = liveMap[stock.symbol];
              const displayPrice = liveQuote?.last_price ?? stock.cmp;

              return (
                <tr
                  key={stock.symbol}
                  className="group cursor-pointer border-b border-slate-800 transition-colors hover:bg-slate-800/50"
                >
                  <td className="px-3 py-3 align-top text-slate-500">{page * PAGE_SIZE + index + 1}</td>
                  <td className="px-3 py-3 pr-4 align-top">
                    <Link
                      to={`/stock/${stock.symbol}${runId ? `?run_id=${runId}` : ''}`}
                      className="font-medium text-blue-400 no-underline group-hover:underline hover:text-blue-300"
                    >
                      {stock.symbol}
                    </Link>
                    <TitanMeta meta={titanMeta} />
                    <TitanContextMeta meta={titanMeta} />
                    {matePro?.one_line_verdict && (
                      <div
                        className="mt-2 max-w-[300px] text-[11px] leading-relaxed text-slate-400 xl:max-w-[360px]"
                        style={{
                          display: '-webkit-box',
                          WebkitLineClamp: 4,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                        }}
                      >
                        <span className="font-semibold text-slate-300">Verdict:</span> {matePro.one_line_verdict}
                      </div>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 pr-4 text-right font-mono text-slate-200">
                    {displayPrice == null ? <span className="text-slate-600">-</span> : formatPrice(displayPrice)}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {titan != null ? <ScoreBar label="T" score={titan} /> : <span className="text-xs text-slate-600">{placeholder}</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {titanV19 != null ? <ScoreBar label="T" score={titanV19} /> : <span className="text-xs text-slate-600">{placeholder}</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {swingAi != null ? <ScoreBar label="S" score={swingAi} /> : <span className="text-xs text-slate-600">{placeholder}</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {swingAiHyper != null ? <ScoreBar label="S" score={swingAiHyper} /> : <span className="text-xs text-slate-600">{placeholder}</span>}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {king != null ? <ScoreBar label="K" score={king} /> : <span className="text-xs text-slate-600">{placeholder}</span>}
                  </td>
                  <td className="whitespace-nowrap px-2 py-3 text-center">
                    {composite != null ? (
                      <span className={`font-mono text-base font-bold ${
                        composite >= 75 ? 'text-emerald-400'
                          : composite >= 55 ? 'text-green-400'
                            : composite >= 40 ? 'text-amber-400'
                              : 'text-red-400'
                      }`}>
                        {composite.toFixed(1)}
                      </span>
                    ) : (
                      <span className="text-slate-600">{placeholder}</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-2 py-3 text-center">
                    {matePro?.consensus_verdict ? (
                      <VerdictBadge verdict={matePro.consensus_verdict} probability={matePro.composite_probability} />
                    ) : (
                      <span className="text-slate-600">-</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-2 py-3 text-center">
                    <ActionBadge action={matePro?.action} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Pagination
        page={page}
        totalPages={totalPages}
        onPageChange={setPage}
        totalItems={safeStocks.length}
        pageSize={PAGE_SIZE}
      />
    </div>
  );
}
