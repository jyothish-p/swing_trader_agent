import { useState, useEffect, useMemo } from 'react';
import { Play, RefreshCw, Clock, CheckCircle, AlertCircle, Loader2 } from 'lucide-react';
import StockTable from '../components/StockTable';
import ActionableTable from '../components/ActionableTable';
import { runScreenerAsync, getScreenerRuns, getScreenerResults, getScreenerStatus } from '../lib/api';

const SORT_OPTIONS = {
  DESC: 'desc',
  ASC: 'asc',
};
const STALE_RUN_MS = 10 * 60 * 1000;
const LIVE_REFRESH_MS = 60 * 1000;

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

  useEffect(() => {
    loadRuns(true);
  }, []);

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
            setResult(normalizeScreenerResult(payload, activeRunId));
            setError('');
            setStatus('');
            setLoading(false);
            setActiveRunId(null);
            loadRuns(false);
          } catch (e) {
            if (cancelled) return;
            setError(e.response?.data?.detail || e.message || 'Failed to load screener results');
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
      } catch (e) {
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
  }, [activeRunId]);

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
        const latestCompleted = latestRuns.find((run) => run.status === 'completed');
        if (!latestCompleted) return;
        if (result?.run_id === latestCompleted.run_id) return;

        const resultsResponse = await getScreenerResults(latestCompleted.run_id);
        if (cancelled) return;

        setResult(normalizeScreenerResult(resultsResponse.data, latestCompleted.run_id));
        setError('');
      } catch (e) {
        if (!cancelled) {
          console.error('Live dashboard refresh failed:', e);
        }
      }
    };

    const intervalId = setInterval(refreshLatestRun, LIVE_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [activeRunId, latestCompletedRunId, loading, result?.run_id]);

  async function loadRuns(autoLoadLatest = false) {
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

        const latest = latestRuns.find(r => r.status === 'completed');
        if (latest) {
          await loadRunResults(latest.run_id);
        } else {
          setLoading(false);
          setStatus('');
        }
      }
    } catch (e) {
      // No runs yet
    }
  }

  async function loadRunResults(runId) {
    try {
      const res = await getScreenerResults(runId);
      setResult(normalizeScreenerResult(res.data, runId));
      setError('');
      setStatus('');
      setLoading(false);
      setActiveRunId(null);
    } catch (e) {
      console.error('Failed to load run results:', e);
      setError(e.response?.data?.detail || e.message || 'Failed to load screener results');
      setLoading(false);
      setStatus('');
      setActiveRunId(null);
    }
  }

  async function handleRunScreener(forceRefresh = false) {
    setLoading(true);
    setError('');
    setStatus('Starting screener...');

    try {
      const res = await runScreenerAsync(forceRefresh);
      setActiveRunId(res.data.run_id);
    } catch (e) {
      setError(e.response?.data?.detail || e.message || 'Screener failed');
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
  const hasVerdicts = Object.values(verdictCounts).some(v => v > 0);
  const totalStocks = Object.values(verdictCounts).reduce((a, b) => a + b, 0);
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

  const filteredActionable = useMemo(() => {
    if (!result?.actionable_stocks) return [];
    if (verdictFilter === 'BUYABLE') {
      return result.actionable_stocks.filter(s =>
        s.action_type === 'BUY' || ['STRONG BUY', 'BUY'].includes(s.verdict)
      );
    }
    if (verdictFilter === 'ALL') return result.actionable_stocks;
    return result.actionable_stocks.filter(s => s.verdict === verdictFilter);
  }, [result?.actionable_stocks, verdictFilter]);

  const sortedActionable = useMemo(
    () => sortByComposite(filteredActionable, actionableSortOrder),
    [filteredActionable, actionableSortOrder]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="max-w-3xl">
          <h1 className="text-2xl font-bold text-white">Swing Trading Dashboard</h1>
          <p className="text-sm text-slate-400 mt-1">
            Top NSE stocks scored by TITAN v20 (60%), TITAN v19 (10%), Swing AI v12.2 (10%), Swing AI v12.1 (10%) & KING v16 (10%)
          </p>
          <p className="mt-2 text-xs text-slate-500">
            We now show all 5 engine scores side by side, so the tables scroll horizontally on smaller screens instead of cramping the data.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 xl:justify-end">
          <button
            onClick={() => handleRunScreener(false)}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-600 text-white rounded-lg text-sm font-medium transition-colors"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {loading ? 'Running...' : 'Run Screener'}
          </button>
          <button
            onClick={() => handleRunScreener(true)}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-800 text-slate-300 rounded-lg text-sm transition-colors"
            title="Force re-download all data"
          >
            <RefreshCw className="w-4 h-4" />
            Full Refresh
          </button>
        </div>
      </div>

      {status && (
        <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg px-4 py-3 flex items-center gap-2">
          <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />
          <span className="text-sm text-blue-300">{status}</span>
        </div>
      )}
      {result && (
        <div className="bg-slate-800 rounded-lg px-4 py-3 border border-slate-700 flex flex-wrap items-center gap-4 text-sm">
          <span className="text-slate-300">
            Checked: <span className="font-semibold text-white">{result.universe_size ?? result.total_analyzed ?? result.top_stocks?.length ?? 0}</span>
          </span>
          <span className="text-slate-300">
            Analyzed: <span className="font-semibold text-white">{result.total_analyzed ?? displayStocks.length ?? 0}</span>
          </span>
          <span className="text-slate-300">
            Showing: <span className="font-semibold text-white">{sortedTopStocks.length}</span>
          </span>
        </div>
      )}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-400" />
          <span className="text-sm text-red-300">{error}</span>
        </div>
      )}

      {hasVerdicts && (
        <div className="flex flex-wrap gap-3">
          <button
            onClick={() => setVerdictFilter('BUYABLE')}
            className={`min-w-[120px] flex-1 rounded-lg px-3 py-2 text-center transition-all cursor-pointer border ${
              verdictFilter === 'BUYABLE'
                ? 'bg-emerald-500/20 border-emerald-400 ring-2 ring-emerald-400/30 scale-[1.02]'
                : 'bg-slate-700/50 border-slate-600 hover:border-slate-400 hover:bg-slate-700'
            }`}
          >
            <div className={`text-xl font-bold ${verdictFilter === 'BUYABLE' ? 'text-emerald-300' : 'text-slate-300'}`}>{buyLikeCount}</div>
            <div className={`text-[10px] font-medium ${verdictFilter === 'BUYABLE' ? 'text-emerald-300' : 'text-slate-400'}`}>BUY / STRONG BUY</div>
          </button>
          <button
            onClick={() => setVerdictFilter('ALL')}
            className={`min-w-[120px] flex-1 rounded-lg px-3 py-2 text-center transition-all cursor-pointer border ${
              verdictFilter === 'ALL'
                ? 'bg-blue-500/20 border-blue-400 ring-2 ring-blue-400/30 scale-[1.02]'
                : 'bg-slate-700/50 border-slate-600 hover:border-slate-400 hover:bg-slate-700'
            }`}
          >
            <div className={`text-xl font-bold ${verdictFilter === 'ALL' ? 'text-blue-300' : 'text-slate-300'}`}>{totalStocks}</div>
            <div className={`text-[10px] font-medium ${verdictFilter === 'ALL' ? 'text-blue-300' : 'text-slate-400'}`}>ALL</div>
          </button>
          {[
            { label: 'STRONG BUY', key: 'STRONG BUY', border: 'border-emerald-500/40', activeBorder: 'border-emerald-400', ring: 'ring-emerald-400/30', text: 'text-emerald-300', bg: 'bg-emerald-500/10', activeBg: 'bg-emerald-500/20' },
            { label: 'BUY', key: 'BUY', border: 'border-green-500/40', activeBorder: 'border-green-400', ring: 'ring-green-400/30', text: 'text-green-300', bg: 'bg-green-500/10', activeBg: 'bg-green-500/20' },
            { label: 'HOLD', key: 'HOLD', border: 'border-amber-500/40', activeBorder: 'border-amber-400', ring: 'ring-amber-400/30', text: 'text-amber-300', bg: 'bg-amber-500/10', activeBg: 'bg-amber-500/20' },
            { label: 'WAIT', key: 'WAIT', border: 'border-orange-500/40', activeBorder: 'border-orange-400', ring: 'ring-orange-400/30', text: 'text-orange-300', bg: 'bg-orange-500/10', activeBg: 'bg-orange-500/20' },
            { label: 'AVOID', key: 'AVOID', border: 'border-red-500/40', activeBorder: 'border-red-400', ring: 'ring-red-400/30', text: 'text-red-300', bg: 'bg-red-500/10', activeBg: 'bg-red-500/20' },
          ].map(v => {
            const isActive = verdictFilter === v.key;
            return (
              <button
                key={v.key}
                onClick={() => setVerdictFilter(isActive ? 'ALL' : v.key)}
                className={`min-w-[112px] flex-1 rounded-lg px-3 py-2 text-center transition-all cursor-pointer border ${
                  isActive
                    ? `${v.activeBg} ${v.activeBorder} ring-2 ${v.ring} scale-[1.02]`
                    : `${v.bg} ${v.border} hover:${v.activeBorder} hover:brightness-125`
                }`}
              >
                <div className={`text-xl font-bold ${v.text}`}>{verdictCounts[v.key]}</div>
                <div className={`text-[10px] font-medium ${v.text} opacity-75`}>{v.label}</div>
              </button>
            );
          })}
        </div>
      )}

      {verdictFilter !== 'ALL' && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">
            Filtering by: <span className="font-semibold text-white">{verdictFilter === 'BUYABLE' ? 'BUY / STRONG BUY' : verdictFilter}</span>
          </span>
          <button
            onClick={() => setVerdictFilter('BUYABLE')}
            className="text-xs text-blue-400 hover:text-blue-300 underline"
          >
            Reset filter
          </button>
        </div>
      )}

      {result && buyLikeCount === 0 && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-3">
          <div className="text-sm text-amber-300 font-medium">This run currently has no BUY or STRONG BUY candidates.</div>
          <div className="text-xs text-slate-400 mt-1">
            Current verdicts: {verdictCounts['WAIT']} WAIT, {verdictCounts['AVOID']} AVOID.
          </div>
        </div>
      )}

      {sortedActionable.length > 0 && (
        <div className="bg-slate-800 rounded-lg p-4 border border-emerald-500/20">
          <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <h2 className="text-lg font-semibold text-white">
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
        </div>
      )}

      {sortedTopStocks.length > 0 && (
        <div className="bg-slate-800 rounded-lg p-4">
          <div className="mb-3 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <h2 className="text-lg font-semibold text-white">
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
        </div>
      )}

      {runs.length > 0 && (
        <div className="bg-slate-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-slate-400 mb-2">Recent Runs</h2>
          <div className="flex gap-2 flex-wrap">
            {runs.map(r => (
              <button
                key={r.run_id}
                onClick={() => loadRunResults(r.run_id)}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs transition-colors ${
                  result?.run_id === r.run_id
                    ? 'bg-emerald-600/30 border border-emerald-500/40 text-emerald-300'
                    : 'bg-slate-700/50 hover:bg-slate-700 text-slate-300'
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
        </div>
      )}

      {!result && !loading && (
        <div className="bg-slate-800 rounded-lg p-12 text-center">
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
      <span>Composite</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-w-[160px] rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"
      >
        <option value={SORT_OPTIONS.DESC}>Highest to Lowest</option>
        <option value={SORT_OPTIONS.ASC}>Lowest to Highest</option>
      </select>
    </label>
  );
}


