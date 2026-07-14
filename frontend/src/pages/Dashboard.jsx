import { useState, useEffect, useMemo } from 'react';
import { useCallback } from 'react';
import {
  Activity,
  AlertCircle,
  CheckCircle,
  Clock,
  Eye,
  Hourglass,
  Loader2,
  Play,
  RefreshCw,
  Shield,
  Target,
  ThumbsUp,
  TrendingUp,
} from 'lucide-react';
import StockTable from '../components/StockTable';
import ActionableTable from '../components/ActionableTable';
import { runScreenerAsync, getScreenerRuns, getScreenerResults, getScreenerStatus } from '../lib/api';

const SORT_OPTIONS = {
  DESC: 'desc',
  ASC: 'asc',
};
const STALE_RUN_MS = 60 * 60 * 1000;
const LIVE_REFRESH_MS = 60 * 1000;
const LATEST_DASHBOARD_RUN_KEY = 'swingTraderLatestDashboardRunId';

function normalizeScreenerResult(payload, fallbackRunId = null) {
  const allStocks = payload?.all_stocks || payload?.stocks || payload?.top_stocks || [];
  const topStocks = payload?.top_stocks || payload?.stocks || payload?.all_stocks || [];

  return {
    run_id: payload?.run_id || fallbackRunId,
    all_stocks: allStocks,
    top_stocks: topStocks,
    total_analyzed: payload?.total_analyzed || payload?.total || payload?.screening?.analyzed || allStocks.length,
    mate_pro_summary: payload?.mate_pro_summary || null,
    actionable_stocks: payload?.actionable_stocks || [],
    universe_size: payload?.universe_size || payload?.total || payload?.screening?.analyzed || allStocks.length,
    full_engine_complete: payload?.full_engine_complete ?? allStocks.every((stock) => Boolean(stock?.mate_pro?.model_scores)),
  };
}

function getCompositeValue(stock) {
  const composite = stock?.mate_pro?.composite_score ?? stock?.composite_score;
  const numeric = Number(composite);
  return Number.isFinite(numeric) ? numeric : -Infinity;
}

function sortByComposite(stocks, direction) {
  const multiplier = direction === SORT_OPTIONS.ASC ? 1 : -1;
  return [...stocks].sort((left, right) => {
    const leftScore = getCompositeValue(left);
    const rightScore = getCompositeValue(right);

    if (leftScore !== rightScore) {
      return (leftScore - rightScore) * multiplier;
    }

    return String(left?.symbol || '').localeCompare(String(right?.symbol || ''));
  });
}

function toggleSortOrder(direction) {
  return direction === SORT_OPTIONS.DESC ? SORT_OPTIONS.ASC : SORT_OPTIONS.DESC;
}

function MetricCard({ icon, value, label, tone = 'blue', active = false, onClick }) {
  const CardIcon = icon;
  const tones = {
    blue: 'from-sky-500/20 to-blue-950/40 border-sky-500/35 text-sky-300 shadow-sky-500/10',
    green: 'from-emerald-500/20 to-emerald-950/35 border-emerald-500/35 text-emerald-300 shadow-emerald-500/10',
    purple: 'from-violet-500/25 to-violet-950/35 border-violet-500/40 text-violet-300 shadow-violet-500/10',
    amber: 'from-amber-500/18 to-orange-950/35 border-amber-500/35 text-amber-300 shadow-amber-500/10',
    cyan: 'from-cyan-500/18 to-teal-950/35 border-cyan-500/35 text-cyan-300 shadow-cyan-500/10',
    red: 'from-rose-500/20 to-red-950/35 border-rose-500/35 text-rose-300 shadow-rose-500/10',
  };

  return (
    <button
      type="button"
      onClick={onClick}
      className={`group min-h-[148px] rounded-2xl border bg-gradient-to-br p-4 text-center shadow-2xl transition-all ${
        tones[tone] || tones.blue
      } ${active ? 'scale-[1.025] ring-2 ring-white/20' : 'hover:-translate-y-0.5 hover:border-white/25'} ${onClick ? 'cursor-pointer' : 'cursor-default'}`}
    >
      <CardIcon className="mx-auto mb-4 h-8 w-8 opacity-95 transition-transform group-hover:scale-110" />
      <div className="text-3xl font-black tracking-tight text-white">{value}</div>
      <div className="mt-1 text-sm font-medium text-slate-300">{label}</div>
    </button>
  );
}

export default function Dashboard() {
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [result, setResult] = useState(null);
  const [runs, setRuns] = useState([]);
  const [error, setError] = useState('');
  const [verdictFilter, setVerdictFilter] = useState('ALL');
  const [activeRunId, setActiveRunId] = useState(null);
  const [stockSortOrder, setStockSortOrder] = useState(SORT_OPTIONS.DESC);
  const [actionableSortOrder, setActionableSortOrder] = useState(SORT_OPTIONS.DESC);

  const displayStocks = useMemo(() => result?.all_stocks || result?.top_stocks || [], [result?.all_stocks, result?.top_stocks]);
  const latestCompletedRunId = useMemo(
    () => runs.find((run) => run.status === 'completed')?.run_id || null,
    [runs]
  );

  const loadRunResults = useCallback(async (runId) => {
    try {
      const res = await getScreenerResults(runId);
      if (res.data?.full_engine_complete === false) {
        setError('This run does not contain full 5-engine results. Start a fresh screener run after the latest deploy.');
        setLoading(false);
        setStatus('');
        setActiveRunId(null);
        return;
      }
      setResult(normalizeScreenerResult(res.data, runId));
      setError('');
      setStatus('');
      setLoading(false);
      setActiveRunId(null);
    } catch (error) {
      console.error('Failed to load run results:', error);
      setError(error.response?.data?.detail || error.message || 'Failed to load screener results');
      setLoading(false);
      setStatus('');
      setActiveRunId(null);
    }
  }, []);

  const loadRuns = useCallback(async (autoLoadLatest = false) => {
    try {
      const res = await getScreenerRuns(5);
      const latestRuns = res.data.runs || [];
      setRuns(latestRuns);
      if (autoLoadLatest) {
        const running = latestRuns.find((r) => (
          r.status === 'running'
          && r.started_at
          && (Date.now() - Date.parse(r.started_at)) < STALE_RUN_MS
        ));
        if (running) {
          setLoading(true);
          setStatus('Resuming latest screener run...');
          setActiveRunId(running.run_id);
          return;
        }

        const latest = latestRuns.find(r => r.status === 'completed' && r.has_full_results);
        if (latest) {
          await loadRunResults(latest.run_id);
        } else {
          setLoading(false);
          setStatus('');
        }
      }
    } catch {
      // No runs yet
    }
  }, [loadRunResults]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadRuns(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [loadRuns]);

  useEffect(() => {
    if (result?.run_id) {
      window.localStorage.setItem(LATEST_DASHBOARD_RUN_KEY, result.run_id);
    }
  }, [result?.run_id]);

  useEffect(() => {
    if (!activeRunId) return undefined;

    let cancelled = false;
    let timeoutId = null;

    const pollStatus = async () => {
      try {
        const res = await getScreenerStatus(activeRunId);
        const data = res.data;
        if (cancelled) return;

        if (data.status === 'completed') {
          try {
            const payload = data.result || (await getScreenerResults(activeRunId)).data;
            if (cancelled) return;
            if (payload?.full_engine_complete === false) {
              setError('This run only has base screener rows. Please run the screener again for full 5-engine results.');
              setStatus('');
              setLoading(false);
              setActiveRunId(null);
              loadRuns(false);
              return;
            }
            setResult(normalizeScreenerResult(payload, activeRunId));
            setError('');
            setStatus('');
            setLoading(false);
            setActiveRunId(null);
            loadRuns(false);
          } catch (error) {
            if (cancelled) return;
            setError(error.response?.data?.detail || error.message || 'Failed to load screener results');
            setStatus('');
            setLoading(false);
            setActiveRunId(null);
          }
          return;
        }

        if (data.status === 'failed') {
          setError(data.error || 'Screener failed');
          setStatus('');
          setLoading(false);
          setActiveRunId(null);
          return;
        }

        setStatus(data.message || 'Running screener...');
      } catch {
        if (cancelled) return;
        setStatus('Running screener...');
      }

      if (!cancelled) {
        timeoutId = setTimeout(pollStatus, 2000);
      }
    };

    pollStatus();

    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [activeRunId, loadRuns]);

  useEffect(() => {
    if (loading || activeRunId) return undefined;
    if (result?.run_id && latestCompletedRunId && result.run_id !== latestCompletedRunId) return undefined;

    let cancelled = false;

    const refreshLatestRun = async () => {
      try {
        const runsResponse = await getScreenerRuns(5);
        const latestRuns = runsResponse.data.runs || [];
        if (cancelled) return;

        setRuns(latestRuns);
        const latestCompleted = latestRuns.find((run) => run.status === 'completed' && run.has_full_results);
        if (!latestCompleted) return;
        if (result?.run_id === latestCompleted.run_id) return;

        const resultsResponse = await getScreenerResults(latestCompleted.run_id);
        if (cancelled) return;

        setResult(normalizeScreenerResult(resultsResponse.data, latestCompleted.run_id));
        setError('');
      } catch (error) {
        if (!cancelled) {
          console.error('Live dashboard refresh failed:', error);
        }
      }
    };

    const intervalId = setInterval(refreshLatestRun, LIVE_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [activeRunId, latestCompletedRunId, loading, result?.run_id]);

  async function handleRunScreener(forceRefresh = false) {
    setLoading(true);
    setError('');
    setStatus('Starting screener...');

    try {
      const res = await runScreenerAsync(forceRefresh);
      if (res.data.message) {
        setStatus(res.data.message);
      }
      setActiveRunId(res.data.run_id);
    } catch (error) {
      setError(error.response?.data?.detail || error.message || 'Screener failed');
      setStatus('');
      setActiveRunId(null);
      setLoading(false);
    }
  }

  const verdictCounts = { 'STRONG BUY': 0, 'BUY': 0, 'HOLD': 0, 'WAIT': 0, 'AVOID': 0 };
  if (result?.mate_pro_summary) {
    verdictCounts['STRONG BUY'] = result.mate_pro_summary.strong_buy || 0;
    verdictCounts['BUY'] = result.mate_pro_summary.buy || 0;
    verdictCounts['HOLD'] = result.mate_pro_summary.hold || 0;
    verdictCounts['WAIT'] = result.mate_pro_summary.wait || 0;
    verdictCounts['AVOID'] = result.mate_pro_summary.avoid || 0;
  } else if (displayStocks.length) {
    displayStocks.forEach(s => {
      const v = s.mate_pro?.consensus_verdict;
      if (v && verdictCounts[v] !== undefined) verdictCounts[v]++;
    });
  }
  const buyLikeCount = (verdictCounts['STRONG BUY'] || 0) + (verdictCounts['BUY'] || 0);

  const filteredTopStocks = useMemo(() => {
    if (!displayStocks.length) return [];
    if (verdictFilter === 'BUYABLE') {
      return displayStocks.filter(s => ['STRONG BUY', 'BUY'].includes(s.mate_pro?.consensus_verdict));
    }
    if (verdictFilter === 'ALL') return displayStocks;
    return displayStocks.filter(s => s.mate_pro?.consensus_verdict === verdictFilter);
  }, [displayStocks, verdictFilter]);

  const sortedTopStocks = useMemo(
    () => sortByComposite(filteredTopStocks, stockSortOrder),
    [filteredTopStocks, stockSortOrder]
  );

  const actionableStocks = useMemo(() => result?.actionable_stocks || [], [result?.actionable_stocks]);
  const filteredActionable = useMemo(() => {
    if (!actionableStocks.length) return [];
    if (verdictFilter === 'BUYABLE') {
      return actionableStocks.filter(s =>
        s.action_type === 'BUY' || ['STRONG BUY', 'BUY'].includes(s.verdict)
      );
    }
    if (verdictFilter === 'ALL') return actionableStocks;
    return actionableStocks.filter(s => s.verdict === verdictFilter);
  }, [actionableStocks, verdictFilter]);

  const sortedActionable = useMemo(
    () => sortByComposite(filteredActionable, actionableSortOrder),
    [filteredActionable, actionableSortOrder]
  );

  return (
    <div className="space-y-6">
      <section className="rounded-[28px] border border-white/10 bg-slate-950/35 p-5 shadow-[0_30px_80px_rgba(2,6,23,0.45)] backdrop-blur-xl sm:p-7">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div className="max-w-4xl">
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h1 className="text-3xl font-black tracking-tight text-white md:text-4xl">Swing Trading Dashboard</h1>
              <span className="inline-flex items-center gap-1 rounded-lg border border-emerald-400/25 bg-emerald-400/10 px-2.5 py-1 text-xs font-bold text-emerald-300">
                <span className="h-2 w-2 rounded-full bg-emerald-300 shadow-[0_0_14px_rgba(110,231,183,0.8)]" />
                LIVE
              </span>
            </div>
            <p className="text-base text-slate-300">
              Top NSE stocks scored equally by TITAN v20, TITAN v19, Swing AI v12.2, Swing AI v12.1 and KING v16.
            </p>
            <p className="mt-3 text-sm text-slate-500">
              Full 5-engine scoring, live quotes and run snapshots in one dark terminal-style workspace. Backtest is a separate report check.
            </p>
          </div>
          <div className="flex flex-wrap gap-3 xl:justify-end">
            <button
              onClick={() => handleRunScreener(false)}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-sky-500 to-indigo-600 px-6 py-3 text-sm font-bold text-white shadow-[0_16px_40px_rgba(79,70,229,0.38)] transition-all hover:-translate-y-0.5 disabled:translate-y-0 disabled:opacity-60"
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
              {loading ? 'Running...' : 'Run Screener'}
            </button>
            <button
              onClick={() => handleRunScreener(true)}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-xl border border-white/10 bg-slate-950/45 px-5 py-3 text-sm font-semibold text-slate-300 shadow-inner shadow-white/5 transition-all hover:border-slate-500 hover:text-white disabled:opacity-60"
              title="Force re-download all data"
            >
              <RefreshCw className="h-4 w-4" />
              Full Refresh
            </button>
          </div>
        </div>

        {status && (
          <div className="mt-6 flex items-center gap-3 rounded-2xl border border-sky-400/30 bg-sky-500/10 px-4 py-3">
            <Loader2 className="h-4 w-4 animate-spin text-sky-300" />
            <span className="text-sm font-medium text-sky-200">{status}</span>
          </div>
        )}

        {error && (
          <div className="mt-6 flex items-center gap-3 rounded-2xl border border-rose-400/30 bg-rose-500/10 px-4 py-3">
            <AlertCircle className="h-4 w-4 text-rose-300" />
            <span className="text-sm font-medium text-rose-200">{error}</span>
          </div>
        )}

        {result && (
          <div className="mt-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-9">
            <MetricCard icon={CheckCircle} value={result.universe_size ?? result.total_analyzed ?? displayStocks.length} label="Checked" tone="blue" />
            <MetricCard icon={TrendingUp} value={result.total_analyzed ?? displayStocks.length} label="Analyzed" tone="green" />
            <MetricCard icon={Eye} value={sortedTopStocks.length} label="Showing" tone="purple" active={verdictFilter === 'ALL'} onClick={() => setVerdictFilter('ALL')} />
            <MetricCard icon={ThumbsUp} value={buyLikeCount} label="Buy / Strong Buy" tone="amber" active={verdictFilter === 'BUYABLE'} onClick={() => setVerdictFilter('BUYABLE')} />
            <MetricCard icon={Target} value={verdictCounts['STRONG BUY']} label="Strong Buy" tone="cyan" active={verdictFilter === 'STRONG BUY'} onClick={() => setVerdictFilter(verdictFilter === 'STRONG BUY' ? 'ALL' : 'STRONG BUY')} />
            <MetricCard icon={Activity} value={verdictCounts['BUY']} label="Buy" tone="blue" active={verdictFilter === 'BUY'} onClick={() => setVerdictFilter(verdictFilter === 'BUY' ? 'ALL' : 'BUY')} />
            <MetricCard icon={Hourglass} value={verdictCounts['HOLD']} label="Hold" tone="amber" active={verdictFilter === 'HOLD'} onClick={() => setVerdictFilter(verdictFilter === 'HOLD' ? 'ALL' : 'HOLD')} />
            <MetricCard icon={Clock} value={verdictCounts['WAIT']} label="Wait" tone="purple" active={verdictFilter === 'WAIT'} onClick={() => setVerdictFilter(verdictFilter === 'WAIT' ? 'ALL' : 'WAIT')} />
            <MetricCard icon={Shield} value={verdictCounts['AVOID']} label="Avoid" tone="red" active={verdictFilter === 'AVOID'} onClick={() => setVerdictFilter(verdictFilter === 'AVOID' ? 'ALL' : 'AVOID')} />
          </div>
        )}
      </section>

      {verdictFilter !== 'ALL' && (
        <div className="flex items-center gap-2 rounded-2xl border border-white/10 bg-slate-950/35 px-4 py-3">
          <span className="text-xs text-slate-400">
            Filtering by: <span className="font-semibold text-white">{verdictFilter === 'BUYABLE' ? 'BUY / STRONG BUY' : verdictFilter}</span>
          </span>
          <button
            onClick={() => setVerdictFilter('ALL')}
            className="text-xs text-blue-400 hover:text-blue-300 underline"
          >
            Clear filter
          </button>
        </div>
      )}

      {result && buyLikeCount === 0 && (
        <div className="rounded-2xl border border-amber-400/30 bg-gradient-to-r from-amber-500/10 to-yellow-950/20 px-5 py-4 shadow-[0_20px_50px_rgba(245,158,11,0.08)]">
          <div className="text-sm font-bold text-amber-200">This run currently has no BUY or STRONG BUY candidates.</div>
          <div className="mt-1 text-xs text-slate-400">
            Current verdicts: {verdictCounts['WAIT']} WAIT, {verdictCounts['AVOID']} AVOID.
          </div>
        </div>
      )}

      {sortedActionable.length > 0 && (
        <section className="rounded-[24px] border border-emerald-400/20 bg-slate-950/40 p-4 shadow-[0_25px_70px_rgba(2,6,23,0.35)] backdrop-blur-xl sm:p-5">
          <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <h2 className="text-xl font-black text-white">
              Actionable Now
              <span className="ml-2 text-xs font-normal text-slate-400">
                {sortedActionable.filter(s => s.action_type === 'BUY').length} BUY
                {' · '}
                {sortedActionable.filter(s => s.action_type === 'SHORT SELL').length} SHORT
              </span>
            </h2>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
              <span className="text-xs text-slate-500">Stocks with clear entry signals</span>
              <SortSelect value={actionableSortOrder} onChange={setActionableSortOrder} />
            </div>
          </div>
          <ActionableTable
            stocks={sortedActionable}
            runId={result?.run_id}
            sortOrder={actionableSortOrder}
            onToggleSort={() => setActionableSortOrder(toggleSortOrder)}
          />
        </section>
      )}

      {sortedTopStocks.length > 0 && (
        <section className="rounded-[24px] border border-white/10 bg-slate-950/40 p-4 shadow-[0_25px_70px_rgba(2,6,23,0.35)] backdrop-blur-xl sm:p-5">
          <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <h2 className="text-xl font-black text-white">
              {verdictFilter === 'ALL'
                ? `All Screened Stocks (${sortedTopStocks.length})`
                : verdictFilter === 'BUYABLE'
                  ? `BUY / STRONG BUY Stocks (${sortedTopStocks.length})`
                  : `${verdictFilter} Stocks (${sortedTopStocks.length})`}
            </h2>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
              <span className="text-xs text-slate-500">Click any stock for full analysis</span>
              <SortSelect value={stockSortOrder} onChange={setStockSortOrder} />
            </div>
          </div>
          <StockTable
            stocks={sortedTopStocks}
            runId={result?.run_id}
            sortOrder={stockSortOrder}
            onToggleSort={() => setStockSortOrder(toggleSortOrder)}
          />
        </section>
      )}

      {runs.length > 0 && (
        <section className="rounded-[24px] border border-white/10 bg-slate-950/35 p-4 backdrop-blur-xl">
          <h2 className="mb-3 text-sm font-bold uppercase tracking-[0.22em] text-slate-500">Recent Runs</h2>
          <div className="flex gap-2 flex-wrap">
            {runs.map(r => (
              <button
                key={r.run_id}
                onClick={() => loadRunResults(r.run_id)}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs transition-colors ${
                  result?.run_id === r.run_id
                    ? 'bg-emerald-500/15 border border-emerald-400/35 text-emerald-300'
                    : 'border border-white/8 bg-white/5 text-slate-300 hover:bg-white/10'
                }`}
              >
                {r.status === 'completed' ? (
                  <CheckCircle className="w-3 h-3 text-emerald-400" />
                ) : (
                  <Clock className="w-3 h-3 text-amber-400" />
                )}
                <span className="font-mono">{r.run_id}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {!result && !loading && (
        <div className="rounded-[28px] border border-white/10 bg-slate-950/40 p-12 text-center backdrop-blur-xl">
          <TrendingUpIcon className="w-12 h-12 text-slate-600 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-white mb-2">Ready to Screen</h2>
          <p className="text-slate-400 mb-4">
            Click "Run Screener" to fetch data and find the top swing trading candidates
          </p>
        </div>
      )}
    </div>
  );
}

function TrendingUpIcon(props) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...props}>
      <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
      <polyline points="16 7 22 7 22 13" />
    </svg>
  );
}

function SortSelect({ value, onChange }) {
  return (
    <label className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
      <span>View Mode:</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-w-[180px] rounded-xl border border-indigo-400/40 bg-slate-950/80 px-3 py-2 text-sm font-semibold text-slate-100 shadow-inner shadow-black/30 focus:border-sky-400 focus:outline-none"
      >
        <option value={SORT_OPTIONS.DESC}>Highest to Lowest</option>
        <option value={SORT_OPTIONS.ASC}>Lowest to Highest</option>
      </select>
    </label>
  );
}


