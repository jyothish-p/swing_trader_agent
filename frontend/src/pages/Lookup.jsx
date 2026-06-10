import { useEffect, useMemo, useState } from 'react';
import { Search, Loader2, ArrowRight, History, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { lookupStock } from '../lib/api';

const LOOKUP_HISTORY_KEY = 'swingtrader_lookup_history';
const MAX_HISTORY_ITEMS = 12;

export default function Lookup() {
  const [symbol, setSymbol] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [history, setHistory] = useState([]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(LOOKUP_HISTORY_KEY) || '[]');
      if (Array.isArray(saved)) {
        setHistory(saved.filter(item => typeof item === 'string'));
      }
    } catch {
      setHistory([]);
    }
  }, []);

  function persistHistory(nextHistory) {
    setHistory(nextHistory);
    localStorage.setItem(LOOKUP_HISTORY_KEY, JSON.stringify(nextHistory));
  }

  function saveSearchHistory(rawSymbol) {
    const normalized = rawSymbol.trim().toUpperCase();
    if (!normalized) return;
    const nextHistory = [
      normalized,
      ...history.filter(item => item !== normalized),
    ].slice(0, MAX_HISTORY_ITEMS);
    persistHistory(nextHistory);
  }

  function removeHistoryItem(itemToRemove) {
    persistHistory(history.filter(item => item !== itemToRemove));
  }

  function clearHistory() {
    persistHistory([]);
  }

  const filteredHistory = useMemo(() => {
    const normalized = symbol.trim().toUpperCase();
    if (!normalized) return history;
    return history.filter(item => item.includes(normalized));
  }, [history, symbol]);

  async function handleLookup(e) {
    e.preventDefault();
    if (!symbol.trim()) return;
    const lookupSymbol = symbol.trim().toUpperCase();
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const res = await lookupStock(lookupSymbol);
      saveSearchHistory(lookupSymbol);
      setSymbol(lookupSymbol);
      setResult(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to look up stock. Check the symbol.');
    }
    setLoading(false);
  }

  const mp = result?.mate_pro;
  const comp = mp?.composite;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-white">Stock Lookup</h1>
        <p className="text-sm text-slate-400 mt-1">
          Search any NSE stock — downloads data and runs full MATE-PRO analysis
        </p>
      </div>

      {/* Search Bar */}
      <form onSubmit={handleLookup} className="flex gap-3">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            list="lookup-history"
            value={symbol}
            onChange={e => setSymbol(e.target.value.toUpperCase())}
            placeholder="Enter NSE symbol (e.g. RELIANCE, TCS, INFY)"
            className="w-full pl-10 pr-4 py-3 bg-slate-800 text-white rounded-lg border border-slate-600 text-sm focus:border-emerald-500 focus:outline-none"
          />
          <datalist id="lookup-history">
            {history.map(item => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </div>
        <button type="submit" disabled={loading || !symbol.trim()}
          className="flex items-center gap-2 px-6 py-3 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-600 text-white rounded-lg text-sm font-medium">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Analyze
        </button>
      </form>

      {history.length > 0 && (
        <div className="bg-slate-800/70 border border-slate-700 rounded-lg px-4 py-3">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div className="flex items-center gap-2 text-sm text-slate-300">
              <History className="w-4 h-4 text-slate-400" />
              Recent searches
            </div>
            <button
              type="button"
              onClick={clearHistory}
              className="text-xs text-slate-400 hover:text-white"
            >
              Clear all
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {filteredHistory.length > 0 ? filteredHistory.map(item => (
              <div
                key={item}
                className="inline-flex items-center gap-1 rounded-full border border-slate-600 bg-slate-700/70 px-3 py-1.5 text-sm"
              >
                <button
                  type="button"
                  onClick={() => setSymbol(item)}
                  className="text-slate-200 hover:text-white"
                >
                  {item}
                </button>
                <button
                  type="button"
                  onClick={() => removeHistoryItem(item)}
                  className="text-slate-500 hover:text-red-300"
                  aria-label={`Remove ${item} from search history`}
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            )) : (
              <div className="text-sm text-slate-500">No matching past searches</div>
            )}
          </div>
        </div>
      )}

      {error && (
        <div className="bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {/* Data fetched but MATE-PRO unavailable */}
      {result && !mp && (
        <div className="bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-3">
          <p className="text-sm text-amber-300">
            Data fetched for <span className="font-bold">{result.symbol}</span> ({result.candle_count || 0} days)
            but MATE-PRO analysis could not be completed.
            {result.error && <span className="block mt-1 text-xs text-amber-400">{result.error}</span>}
          </p>
          <Link to={`/stock/${result.symbol}`}
            className="inline-flex items-center gap-1 mt-2 text-sm text-blue-400 hover:text-blue-300 no-underline">
            View available data <ArrowRight className="w-3 h-3" />
          </Link>
        </div>
      )}

      {/* Results */}
      {result && mp && (
        <div className="space-y-4">
          {/* Header with verdict */}
          <div className="bg-slate-800 rounded-lg p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-xl font-bold text-white">{result.symbol}</h2>
                <span className="text-slate-400 text-sm font-mono">₹{mp.cmp?.toLocaleString('en-IN')}</span>
              </div>
              <Link to={`/stock/${result.symbol}`}
                className="flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300 no-underline">
                Full Detail <ArrowRight className="w-4 h-4" />
              </Link>
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="text-center">
                <div className="text-3xl font-bold text-white">{comp.composite_score}</div>
                <div className="text-xs text-slate-400 mt-1">Composite Score</div>
              </div>
              <div className="text-center">
                <div className={`text-2xl font-bold ${
                  comp.consensus_verdict === 'STRONG BUY' ? 'text-emerald-400' :
                  comp.consensus_verdict === 'BUY' ? 'text-green-400' :
                  comp.consensus_verdict === 'HOLD' ? 'text-amber-400' :
                  comp.consensus_verdict === 'WAIT' ? 'text-orange-400' : 'text-red-400'
                }`}>{comp.consensus_verdict}</div>
                <div className="text-xs text-slate-400 mt-1">Verdict ({comp.agreement})</div>
              </div>
              <div className="text-center">
                <div className="text-3xl font-bold text-blue-400">{comp.composite_probability}%</div>
                <div className="text-xs text-slate-400 mt-1">Probability</div>
              </div>
            </div>
          </div>

          {/* Model Scores */}
          <div className="grid grid-cols-3 gap-4">
            {Object.entries(mp.models).map(([key, model]) => (
              <div key={key} className="bg-slate-800 rounded-lg p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-semibold text-white">{model.model}</span>
                  <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                    model.verdict === 'STRONG BUY' ? 'bg-emerald-500/20 text-emerald-300' :
                    model.verdict === 'BUY' ? 'bg-green-500/20 text-green-300' :
                    model.verdict === 'HOLD' ? 'bg-amber-500/20 text-amber-300' :
                    'bg-red-500/20 text-red-300'
                  }`}>{model.verdict}</span>
                </div>
                <div className="text-2xl font-bold text-white">
                  {model.scanner_score || model.selection_total}/100
                </div>
                <div className="mt-2 space-y-1">
                  {Object.entries(model.components).map(([cKey, comp]) => (
                    <div key={cKey} className="flex items-center gap-2">
                      <span className="text-[10px] text-slate-400 w-20 truncate">
                        {cKey.replace(/^[A-Z]\d_/, '').replace(/_/g, ' ')}
                      </span>
                      <div className="flex-1 h-1 bg-slate-700 rounded-full overflow-hidden">
                        <div className="h-full bg-blue-400 rounded-full"
                          style={{ width: `${(comp.score / comp.max) * 100}%` }} />
                      </div>
                      <span className="text-[10px] text-slate-400 font-mono">{comp.score}/{comp.max}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {/* Trade Plan */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-slate-800 rounded-lg p-4 border border-blue-500/20">
              <h3 className="text-sm font-semibold text-blue-300 mb-3">Scanner Plan (10-15%)</h3>
              <div className="space-y-1.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-slate-400">Entry</span>
                  <span className="text-white font-mono">₹{mp.trade_plans.scanner_plan.entry_breakout?.toLocaleString('en-IN')}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-red-400">SL</span>
                  <span className="text-red-300 font-mono">₹{mp.trade_plans.scanner_plan.stop_loss?.toLocaleString('en-IN')} ({mp.trade_plans.scanner_plan.sl_pct}%)</span>
                </div>
                {Object.entries(mp.trade_plans.scanner_plan.targets || {}).map(([k, v]) => (
                  <div key={k} className="flex justify-between">
                    <span className="text-emerald-400">{k} (+{v.pct}%)</span>
                    <span className="text-emerald-300 font-mono">₹{v.price?.toLocaleString('en-IN')}</span>
                  </div>
                ))}
                <div className="flex justify-between border-t border-slate-700 pt-1">
                  <span className="text-slate-400">Action</span>
                  <span className={`font-bold ${
                    mp.trade_plans.scanner_plan.action === 'TRADE' ? 'text-emerald-400' : 'text-amber-400'
                  }`}>{mp.trade_plans.scanner_plan.action}</span>
                </div>
              </div>
            </div>
            <div className="bg-slate-800 rounded-lg p-4">
              <h3 className="text-sm font-semibold text-slate-300 mb-3">Context</h3>
              <div className="space-y-1.5 text-sm">
                <div className="flex justify-between">
                  <span className="text-slate-400">Structure</span>
                  <span className="text-white">{mp.context.daily_structure} / {mp.context.weekly_structure}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Phase</span>
                  <span className="text-white capitalize">{mp.context.phase}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Pattern</span>
                  <span className="text-white">{mp.context.pattern?.replace(/_/g, ' ')}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">RSI</span>
                  <span className="text-white">{mp.metrics.rsi}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">EMA Stack</span>
                  <span className={mp.metrics.ema_stack === 'bullish' ? 'text-emerald-400' : 'text-amber-400'}>
                    {mp.metrics.ema_stack}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Vol Ratio</span>
                  <span className="text-white">{mp.metrics.vol_ratio}x</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
