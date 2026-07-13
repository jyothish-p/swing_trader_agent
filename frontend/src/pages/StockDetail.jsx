import { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { ArrowLeft, FileText, Loader2 } from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, ComposedChart, Area, ReferenceLine
} from 'recharts';
import SignalBadge from '../components/SignalBadge';
import { getAnalysis, getChartData, getPriceHistory, getMatePro } from '../lib/api';

const LATEST_DASHBOARD_RUN_KEY = 'swingTraderLatestDashboardRunId';

export default function StockDetail() {
  const { symbol } = useParams();
  const [searchParams] = useSearchParams();
  const runId = searchParams.get('run_id');
  const savedRunId = window.localStorage.getItem(LATEST_DASHBOARD_RUN_KEY);
  const effectiveRunId = runId || savedRunId;

  const [timeframe, setTimeframe] = useState('daily');
  const [analysis, setAnalysis] = useState(null);
  const [chartData, setChartData] = useState(null);
  const [priceHistory, setPriceHistory] = useState(null);
  const [matePro, setMatePro] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('mate-pro');
  const [selectedEngineKey, setSelectedEngineKey] = useState(null);

  const loadData = useCallback(async (isCancelled = () => false) => {
    setLoading(true);
    setMatePro(null);
    try {
      const [analysisRes, chartRes, historyRes] = await Promise.all([
        getAnalysis(symbol, effectiveRunId).catch(() => null),
        getChartData(symbol, timeframe, 180).catch(() => null),
        getPriceHistory(symbol, 30).catch(() => null),
      ]);
      if (isCancelled()) return;
      if (analysisRes) setAnalysis(analysisRes.data);
      if (chartRes) setChartData(chartRes.data);
      if (historyRes) setPriceHistory(historyRes.data);

      // Fetch MATE-PRO separately so we can see errors
      try {
        const mateProRes = await getMatePro(symbol, effectiveRunId);
        console.log('MATE-PRO response:', mateProRes.data);
        if (isCancelled()) return;
        if (mateProRes?.data) setMatePro(mateProRes.data);
      } catch (mpErr) {
        console.error('MATE-PRO error:', mpErr.response?.status, mpErr.response?.data, mpErr.message);
      }
    } catch (e) {
      console.error(e);
    }
    if (isCancelled()) return;
    setLoading(false);
  }, [effectiveRunId, symbol, timeframe]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      loadData(() => cancelled);
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [loadData]);

  const ta = analysis?.analysis?.[timeframe] || analysis?.analysis?.daily || {};
  const chart = chartData?.chart_data || [];
  const modelEntries = orderedModelEntries(matePro?.models);
  const selectedEngine = selectedEngineKey ? matePro?.models?.[selectedEngineKey] : null;
  const sectorMomentum = matePro?.context?.sector_momentum_score ?? 0;
  const sectorMomentumTone = sectorMomentum >= 6
    ? 'text-emerald-400 border-emerald-500/30 bg-emerald-500/10'
    : sectorMomentum >= 3
      ? 'text-amber-400 border-amber-500/30 bg-amber-500/10'
      : 'text-red-400 border-red-500/30 bg-red-500/10';

  return (
    <div className="space-y-6">
      {/* Back + Header */}
      <div className="flex items-center gap-4">
        <Link to="/" className="text-slate-400 hover:text-white">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-white">{symbol}</h1>
          <div className="flex items-center gap-3 mt-1">
            {ta.signal && <SignalBadge signal={ta.signal} />}
            {ta.cmp && <span className="text-slate-300 font-mono text-lg">₹{ta.cmp?.toLocaleString('en-IN')}</span>}
            {ta.rsi && <span className="text-xs text-slate-400">RSI: {ta.rsi}</span>}
          </div>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-8 h-8 text-slate-400 animate-spin" />
        </div>
      )}

      {!loading && (
        <>
          {/* Timeframe Toggle */}
          <div className="flex gap-2">
            {['daily', 'weekly', 'monthly'].map(tf => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
                  timeframe === tf
                    ? 'bg-emerald-600 text-white'
                    : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                }`}
              >
                {tf.charAt(0).toUpperCase() + tf.slice(1)}
              </button>
            ))}
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b border-slate-700 overflow-x-auto">
            {['mate-pro', 'report', 'chart', 'indicators', 'levels', 'history'].map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`shrink-0 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab
                    ? 'border-emerald-400 text-white'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                {tab === 'mate-pro' ? 'MATE-PRO' : tab === 'report' ? 'Full Report' : tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>

          {/* MATE-PRO Tab */}
          {activeTab === 'mate-pro' && matePro && (
            <div className="space-y-4">
              {/* Composite Verdict */}
              <div className="bg-slate-800 rounded-lg p-5">
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-lg font-semibold text-white">5-Engine Consensus</h3>
                  <span className={`text-sm font-mono px-2 py-0.5 rounded ${
                    matePro.composite.agreement === 'UNANIMOUS' ? 'bg-emerald-500/20 text-emerald-300' :
                    matePro.composite.agreement === 'MAJORITY' ? 'bg-amber-500/20 text-amber-300' :
                    'bg-red-500/20 text-red-300'
                  }`}>{matePro.composite.agreement}</span>
                </div>
                <div className="grid grid-cols-3 gap-4 mb-4">
                  <div className="text-center">
                    <div className="text-3xl font-bold text-white">{matePro.composite.composite_score}</div>
                    <div className="text-xs text-slate-400 mt-1">Composite Score</div>
                  </div>
                  <div className="text-center">
                    <div className={`text-3xl font-bold ${
                      matePro.composite.consensus_verdict === 'STRONG BUY' ? 'text-emerald-400' :
                      matePro.composite.consensus_verdict === 'BUY' ? 'text-green-400' :
                      matePro.composite.consensus_verdict === 'HOLD' ? 'text-amber-400' :
                      matePro.composite.consensus_verdict === 'WAIT' ? 'text-orange-400' : 'text-red-400'
                    }`}>{matePro.composite.consensus_verdict}</div>
                    <div className="text-xs text-slate-400 mt-1">Verdict</div>
                  </div>
                  <div className="text-center">
                    <div className="text-3xl font-bold text-blue-400">{matePro.composite.composite_probability}%</div>
                    <div className="text-xs text-slate-400 mt-1">Probability</div>
                  </div>
                </div>
                {matePro.one_line_verdict && (
                  <div className="rounded-lg border border-slate-700 bg-slate-900/50 px-4 py-3 text-sm leading-relaxed text-slate-200">
                    <span className="font-semibold text-white">One-line verdict:</span> {matePro.one_line_verdict}
                  </div>
                )}
              </div>

              {/* Individual Model Scores */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {modelEntries.map(([key, model]) => (
                  <button
                    type="button"
                    key={key}
                    onClick={() => setSelectedEngineKey(selectedEngineKey === key ? null : key)}
                    className={`rounded-lg border p-4 text-left transition-all ${
                      selectedEngineKey === key
                        ? 'border-emerald-400 bg-slate-800 ring-2 ring-emerald-400/20'
                        : 'border-transparent bg-slate-800 hover:border-slate-600 hover:bg-slate-800/80'
                    }`}
                  >
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-sm font-semibold text-white">{model.model}</h3>
                      <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                        model.verdict === 'STRONG BUY' ? 'bg-emerald-500/20 text-emerald-300' :
                        model.verdict === 'BUY' ? 'bg-green-500/20 text-green-300' :
                        model.verdict === 'HOLD' ? 'bg-amber-500/20 text-amber-300' :
                        model.verdict === 'WAIT' ? 'bg-orange-500/20 text-orange-300' :
                        'bg-red-500/20 text-red-300'
                      }`}>{model.verdict}</span>
                    </div>
                    <div className="text-2xl font-bold text-white mb-3">
                      {model.scanner_score || model.selection_total}/100
                    </div>
                    <div className="mb-3 text-[11px] font-medium text-emerald-300">
                      {selectedEngineKey === key ? 'Report chart open' : 'Click for full report chart'}
                    </div>
                    {/* Component breakdown */}
                    <div className="space-y-1.5">
                      {Object.entries(model.components).map(([cKey, comp]) => (
                        <div key={cKey} className="flex items-center gap-2">
                          <span className="text-[10px] text-slate-400 w-24 truncate" title={cKey.replace(/_/g, ' ')}>
                            {cKey.replace(/^[A-Z]\d_/, '').replace(/_/g, ' ')}
                          </span>
                          <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden">
                            <div
                              className="h-full bg-blue-400 rounded-full"
                              style={{ width: `${(comp.score / comp.max) * 100}%` }}
                            />
                          </div>
                          <span className="text-[10px] text-slate-400 font-mono w-8 text-right">
                            {comp.score}/{comp.max}
                          </span>
                        </div>
                      ))}
                    </div>
                    {/* Penalties */}
                    {model.penalties > 0 && (
                      <div className="mt-2 text-xs text-red-400">
                        Penalties: -{model.penalties} ({model.penalty_reasons?.join(', ')})
                      </div>
                    )}
                    {/* Positional */}
                    {model.positional_score !== undefined && (
                      <div className="mt-2 pt-2 border-t border-slate-700">
                        <span className="text-xs text-slate-400">Positional: </span>
                        <span className="text-xs font-mono text-white">
                          {model.positional_score}/{model.positional_max}
                        </span>
                        {model.positional_class && (
                          <span className="text-xs text-slate-500 ml-1">({model.positional_class})</span>
                        )}
                      </div>
                    )}
                    {model.probability_pct !== undefined && (
                      <div className="mt-1">
                        <span className="text-xs text-slate-400">Probability: </span>
                        <span className="text-xs font-mono text-blue-300">{model.probability_pct || model.final_probability}%</span>
                      </div>
                    )}
                  </button>
                ))}
              </div>

              {selectedEngine && (
                <EngineFullReportPanel
                  model={selectedEngine}
                  matePro={matePro}
                  onClose={() => setSelectedEngineKey(null)}
                />
              )}

              <div className={`rounded-lg border px-4 py-3 ${sectorMomentumTone}`}>
                <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
                  <div>
                    <div className="text-xs uppercase tracking-wide opacity-80">Sector Momentum</div>
                    <div className="text-lg font-semibold">
                      {sectorMomentum}/10
                      {matePro.context.sector_index ? ` • ${matePro.context.sector_index}` : ''}
                    </div>
                  </div>
                  <div className="text-sm opacity-90">
                    RSI {matePro.context.sector_weekly_rsi != null ? Number(matePro.context.sector_weekly_rsi).toFixed(1) : '—'}
                    {' • '}
                    {matePro.context.sector_structure || '—'}
                    {' • '}
                    {matePro.context.sector_positive_peers ?? 0} positive peers
                    {' • '}
                    1M {matePro.context.sector_perf_1m != null ? `${Number(matePro.context.sector_perf_1m).toFixed(1)}%` : '—'}
                    {' • '}
                    3M {matePro.context.sector_perf_3m != null ? `${Number(matePro.context.sector_perf_3m).toFixed(1)}%` : '—'}
                  </div>
                </div>
              </div>

              {/* Market Context */}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-semibold text-white mb-3">Market Context</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <div>
                    <div className="text-xs text-slate-400">Structure</div>
                    <div className="text-sm text-white">{matePro.context.daily_structure} / {matePro.context.weekly_structure}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Phase</div>
                    <div className="text-sm text-white capitalize">{matePro.context.phase}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Pattern</div>
                    <div className="text-sm text-white">{matePro.context.pattern?.replace(/_/g, ' ')}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Volatility</div>
                    <div className="text-sm text-white capitalize">{matePro.context.volatility_state}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">EMA Stack</div>
                    <div className={`text-sm ${matePro.metrics.ema_stack === 'bullish' ? 'text-emerald-400' : 'text-amber-400'}`}>
                      {matePro.metrics.ema_stack}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">RSI</div>
                    <div className="text-sm text-white">{matePro.metrics.rsi}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">MACD Cross</div>
                    <div className={`text-sm ${matePro.metrics.macd_crossover ? 'text-emerald-400' : 'text-slate-400'}`}>
                      {matePro.metrics.macd_crossover ? 'YES' : 'No'}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Vol Ratio</div>
                    <div className={`text-sm ${matePro.metrics.vol_ratio >= 1.5 ? 'text-amber-400 font-bold' : 'text-white'}`}>
                      {matePro.metrics.vol_ratio}x
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Sector Index</div>
                    <div className="text-sm text-white">{matePro.context.sector_index || '—'}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Sector Momentum</div>
                    <div className={`text-sm font-semibold ${
                      (matePro.context.sector_momentum_score ?? 0) >= 6 ? 'text-emerald-400' :
                      (matePro.context.sector_momentum_score ?? 0) >= 3 ? 'text-amber-400' :
                      'text-red-400'
                    }`}>
                      {matePro.context.sector_momentum_score ?? 0}/10
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Sector RSI / Structure</div>
                    <div className="text-sm text-white">
                      {matePro.context.sector_weekly_rsi != null ? Number(matePro.context.sector_weekly_rsi).toFixed(1) : '—'}
                      {' / '}
                      {matePro.context.sector_structure || '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Sector Breadth</div>
                    <div className="text-sm text-white">
                      {matePro.context.sector_positive_peers ?? 0} positive peers
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Sector 1M / 3M</div>
                    <div className="text-sm text-white">
                      {matePro.context.sector_perf_1m != null ? `${Number(matePro.context.sector_perf_1m).toFixed(1)}%` : '—'}
                      {' / '}
                      {matePro.context.sector_perf_3m != null ? `${Number(matePro.context.sector_perf_3m).toFixed(1)}%` : '—'}
                    </div>
                  </div>
                </div>
              </div>

              {/* Trade Plans */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Scanner Plan */}
                <div className="bg-slate-800 rounded-lg p-4 border border-blue-500/20">
                  <h3 className="text-sm font-semibold text-blue-300 mb-3">Scanner Trade Plan (10-15%)</h3>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Breakout Entry</span>
                      <span className="text-white font-mono">₹{matePro.trade_plans.scanner_plan.entry_breakout?.toLocaleString('en-IN')}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Retest Zone</span>
                      <span className="text-white font-mono">
                        ₹{matePro.trade_plans.scanner_plan.entry_retest_zone?.[0]?.toLocaleString('en-IN')} –
                        ₹{matePro.trade_plans.scanner_plan.entry_retest_zone?.[1]?.toLocaleString('en-IN')}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-red-400">Stop Loss</span>
                      <span className="text-red-300 font-mono">
                        ₹{matePro.trade_plans.scanner_plan.stop_loss?.toLocaleString('en-IN')} ({matePro.trade_plans.scanner_plan.sl_pct}%)
                      </span>
                    </div>
                    <div className="border-t border-slate-700 pt-2">
                      {Object.entries(matePro.trade_plans.scanner_plan.targets || {}).map(([tKey, tVal]) => (
                        <div key={tKey} className="flex justify-between py-0.5">
                          <span className="text-emerald-400">{tKey} (+{tVal.pct}%)</span>
                          <span className="text-emerald-300 font-mono">₹{tVal.price?.toLocaleString('en-IN')}</span>
                        </div>
                      ))}
                    </div>
                    <div className="flex justify-between border-t border-slate-700 pt-2">
                      <span className="text-slate-400">RR to T2</span>
                      <span className="text-white font-mono">1:{matePro.trade_plans.scanner_plan.rr_t2}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-slate-400">Action</span>
                      <span className={`font-bold ${
                        matePro.trade_plans.scanner_plan.action === 'TRADE' ? 'text-emerald-400' :
                        matePro.trade_plans.scanner_plan.action === 'WAIT RETEST' ? 'text-amber-400' : 'text-red-400'
                      }`}>{matePro.trade_plans.scanner_plan.action}</span>
                    </div>
                  </div>
                </div>

                {/* Positional Plan */}
                <div className="bg-slate-800 rounded-lg p-4 border border-purple-500/20">
                  <h3 className="text-sm font-semibold text-purple-300 mb-3">Positional Trade Plan (15-25%)</h3>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-slate-400">Entry Zone</span>
                      <span className="text-white font-mono">
                        ₹{matePro.trade_plans.positional_plan.entry_zone?.[0]?.toLocaleString('en-IN')} –
                        ₹{matePro.trade_plans.positional_plan.entry_zone?.[1]?.toLocaleString('en-IN')}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-red-400">Stop Loss</span>
                      <span className="text-red-300 font-mono">
                        ₹{matePro.trade_plans.positional_plan.stop_loss?.toLocaleString('en-IN')} ({matePro.trade_plans.positional_plan.sl_pct}%)
                      </span>
                    </div>
                    <div className="border-t border-slate-700 pt-2">
                      {Object.entries(matePro.trade_plans.positional_plan.targets || {}).map(([tKey, tVal]) => (
                        <div key={tKey} className="flex justify-between py-0.5">
                          <span className="text-emerald-400">{tKey} (+{tVal.pct}%)</span>
                          <span className="text-emerald-300 font-mono">₹{tVal.price?.toLocaleString('en-IN')}</span>
                        </div>
                      ))}
                    </div>
                    <div className="flex justify-between border-t border-slate-700 pt-2">
                      <span className="text-slate-400">Hold Rule</span>
                      <span className="text-white text-xs">{matePro.trade_plans.positional_plan.hold_rule}</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Key Levels */}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-semibold text-white mb-3">Key Levels</h3>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="text-xs text-slate-400 mb-2">Supports</div>
                    {matePro.levels.supports?.map((s, i) => (
                      <div key={i} className="text-sm font-mono text-green-400 py-0.5">S{i+1}: ₹{s?.toLocaleString('en-IN')}</div>
                    ))}
                  </div>
                  <div>
                    <div className="text-xs text-slate-400 mb-2">Resistances</div>
                    {matePro.levels.resistances?.map((r, i) => (
                      <div key={i} className="text-sm font-mono text-red-400 py-0.5">R{i+1}: ₹{r?.toLocaleString('en-IN')}</div>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4 mt-3 pt-3 border-t border-slate-700">
                  <div>
                    <span className="text-xs text-slate-400">Trigger: </span>
                    <span className="text-sm font-mono text-amber-400 font-bold">₹{matePro.levels.trigger?.toLocaleString('en-IN')}</span>
                  </div>
                  <div>
                    <span className="text-xs text-slate-400">Invalidation: </span>
                    <span className="text-sm font-mono text-red-400 font-bold">₹{matePro.levels.invalidation?.toLocaleString('en-IN')}</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'mate-pro' && !matePro && (
            <div className="bg-slate-800 rounded-lg p-8 text-center">
              <p className="text-slate-400">MATE-PRO analysis not available. Run the screener first.</p>
            </div>
          )}

          {/* Full Report Tab */}
          {activeTab === 'report' && matePro && (
            <FullReport symbol={symbol} matePro={matePro} ta={ta} />
          )}

          {activeTab === 'report' && !matePro && (
            <div className="bg-slate-800 rounded-lg p-8 text-center">
              <p className="text-slate-400">Full report is available after MATE-PRO analysis loads.</p>
            </div>
          )}

          {/* Chart Tab */}
          {activeTab === 'chart' && chart.length > 0 && (
            <div className="space-y-4">
              {/* Price + EMA Chart */}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-medium text-slate-400 mb-3">Price & Moving Averages</h3>
                <ResponsiveContainer width="100%" height={350}>
                  <ComposedChart data={chart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" />
                    <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10, fill: '#94a3b8' }} />
                    <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }} />
                    {chart[0]?.bb_upper && (
                      <Area dataKey="bb_upper" stroke="none" fill="#6366f1" fillOpacity={0.05} />
                    )}
                    {chart[0]?.bb_lower && (
                      <Area dataKey="bb_lower" stroke="none" fill="#6366f1" fillOpacity={0.05} />
                    )}
                    <Line type="monotone" dataKey="close" stroke="#22c55e" strokeWidth={2} dot={false} name="Close" />
                    <Line type="monotone" dataKey="ema_20" stroke="#f59e0b" strokeWidth={1} dot={false} name="EMA 20" strokeDasharray="4 2" />
                    <Line type="monotone" dataKey="ema_50" stroke="#3b82f6" strokeWidth={1} dot={false} name="EMA 50" strokeDasharray="4 2" />
                    {chart[0]?.bb_upper && (
                      <Line type="monotone" dataKey="bb_upper" stroke="#818cf8" strokeWidth={1} dot={false} name="BB Upper" strokeDasharray="2 2" />
                    )}
                    {chart[0]?.bb_lower && (
                      <Line type="monotone" dataKey="bb_lower" stroke="#818cf8" strokeWidth={1} dot={false} name="BB Lower" strokeDasharray="2 2" />
                    )}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* Volume Chart */}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-medium text-slate-400 mb-3">Volume</h3>
                <ResponsiveContainer width="100%" height={150}>
                  <BarChart data={chart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} />
                    <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }} />
                    <Bar dataKey="volume" fill="#3b82f6" fillOpacity={0.6} />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* RSI Chart */}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-medium text-slate-400 mb-3">RSI ({ta.rsi_signal})</h3>
                <ResponsiveContainer width="100%" height={150}>
                  <LineChart data={chart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#94a3b8' }} />
                    <ReferenceLine y={70} stroke="#ef4444" strokeDasharray="3 3" />
                    <ReferenceLine y={30} stroke="#22c55e" strokeDasharray="3 3" />
                    <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }} />
                    <Line type="monotone" dataKey="rsi" stroke="#a78bfa" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* MACD Chart */}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-medium text-slate-400 mb-3">
                  MACD {ta.macd_crossover && ta.macd_crossover !== 'none' && (
                    <span className={ta.macd_crossover === 'bullish' ? 'text-emerald-400' : 'text-red-400'}>
                      ({ta.macd_crossover} crossover)
                    </span>
                  )}
                </h3>
                <ResponsiveContainer width="100%" height={150}>
                  <ComposedChart data={chart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                    <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#94a3b8' }} interval="preserveStartEnd" />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} />
                    <ReferenceLine y={0} stroke="#475569" />
                    <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: '8px' }} />
                    <Bar dataKey="macd_histogram" fill="#6366f1" fillOpacity={0.5} />
                    <Line type="monotone" dataKey="macd" stroke="#22c55e" strokeWidth={1.5} dot={false} name="MACD" />
                    <Line type="monotone" dataKey="macd_signal" stroke="#ef4444" strokeWidth={1.5} dot={false} name="Signal" />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Indicators Tab */}
          {activeTab === 'indicators' && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <IndicatorCard title="Moving Averages" items={[
                { label: 'EMA 20', value: ta.ema_20 },
                { label: 'EMA 50', value: ta.ema_50 },
                { label: 'EMA 100', value: ta.ema_100 },
                { label: 'EMA 200', value: ta.ema_200 },
                { label: 'SMA 20', value: ta.sma_20 },
                { label: 'SMA 50', value: ta.sma_50 },
              ]} />
              <IndicatorCard title="Bollinger Bands" items={[
                { label: 'Upper', value: ta.bb_upper },
                { label: 'Middle', value: ta.bb_middle },
                { label: 'Lower', value: ta.bb_lower },
                { label: 'Width', value: ta.bb_width, suffix: '%' },
              ]} />
              <IndicatorCard title="RSI" items={[
                { label: 'RSI (14)', value: ta.rsi },
                { label: 'Signal', value: ta.rsi_signal, isText: true },
              ]} />
              <IndicatorCard title="MACD" items={[
                { label: 'MACD', value: ta.macd },
                { label: 'Signal', value: ta.macd_signal_line },
                { label: 'Histogram', value: ta.macd_histogram },
                { label: 'Crossover', value: ta.macd_crossover, isText: true },
              ]} />
              <IndicatorCard title="VWAP & Crosses" items={[
                { label: 'VWAP', value: ta.vwap },
                { label: 'Golden Cross', value: ta.golden_cross ? 'YES' : 'No', isText: true },
                { label: 'Death Cross', value: ta.death_cross ? 'YES' : 'No', isText: true },
              ]} />
              <IndicatorCard title="Signal" items={[
                { label: 'Overall', value: ta.signal, isText: true },
                { label: 'Score', value: ta.signal_score },
              ]} />
            </div>
          )}

          {/* Levels Tab */}
          {activeTab === 'levels' && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {ta.fib_levels && (
                <div className="bg-slate-800 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-slate-400 mb-3">Fibonacci Levels</h3>
                  {Object.entries(ta.fib_levels).map(([level, price]) => (
                    <div key={level} className="flex justify-between py-1 border-b border-slate-700/50">
                      <span className="text-xs text-slate-400">{level}</span>
                      <span className="text-sm font-mono text-white">₹{price?.toLocaleString('en-IN')}</span>
                    </div>
                  ))}
                </div>
              )}
              {ta.gann_levels && (
                <div className="bg-slate-800 rounded-lg p-4">
                  <h3 className="text-sm font-medium text-slate-400 mb-3">Gann Levels</h3>
                  {Object.entries(ta.gann_levels).map(([level, price]) => (
                    <div key={level} className="flex justify-between py-1 border-b border-slate-700/50">
                      <span className="text-xs text-slate-400">{level}</span>
                      <span className="text-sm font-mono text-white">₹{price?.toLocaleString('en-IN')}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="bg-slate-800 rounded-lg p-4">
                <h3 className="text-sm font-medium text-slate-400 mb-3">Support & Resistance</h3>
                {ta.resistance_levels?.map((r, i) => (
                  <div key={`r-${i}`} className="flex justify-between py-1 border-b border-slate-700/50">
                    <span className="text-xs text-red-400">Resistance {i + 1}</span>
                    <span className="text-sm font-mono text-white">₹{r?.toLocaleString('en-IN')}</span>
                  </div>
                ))}
                <div className="flex justify-between py-1 border-b border-slate-700/50 bg-slate-700/30">
                  <span className="text-xs text-emerald-400 font-bold">CMP</span>
                  <span className="text-sm font-mono text-emerald-400 font-bold">₹{ta.cmp?.toLocaleString('en-IN')}</span>
                </div>
                {ta.support_levels?.map((s, i) => (
                  <div key={`s-${i}`} className="flex justify-between py-1 border-b border-slate-700/50">
                    <span className="text-xs text-green-400">Support {i + 1}</span>
                    <span className="text-sm font-mono text-white">₹{s?.toLocaleString('en-IN')}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Price History Tab */}
          {activeTab === 'history' && priceHistory?.history && (
            <div className="bg-slate-800 rounded-lg p-4 overflow-x-auto">
              <h3 className="text-sm font-medium text-slate-400 mb-3">Price History (Last 30 Days)</h3>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-slate-400 border-b border-slate-700">
                    <th className="pb-2 pr-2">Date</th>
                    <th className="pb-2 pr-2 text-right">Open</th>
                    <th className="pb-2 pr-2 text-right">High</th>
                    <th className="pb-2 pr-2 text-right">Low</th>
                    <th className="pb-2 pr-2 text-right">Close</th>
                    <th className="pb-2 pr-2 text-right">Change</th>
                    <th className="pb-2 pr-2 text-right">Change%</th>
                    <th className="pb-2 pr-2 text-right">Volume</th>
                    <th className="pb-2 pr-2 text-right">Value (Cr)</th>
                  </tr>
                </thead>
                <tbody>
                  {priceHistory.history.slice().reverse().map(h => (
                    <tr key={h.date} className="border-b border-slate-700/50">
                      <td className="py-1.5 pr-2 font-mono">{h.date}</td>
                      <td className="py-1.5 pr-2 text-right font-mono">{h.open}</td>
                      <td className="py-1.5 pr-2 text-right font-mono">{h.high}</td>
                      <td className="py-1.5 pr-2 text-right font-mono">{h.low}</td>
                      <td className="py-1.5 pr-2 text-right font-mono font-bold">{h.close}</td>
                      <td className={`py-1.5 pr-2 text-right font-mono ${h.change >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {h.change >= 0 ? '+' : ''}{h.change}
                      </td>
                      <td className={`py-1.5 pr-2 text-right font-mono ${h.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {h.change_pct >= 0 ? '+' : ''}{h.change_pct}%
                      </td>
                      <td className="py-1.5 pr-2 text-right font-mono">{h.volume?.toLocaleString('en-IN')}</td>
                      <td className="py-1.5 pr-2 text-right font-mono">{h.value_cr}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

const ENGINE_ORDER = [
  'titan',
  'titan_v19',
  'swing_ai_v12_2',
  'swing_ai_v12_1',
  'king',
];

function orderedModelEntries(models = {}) {
  const ordered = ENGINE_ORDER
    .filter(key => models[key])
    .map(key => [key, models[key]]);
  const remaining = Object.entries(models).filter(([key]) => !ENGINE_ORDER.includes(key));
  return [...ordered, ...remaining];
}

function buildEngineChartData(model) {
  return Object.entries(model.components || {}).map(([key, comp]) => {
    const max = Number(comp.max) || 0;
    const score = Number(comp.score) || 0;
    const pct = max > 0 ? Math.round((score / max) * 100) : 0;
    return {
      name: labelize(key),
      pct: Math.max(0, Math.min(100, pct)),
      scoreLabel: `${formatNumber(comp.score)}/${formatNumber(comp.max)}`,
    };
  });
}

function FullReport({ symbol, matePro, ta }) {
  const stockReport = buildStockReport(symbol, matePro, ta);
  const models = orderedModelEntries(matePro.models);

  return (
    <div className="space-y-5">
      <div className="bg-slate-800 rounded-lg p-5">
        <div className="flex items-center gap-2 mb-4">
          <FileText className="w-5 h-5 text-emerald-300" />
          <h3 className="text-lg font-semibold text-white">Full Report</h3>
        </div>
        <pre className="max-h-[640px] overflow-auto whitespace-pre-wrap rounded-lg border border-slate-700 bg-slate-950/60 p-4 font-mono text-sm leading-7 text-slate-200">
          {stockReport}
        </pre>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {models.map(([key, model]) => (
          <EngineReportCard key={key} model={model} matePro={matePro} />
        ))}
      </div>
    </div>
  );
}

function EngineReportCard({ model, matePro }) {
  const score = getModelScore(model);
  const report = buildEngineReport(model, matePro);

  return (
    <div className="bg-slate-800 rounded-lg p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="text-base font-semibold text-white">{model.model}</h3>
          <p className="text-xs text-slate-400">Reason for engine score</p>
        </div>
        <div className="text-right">
          <div className="text-xl font-bold text-white">{score}/100</div>
          <div className={`text-xs font-bold ${verdictTextClass(model.verdict)}`}>{model.verdict || '-'}</div>
        </div>
      </div>
      <pre className="max-h-[460px] overflow-auto whitespace-pre-wrap rounded-lg border border-slate-700 bg-slate-950/50 p-3 font-mono text-xs leading-6 text-slate-200">
        {report}
      </pre>
    </div>
  );
}

function EngineFullReportPanel({ model, matePro, onClose }) {
  const score = getModelScore(model);
  const probability = model.probability_pct ?? model.final_probability ?? model.base_probability;
  const chartData = buildEngineChartData(model);
  const report = buildEngineReport(model, matePro);
  const chartHeight = Math.max(260, chartData.length * 42);

  return (
    <div className="rounded-lg border border-emerald-400/30 bg-slate-800 p-5 shadow-lg shadow-emerald-950/20">
      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="text-xs uppercase tracking-wide text-emerald-300">Engine Full Report Chart</div>
          <h3 className="mt-1 text-xl font-semibold text-white">{model.model}</h3>
          <p className="mt-1 text-sm text-slate-400">Component score chart and reason for this model verdict.</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="self-start rounded border border-slate-600 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:border-slate-400 hover:text-white"
        >
          Close
        </button>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        <EngineStat label="Score" value={`${score}/100`} />
        <EngineStat label="Verdict" value={model.verdict || '-'} tone={verdictTextClass(model.verdict)} />
        <EngineStat label="Probability" value={probability != null ? `${formatNumber(probability)}%` : '-'} />
        <EngineStat label="Gate" value={model.liquidity_gate || '-'} />
        <EngineStat label="Action" value={model.selection_action || matePro.trade_plans?.scanner_plan?.action || '-'} />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
        <div className="rounded-lg border border-slate-700 bg-slate-950/40 p-4">
          <h4 className="mb-3 text-sm font-semibold text-white">Score Components</h4>
          {chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={chartHeight}>
              <BarChart data={chartData} layout="vertical" margin={{ top: 6, right: 24, bottom: 6, left: 18 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" horizontal={false} />
                <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11, fill: '#94a3b8' }} unit="%" />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={142}
                  tick={{ fontSize: 11, fill: '#cbd5e1' }}
                />
                <Tooltip
                  cursor={{ fill: 'rgba(15, 23, 42, 0.7)' }}
                  contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: '8px' }}
                  formatter={(value, _name, props) => [`${value}% (${props.payload.scoreLabel})`, 'Score']}
                />
                <Bar dataKey="pct" fill="#60a5fa" radius={[0, 4, 4, 0]} barSize={18} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="rounded border border-slate-700 bg-slate-900/60 p-4 text-sm text-slate-400">
              Component score chart is not available for this saved model snapshot.
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-700 bg-slate-950/40 p-4">
          <h4 className="mb-3 text-sm font-semibold text-white">Reason Report</h4>
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap font-mono text-xs leading-6 text-slate-200">
            {report}
          </pre>
        </div>
      </div>
    </div>
  );
}

function EngineStat({ label, value, tone = 'text-white' }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
      <div className="text-xs text-slate-400">{label}</div>
      <div className={`mt-1 text-sm font-bold ${tone}`}>{value}</div>
    </div>
  );
}

function buildStockReport(symbol, matePro, ta) {
  const comp = matePro.composite || {};
  const metrics = matePro.metrics || {};
  const context = matePro.context || {};
  const levels = matePro.levels || {};
  const scannerPlan = matePro.trade_plans?.scanner_plan || {};
  const positionalPlan = matePro.trade_plans?.positional_plan || {};
  const supports = formatPriceList(levels.supports);
  const resistances = formatPriceList(levels.resistances);
  const targets = Object.entries(scannerPlan.targets || {})
    .map(([label, target]) => `${label}: ${formatMoney(target?.price)} (+${target?.pct ?? '-'}%)`)
    .join(', ') || 'Not available';

  return [
    `## Chart Analysis Report: ${symbol}`,
    '',
    '### 1. Overview',
    `- Timeframe analyzed: Daily primary with weekly/monthly context`,
    `- Overall trend: ${trendLabel(context, metrics)}`,
    `- Current price snapshot: ${formatMoney(matePro.cmp)}${matePro.timestamp ? ` as of ${formatTimestamp(matePro.timestamp)}` : ''}`,
    `- Final model: 5-engine MATE-PRO consensus`,
    `- Final score: ${comp.composite_score ?? '-'} / 100`,
    `- Final verdict: ${comp.consensus_verdict || '-'} (${comp.agreement || 'agreement not available'})`,
    `- Trend summary: ${trendSummary(context, metrics, ta)}`,
    '',
    '### 2. Chart Patterns',
    `- ${patternSummary(context, levels)}`,
    '',
    '### 3. Candlestick / Price Quality',
    `- ${candleSummary(context, metrics, matePro)}`,
    '',
    '### 4. Technical Indicators',
    `- RSI: ${rsiSummary(metrics.rsi)}`,
    `- MACD: ${metrics.macd_crossover ? 'MACD is bullish, supporting upside momentum.' : 'MACD is not showing a confirmed bullish crossover.'}`,
    `- Bollinger Bands: ${bollingerSummary(ta)}`,
    `- Moving Averages: ${movingAverageSummary(metrics, ta)}`,
    `- ATR / Volatility: ATR is ${formatNumber(metrics.atr_pct)}%, with ${context.volatility_state || 'unknown'} volatility.`,
    `- Volume: Volume ratio is ${formatNumber(metrics.vol_ratio)}x and 10D traded value is ${formatMoney(metrics.value_10d_cr)} Cr.`,
    '',
    '### 5. Price Action',
    `- Key support levels: ${supports}`,
    `- Key resistance levels: ${resistances}`,
    `- Breakout trigger: ${formatMoney(levels.trigger)}`,
    `- Invalidation / stop level: ${formatMoney(levels.invalidation)}`,
    `- Overhead supply: ${labelize(context.overhead_supply || 'not_available')}`,
    '',
    '### 6. Sector And Market Context',
    `- Sector: ${context.sector || 'Not available'}${context.sector_index ? ` (${context.sector_index})` : ''}`,
    `- Sector momentum: ${context.sector_momentum_score ?? 0}/10, structure ${context.sector_structure || '-'}`,
    `- Sector performance: 1M ${formatPct(context.sector_perf_1m)}, 3M ${formatPct(context.sector_perf_3m)}, 6M ${formatPct(context.sector_perf_6m)}`,
    `- Nifty trend: ${context.nifty_trend_state || '-'} with weekly RSI ${formatNumber(context.nifty_weekly_rsi)}`,
    `- News / sentiment: ${context.news_tone || '-'} news tone, retail psychology ${context.retail_psych || '-'}`,
    '',
    '### 7. Trade Plan',
    `- Action: ${scannerPlan.action || '-'}`,
    `- Entry: breakout above ${formatMoney(scannerPlan.entry_breakout)}`,
    `- Retest zone: ${formatRange(scannerPlan.entry_retest_zone)}`,
    `- Stop loss: ${formatMoney(scannerPlan.stop_loss)} (${scannerPlan.sl_pct ?? metrics.sl_pct ?? '-'}%)`,
    `- Targets: ${targets}`,
    `- Risk/reward to T2: ${scannerPlan.rr_t2 ? `1:${scannerPlan.rr_t2}` : '-'}`,
    `- Positional entry zone: ${formatRange(positionalPlan.entry_zone)}`,
    `- Positional hold rule: ${positionalPlan.hold_rule || '-'}`,
    '',
    '### 8. Engine Consensus',
    ...orderedModelEntries(matePro.models).map(([, model]) => (
      `- ${model.model}: ${getModelScore(model)}/100, ${model.verdict || '-'} - ${engineOneLine(model, matePro)}`
    )),
    '',
    '### 9. Final View',
    `- ${matePro.one_line_verdict || finalView(matePro)}`,
  ].join('\n');
}

function buildEngineReport(model, matePro) {
  const score = getModelScore(model);
  const components = Object.entries(model.components || {});
  const lines = [
    `## Engine Report: ${model.model}`,
    '',
    '### Score Snapshot',
    `- Score: ${score}/100`,
    `- Verdict: ${model.verdict || '-'}`,
    `- Probability: ${model.probability_pct ?? model.final_probability ?? model.base_probability ?? '-'}%`,
  ];

  if (model.selection_grade || model.selection_action || model.liquidity_gate) {
    lines.push(`- Gate / action: ${model.liquidity_gate || '-'} gate, ${model.selection_grade || '-'} grade, ${model.selection_action || '-'} action`);
  }

  lines.push('', '### Why The Score Came Out This Way');
  if (components.length) {
    components.forEach(([key, comp]) => {
      lines.push(`- ${labelize(key)}: ${comp.score}/${comp.max}. ${componentReason(key, comp, model, matePro)}`);
    });
  } else {
    lines.push('- Component-level score data was not saved in this screener snapshot.');
  }

  lines.push('', '### Adjustments And Filters');
  if (model.penalties > 0) {
    lines.push(`- Penalties: -${model.penalties}. ${(model.penalty_reasons || []).join('; ') || 'Penalty reason not specified.'}`);
  } else {
    lines.push('- Penalties: none applied.');
  }

  if (model.setup_family) lines.push(`- Setup family: ${labelize(model.setup_family)}.`);
  if (model.pattern_engine != null) lines.push(`- Pattern engine: ${model.pattern_engine}/100 from the detected chart pattern.`);
  if (model.liquidity_score != null) lines.push(`- Liquidity: ${model.liquidity_score}/10 from phase, delivery, VWAP, and supply checks.`);
  if (model.indicator_score != null) lines.push(`- Indicator stack: ${model.indicator_score}/20 from RSI, MACD, EMA stack, and volatility.`);
  if (model.fib_avwap_score != null) lines.push(`- Fib / AVWAP: ${model.fib_avwap_score}/10 from levels, VWAP, and nearby supports.`);
  if (model.base_weekly_score != null) lines.push(`- Base weekly score: ${model.base_weekly_score}/${model.base_weekly_max || 40}.`);
  if (model.velocity_points != null) lines.push(`- Velocity: ${model.velocity_points}/${model.velocity_max || 5}.`);
  if (model.sector_boost != null) lines.push(`- Sector boost: ${model.sector_boost} points.`);
  if (model.sector_boost_impact != null) lines.push(`- Sector boost impact: +${model.sector_boost_impact} probability points.`);
  if (model.backtest_score != null) lines.push(`- Backtest score: ${model.backtest_score}/20.`);
  if (model.positional_score != null) lines.push(`- Positional read: ${model.positional_score}/${model.positional_max || 30}${model.positional_class ? ` (${model.positional_class})` : ''}.`);

  lines.push('', '### Verdict Reason');
  lines.push(`- ${engineOneLine(model, matePro)}`);

  return lines.join('\n');
}

function componentReason(key, comp, model, matePro) {
  const context = matePro.context || {};
  const metrics = matePro.metrics || {};
  const levels = matePro.levels || {};
  const ratio = componentRatio(comp);
  const strength = ratio >= 0.75 ? 'strong' : ratio >= 0.5 ? 'moderate' : 'weak';
  const readable = labelize(key);

  if (key.includes('weekly_tailwind') || key.includes('trend_power')) {
    return `${strengthText(strength)} because weekly structure is ${context.weekly_structure || '-'}, daily bias is ${context.daily_bias || '-'}, and price/RSI alignment is ${formatNumber(metrics.rsi)} RSI.`;
  }
  if (key.includes('daily_setup') || key.includes('setup_quality')) {
    return `${strengthText(strength)} because the detected setup is ${labelize(context.pattern || 'unclear')} with trigger ${formatMoney(levels.trigger)}.`;
  }
  if (key.includes('trigger_clarity')) {
    return `${strengthText(strength)} because trigger ${formatMoney(levels.trigger)} and invalidation ${formatMoney(levels.invalidation)} define the trade clearly.`;
  }
  if (key.includes('volume') || key.includes('delivery')) {
    return `${strengthText(strength)} with volume ratio ${formatNumber(metrics.vol_ratio)}x and delivery trend ${metrics.delivery_trend || context.delivery_trend || '-'}.`;
  }
  if (key.includes('move_capacity') || key.includes('velocity')) {
    return `${strengthText(strength)} because ATR is ${formatNumber(metrics.atr_pct)}% and volatility is ${context.volatility_state || '-'}.`;
  }
  if (key.includes('overhead') || key.includes('vrvp')) {
    return `${strengthText(strength)} because overhead supply is ${labelize(context.overhead_supply || 'not_available')}.`;
  }
  if (key.includes('sector')) {
    return `${strengthText(strength)} because sector momentum is ${context.sector_momentum_score ?? 0}/10 with ${context.sector_positive_peers ?? 0} positive peers.`;
  }
  if (key.includes('risk') || key.includes('risk_reward')) {
    return `${strengthText(strength)} because stop-loss risk is ${metrics.sl_pct ?? '-'}% from current levels.`;
  }
  if (key.includes('pattern')) {
    return `${strengthText(strength)} from ${labelize(context.pattern || 'unclear')} pattern quality.`;
  }
  if (key.includes('liquidity')) {
    return `${strengthText(strength)} from phase ${context.phase || '-'}, traded value ${formatMoney(metrics.value_10d_cr)} Cr, and supply checks.`;
  }
  if (key.includes('indicator')) {
    return `${strengthText(strength)} from RSI ${formatNumber(metrics.rsi)}, MACD ${metrics.macd_crossover ? 'bullish' : 'not bullish'}, and EMA stack ${metrics.ema_stack || '-'}.`;
  }
  if (key.includes('fib') || key.includes('avwap')) {
    return `${strengthText(strength)} because support/resistance levels are available around ${formatPriceList([...(levels.supports || []), ...(levels.resistances || [])].slice(0, 3))}.`;
  }
  if (key.includes('sweep')) {
    return `${strengthText(strength)} after liquidity sweep risk and delivery quality checks.`;
  }
  if (key.includes('core_swing')) {
    return `${strengthText(strength)} from combined weekly trend, breakout validity, indicators, and risk fit.`;
  }

  return `${strengthText(strength)} contribution from ${readable}.`;
}

function engineOneLine(model, matePro) {
  const score = getModelScore(model);
  const context = matePro.context || {};
  const metrics = matePro.metrics || {};
  const action = model.selection_action || matePro.trade_plans?.scanner_plan?.action || 'watch';
  const base = score >= 80
    ? 'score is high because trend, setup quality, and risk filters are aligned'
    : score >= 65
      ? 'score is constructive, but at least one filter still needs confirmation'
      : score >= 50
        ? 'score is mixed because the setup has promise but confirmation is incomplete'
        : 'score is weak because multiple filters are not aligned';
  const caution = [];

  if (model.liquidity_gate === 'FAIL') caution.push('liquidity gate failed');
  if ((model.penalty_reasons || []).length) caution.push(model.penalty_reasons[0]);
  if ((metrics.sl_pct ?? 0) > 5) caution.push('risk is wide');
  if (context.overhead_supply === 'heavy') caution.push('overhead supply is heavy');

  return `${base}; action is ${action}${caution.length ? `, with caution: ${caution.join(', ')}` : ''}.`;
}

function finalView(matePro) {
  const comp = matePro.composite || {};
  const action = matePro.trade_plans?.scanner_plan?.action || '-';
  return `${matePro.symbol || 'Stock'} has a ${comp.consensus_verdict || '-'} consensus with ${comp.composite_score ?? '-'} score; preferred action is ${action}.`;
}

function trendLabel(context, metrics) {
  if (context.daily_bias === 'bullish' || metrics.ema_stack === 'bullish') return 'Bullish';
  if (context.daily_bias === 'bearish') return 'Bearish';
  return 'Neutral / Mixed';
}

function trendSummary(context, metrics, ta) {
  if (metrics.ema_stack === 'bullish') {
    return 'Price is trading with a bullish EMA stack, which supports an uptrend.';
  }
  if (ta?.ema_20 && ta?.ema_50 && ta?.cmp) {
    const relation = ta.cmp > ta.ema_20 && ta.cmp > ta.ema_50 ? 'above' : 'below or mixed against';
    return `Price is ${relation} the 20 and 50 EMAs, while daily structure is ${context.daily_structure || '-'}.`;
  }
  return `Daily structure is ${context.daily_structure || '-'} and weekly structure is ${context.weekly_structure || '-'}.`;
}

function patternSummary(context, levels) {
  const pattern = labelize(context.pattern || 'unclear');
  const trigger = formatMoney(levels.trigger);
  if (context.pattern && context.pattern !== 'unclear') {
    return `${pattern}: The setup is active. Confirmation improves on a sustained move above ${trigger} with stronger volume.`;
  }
  return `No dominant chart pattern is confirmed. A close above ${trigger} would improve breakout clarity.`;
}

function candleSummary(context, metrics, matePro) {
  const action = matePro.trade_plans?.scanner_plan?.action;
  if (action === 'TRADE') {
    return 'Latest price action is acceptable for the scanner plan, provided the stop-loss remains respected.';
  }
  if (action === 'WAIT RETEST' || action === 'NO CHASE') {
    return 'Latest price action is extended enough that the plan prefers a retest instead of chasing.';
  }
  return `Price quality is mixed; volatility is ${context.volatility_state || '-'} and risk is ${metrics.sl_pct ?? '-'}%.`;
}

function rsiSummary(rsi) {
  if (rsi == null) return 'RSI data is not available.';
  if (rsi >= 70) return `RSI is at ${formatNumber(rsi)}, which is strong but entering overbought territory.`;
  if (rsi >= 60) return `RSI is at ${formatNumber(rsi)}, showing healthy bullish momentum.`;
  if (rsi >= 45) return `RSI is at ${formatNumber(rsi)}, broadly neutral but useful for confirmation.`;
  return `RSI is at ${formatNumber(rsi)}, showing weak momentum.`;
}

function bollingerSummary(ta) {
  if (!ta?.bb_width) return 'Bollinger Band width is not available.';
  if (ta.bb_width < 8) return `Band width is ${formatNumber(ta.bb_width)}%, suggesting volatility contraction.`;
  if (ta.bb_width > 18) return `Band width is ${formatNumber(ta.bb_width)}%, suggesting elevated volatility.`;
  return `Band width is ${formatNumber(ta.bb_width)}%, pointing to balanced volatility.`;
}

function movingAverageSummary(metrics, ta) {
  if (metrics.ema_stack === 'bullish') return 'Price is aligned with a bullish EMA stack.';
  const parts = [
    ta?.ema_20 ? `20 EMA ${formatMoney(ta.ema_20)}` : null,
    ta?.ema_50 ? `50 EMA ${formatMoney(ta.ema_50)}` : null,
    ta?.ema_200 ? `200 EMA ${formatMoney(ta.ema_200)}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join(', ') : 'Moving-average data is not available.';
}

function getModelScore(model) {
  const score = model.scanner_score ?? model.selection_total ?? model.final_probability ?? model.probability_pct ?? 0;
  return formatNumber(score);
}

function componentRatio(comp) {
  const max = Number(comp.max) || 0;
  if (max <= 0) return 0;
  return Math.max(0, Math.min(1, (Number(comp.score) || 0) / max));
}

function strengthText(strength) {
  if (strength === 'strong') return 'Strong contribution';
  if (strength === 'moderate') return 'Moderate contribution';
  return 'Weak contribution';
}

function verdictTextClass(verdict) {
  if (verdict === 'STRONG BUY') return 'text-emerald-300';
  if (verdict === 'BUY') return 'text-green-300';
  if (verdict === 'HOLD') return 'text-amber-300';
  if (verdict === 'WAIT') return 'text-orange-300';
  return 'text-red-300';
}

function labelize(value) {
  return String(value || '-')
    .replace(/^[A-Z]\d_/, '')
    .replace(/^P\d_/, '')
    .replace(/^S\d_/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, char => char.toUpperCase());
}

function formatNumber(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  return Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

function formatMoney(value) {
  if (value == null || Number.isNaN(Number(value))) return 'Rs -';
  return `Rs ${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`;
}

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return '-';
  return `${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}%`;
}

function formatRange(values) {
  if (!Array.isArray(values) || values.length < 2) return 'Not available';
  return `${formatMoney(values[0])} to ${formatMoney(values[1])}`;
}

function formatPriceList(values) {
  if (!Array.isArray(values) || values.length === 0) return 'Not available';
  return values.map(formatMoney).join(', ');
}

function formatTimestamp(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en-IN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function IndicatorCard({ title, items }) {
  return (
    <div className="bg-slate-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-slate-400 mb-3">{title}</h3>
      <div className="space-y-2">
        {items.map(({ label, value, suffix, isText }) => (
          <div key={label} className="flex justify-between">
            <span className="text-xs text-slate-400">{label}</span>
            <span className={`text-sm font-mono ${isText ? 'text-amber-300' : 'text-white'}`}>
              {value != null ? (isText ? value : `${Number(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}${suffix || ''}`) : '-'}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
