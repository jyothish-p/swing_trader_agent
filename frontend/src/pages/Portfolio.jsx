import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, RefreshCw, Loader2, Trash2, DollarSign, TrendingUp, TrendingDown } from 'lucide-react';
import { getPortfolio, addToPortfolio, refreshPortfolio, sellStock, deletePortfolioEntry } from '../lib/api';

export default function Portfolio() {
  const [entries, setEntries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [showSell, setShowSell] = useState(null);
  const [filter, setFilter] = useState('open');

  // Add form state
  const [addForm, setAddForm] = useState({
    symbol: '', buy_date: new Date().toISOString().split('T')[0],
    buy_price: '', quantity: '', buy_reason: '', notes: '',
  });
  const [sellForm, setSellForm] = useState({
    sell_date: new Date().toISOString().split('T')[0], sell_price: '', notes: '',
  });

  useEffect(() => { loadPortfolio(); }, [filter]);

  async function loadPortfolio() {
    setLoading(true);
    try {
      const res = await getPortfolio(filter);
      setEntries(res.data.entries || []);
      setSummary(res.data.summary || null);
    } catch (e) { console.error(e); }
    setLoading(false);
  }

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await refreshPortfolio();
      await loadPortfolio();
    } catch (e) { console.error(e); }
    setRefreshing(false);
  }

  async function handleAdd(e) {
    e.preventDefault();
    try {
      await addToPortfolio({
        ...addForm,
        buy_price: parseFloat(addForm.buy_price),
        quantity: parseInt(addForm.quantity),
      });
      setShowAdd(false);
      setAddForm({ symbol: '', buy_date: new Date().toISOString().split('T')[0], buy_price: '', quantity: '', buy_reason: '', notes: '' });
      await loadPortfolio();
    } catch (err) { alert(err.response?.data?.detail || 'Failed to add'); }
  }

  async function handleSell(entryId) {
    try {
      await sellStock(entryId, {
        ...sellForm,
        sell_price: parseFloat(sellForm.sell_price),
      });
      setShowSell(null);
      await loadPortfolio();
    } catch (err) { alert(err.response?.data?.detail || 'Failed'); }
  }

  async function handleDelete(entryId) {
    if (!confirm('Delete this entry?')) return;
    try {
      await deletePortfolioEntry(entryId);
      await loadPortfolio();
    } catch (err) { alert('Failed'); }
  }

  const actionColor = (action) => {
    if (!action) return 'text-slate-400';
    if (action.includes('BUY')) return 'text-emerald-400';
    if (action === 'HOLD') return 'text-amber-400';
    if (action.includes('SELL') || action.includes('EXIT') || action.includes('PROFIT')) return 'text-red-400';
    return 'text-slate-400';
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">Portfolio Tracker</h1>
          <p className="text-sm text-slate-400 mt-1">Track purchases with live MATE-PRO recommendations</p>
        </div>
        <div className="flex gap-2">
          <button onClick={() => setShowAdd(true)}
            className="flex items-center gap-2 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg text-sm font-medium">
            <Plus className="w-4 h-4" /> Add Stock
          </button>
          <button onClick={handleRefresh} disabled={refreshing}
            className="flex items-center gap-2 px-3 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded-lg text-sm">
            {refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh Scores
          </button>
        </div>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-slate-800 rounded-lg p-4">
            <div className="text-xs text-slate-400">Invested</div>
            <div className="text-lg font-bold text-white mt-1">₹{summary.total_invested?.toLocaleString('en-IN')}</div>
          </div>
          <div className="bg-slate-800 rounded-lg p-4">
            <div className="text-xs text-slate-400">Current Value</div>
            <div className="text-lg font-bold text-white mt-1">₹{summary.total_current_value?.toLocaleString('en-IN')}</div>
          </div>
          <div className="bg-slate-800 rounded-lg p-4">
            <div className="text-xs text-slate-400">P&L</div>
            <div className={`text-lg font-bold mt-1 ${summary.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {summary.total_pnl >= 0 ? '+' : ''}₹{summary.total_pnl?.toLocaleString('en-IN')}
            </div>
          </div>
          <div className="bg-slate-800 rounded-lg p-4">
            <div className="text-xs text-slate-400">P&L %</div>
            <div className={`text-lg font-bold mt-1 flex items-center gap-1 ${summary.total_pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
              {summary.total_pnl_pct >= 0 ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
              {summary.total_pnl_pct >= 0 ? '+' : ''}{summary.total_pnl_pct}%
            </div>
          </div>
        </div>
      )}

      {/* Action Summary */}
      {summary?.action_counts && (
        <div className="flex gap-3">
          <span className="px-3 py-1 rounded-full text-xs font-medium bg-emerald-500/20 text-emerald-300">
            BUY MORE: {summary.action_counts.buy_more}
          </span>
          <span className="px-3 py-1 rounded-full text-xs font-medium bg-amber-500/20 text-amber-300">
            HOLD: {summary.action_counts.hold}
          </span>
          <span className="px-3 py-1 rounded-full text-xs font-medium bg-red-500/20 text-red-300">
            SELL: {summary.action_counts.sell}
          </span>
        </div>
      )}

      {/* Filter */}
      <div className="flex gap-2">
        {['open', 'closed', 'all'].map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded text-sm font-medium ${
              filter === f ? 'bg-emerald-600 text-white' : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
            }`}>{f.charAt(0).toUpperCase() + f.slice(1)}</button>
        ))}
      </div>

      {/* Portfolio Table */}
      {loading ? (
        <div className="flex justify-center py-12"><Loader2 className="w-8 h-8 text-slate-400 animate-spin" /></div>
      ) : entries.length === 0 ? (
        <div className="bg-slate-800 rounded-lg p-12 text-center">
          <DollarSign className="w-12 h-12 text-slate-600 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-white mb-2">No Stocks in Portfolio</h2>
          <p className="text-slate-400">Click "Add Stock" to track your purchases with MATE-PRO recommendations</p>
        </div>
      ) : (
        <div className="bg-slate-800 rounded-lg p-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-slate-400 border-b border-slate-700">
                <th className="pb-2 pr-3">Symbol</th>
                <th className="pb-2 pr-3 text-right">Buy ₹</th>
                <th className="pb-2 pr-3 text-right">Qty</th>
                <th className="pb-2 pr-3 text-right">CMP</th>
                <th className="pb-2 pr-3 text-right">P&L %</th>
                <th className="pb-2 pr-3 text-right">P&L ₹</th>
                <th className="pb-2 pr-3 text-center">Verdict</th>
                <th className="pb-2 pr-3 text-right">Score</th>
                <th className="pb-2 pr-3 text-center">Action</th>
                <th className="pb-2 pr-3 text-right">SL</th>
                <th className="pb-2 pr-3 text-right">T1/T2</th>
                <th className="pb-2 pr-3"></th>
              </tr>
            </thead>
            <tbody>
              {entries.map(e => (
                <tr key={e.id} className="border-b border-slate-800 hover:bg-slate-800/50">
                  <td className="py-2 pr-3 font-medium">
                    <Link to={`/stock/${e.symbol}`} className="text-blue-400 hover:text-blue-300 no-underline">
                      {e.symbol}
                    </Link>
                    <div className="text-[10px] text-slate-500">{e.buy_date}</div>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">₹{e.buy_price}</td>
                  <td className="py-2 pr-3 text-right font-mono">{e.quantity}</td>
                  <td className="py-2 pr-3 text-right font-mono">₹{e.current_price?.toFixed(2)}</td>
                  <td className={`py-2 pr-3 text-right font-mono font-bold ${e.pnl_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {e.pnl_pct >= 0 ? '+' : ''}{e.pnl_pct}%
                  </td>
                  <td className={`py-2 pr-3 text-right font-mono ${e.pnl_amount >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {e.pnl_amount >= 0 ? '+' : ''}₹{e.pnl_amount?.toLocaleString('en-IN')}
                  </td>
                  <td className="py-2 pr-3 text-center">
                    {e.mate_pro?.verdict && (
                      <span className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                        e.mate_pro.verdict === 'STRONG BUY' ? 'bg-emerald-500/20 text-emerald-300' :
                        e.mate_pro.verdict === 'BUY' ? 'bg-green-500/20 text-green-300' :
                        e.mate_pro.verdict === 'HOLD' ? 'bg-amber-500/20 text-amber-300' :
                        'bg-red-500/20 text-red-300'
                      }`}>{e.mate_pro.verdict}</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-amber-300">
                    {e.mate_pro?.score?.toFixed(0)}
                  </td>
                  <td className="py-2 pr-3 text-center">
                    <span className={`text-xs font-bold px-2 py-1 rounded ${actionColor(e.mate_pro?.action)} bg-slate-700`}>
                      {e.mate_pro?.action || '—'}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-red-400 text-xs">
                    {e.stop_loss ? `₹${e.stop_loss?.toFixed(0)}` : '—'}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono text-emerald-400 text-xs">
                    {e.target_1 ? `₹${e.target_1?.toFixed(0)} / ₹${e.target_2?.toFixed(0)}` : '—'}
                  </td>
                  <td className="py-2 pr-3">
                    <div className="flex gap-1">
                      {e.status === 'open' && (
                        <button onClick={() => { setShowSell(e.id); setSellForm({
                          sell_date: new Date().toISOString().split('T')[0],
                          sell_price: e.current_price?.toString() || '', notes: ''
                        }); }}
                          className="text-xs px-2 py-1 bg-amber-600/20 text-amber-300 rounded hover:bg-amber-600/40">
                          Sell
                        </button>
                      )}
                      <button onClick={() => handleDelete(e.id)}
                        className="text-slate-500 hover:text-red-400 p-1">
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Stock Modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-800 rounded-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-semibold text-white mb-4">Add Stock to Portfolio</h2>
            <form onSubmit={handleAdd} className="space-y-3">
              <div>
                <label className="text-xs text-slate-400">Symbol (NSE)</label>
                <input type="text" value={addForm.symbol}
                  onChange={e => setAddForm({...addForm, symbol: e.target.value.toUpperCase()})}
                  className="w-full mt-1 px-3 py-2 bg-slate-700 text-white rounded border border-slate-600 text-sm"
                  placeholder="e.g. RELIANCE" required />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-slate-400">Buy Date</label>
                  <input type="date" value={addForm.buy_date}
                    onChange={e => setAddForm({...addForm, buy_date: e.target.value})}
                    className="w-full mt-1 px-3 py-2 bg-slate-700 text-white rounded border border-slate-600 text-sm" required />
                </div>
                <div>
                  <label className="text-xs text-slate-400">Buy Price (₹)</label>
                  <input type="number" step="0.01" value={addForm.buy_price}
                    onChange={e => setAddForm({...addForm, buy_price: e.target.value})}
                    className="w-full mt-1 px-3 py-2 bg-slate-700 text-white rounded border border-slate-600 text-sm"
                    placeholder="1500.50" required />
                </div>
              </div>
              <div>
                <label className="text-xs text-slate-400">Quantity</label>
                <input type="number" value={addForm.quantity}
                  onChange={e => setAddForm({...addForm, quantity: e.target.value})}
                  className="w-full mt-1 px-3 py-2 bg-slate-700 text-white rounded border border-slate-600 text-sm"
                  placeholder="10" required />
              </div>
              <div>
                <label className="text-xs text-slate-400">Reason</label>
                <input type="text" value={addForm.buy_reason}
                  onChange={e => setAddForm({...addForm, buy_reason: e.target.value})}
                  className="w-full mt-1 px-3 py-2 bg-slate-700 text-white rounded border border-slate-600 text-sm"
                  placeholder="MATE-PRO STRONG BUY" />
              </div>
              <div className="flex gap-2 pt-2">
                <button type="submit" className="flex-1 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-sm font-medium">
                  Add & Analyze
                </button>
                <button type="button" onClick={() => setShowAdd(false)}
                  className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-sm">
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Sell Modal */}
      {showSell && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-slate-800 rounded-xl p-6 w-full max-w-md">
            <h2 className="text-lg font-semibold text-white mb-4">Record Sale</h2>
            <form onSubmit={(e) => { e.preventDefault(); handleSell(showSell); }} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-slate-400">Sell Date</label>
                  <input type="date" value={sellForm.sell_date}
                    onChange={e => setSellForm({...sellForm, sell_date: e.target.value})}
                    className="w-full mt-1 px-3 py-2 bg-slate-700 text-white rounded border border-slate-600 text-sm" required />
                </div>
                <div>
                  <label className="text-xs text-slate-400">Sell Price (₹)</label>
                  <input type="number" step="0.01" value={sellForm.sell_price}
                    onChange={e => setSellForm({...sellForm, sell_price: e.target.value})}
                    className="w-full mt-1 px-3 py-2 bg-slate-700 text-white rounded border border-slate-600 text-sm" required />
                </div>
              </div>
              <div className="flex gap-2 pt-2">
                <button type="submit" className="flex-1 px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded text-sm font-medium">
                  Record Sale
                </button>
                <button type="button" onClick={() => setShowSell(null)}
                  className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-300 rounded text-sm">
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
