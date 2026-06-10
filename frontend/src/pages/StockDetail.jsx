import { useState, useEffect } from 'react';
import { useParams, useSearchParams, Link } from 'react-router-dom';
import { ArrowLeft, Loader2 } from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, ComposedChart, Area, ReferenceLine
} from 'recharts';
import SignalBadge from '../components/SignalBadge';
import { getAnalysis, getChartData, getPriceHistory, getMatePro } from '../lib/api';

export default function StockDetail() {
  const { symbol } = useParams();
  const [searchParams] = useSearchParams();
  const runId = searchParams.get('run_id');

  const [timeframe, setTimeframe] = useState('daily');
  const [analysis, setAnalysis] = useState(null);
  const [chartData, setChartData] = useState(null);
  const [priceHistory, setPriceHistory] = useState(null);
  const [matePro, setMatePro] = useState(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('mate-pro');

  useEffect(() => {
    loadData();
  }, [symbol, timeframe]);

  async function loadData() {
    setLoading(true);
    try {
      const [analysisRes, chartRes, historyRes] = await Promise.all([
        getAnalysis(symbol, runId).catch(() => null),
        getChartData(symbol, timeframe, 180).catch(() => null),
        getPriceHistory(symbol, 30).catch(() => null),
      ]);
      if (analysisRes) setAnalysis(analysisRes.data);
      if (chartRes) setChartData(chartRes.data);
      if (historyRes) setPriceHistory(historyRes.data);

      // Fetch MATE-PRO separately so we can see errors
      try {
        const mateProRes = await getMatePro(symbol);
        console.log('MATE-PRO response:', mateProRes.data);
        if (mateProRes?.data) setMatePro(mateProRes.data);
      } catch (mpErr) {
        console.error('MATE-PRO error:', mpErr.response?.status, mpErr.response?.data, mpErr.message);
      }
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }

  const ta = analysis?.analysis?.[timeframe] || analysis?.analysis?.daily || {};
  const chart = chartData?.chart_data || [];
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
          <div className="flex gap-1 border-b border-slate-700">
            {['mate-pro', 'chart', 'indicators', 'levels', 'history'].map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  activeTab === tab
                    ? 'border-emerald-400 text-white'
                    : 'border-transparent text-slate-400 hover:text-slate-200'
                }`}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
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
                {Object.entries(matePro.models).map(([key, model]) => (
                  <div key={key} className="bg-slate-800 rounded-lg p-4">
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
                  </div>
                ))}
              </div>

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
