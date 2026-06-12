import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 300000, // 5 minutes for screener runs
});

export const runScreener = (forceRefresh = false) =>
  api.post(`/screener/run?force_refresh=${forceRefresh}`);

export const runScreenerAsync = (forceRefresh = false) =>
  api.post(`/screener/run/async?force_refresh=${forceRefresh}`);

export const getScreenerStatus = (runId) =>
  api.get(`/screener/status/${runId}`);

export const getScreenerResults = (runId) =>
  api.get(`/screener/results/${runId}`);

export const getScreenerRuns = (limit = 10) =>
  api.get(`/screener/runs?limit=${limit}`);

export const getUniverse = () =>
  api.get('/stocks/universe');

export const getPriceHistory = (symbol, days = 30) =>
  api.get(`/stocks/${symbol}/price-history?days=${days}`);

export const getDeliveryData = (symbol, days = 30) =>
  api.get(`/stocks/${symbol}/delivery?days=${days}`);

export const getAnalysis = (symbol, runId = null) =>
  api.get(`/analysis/${symbol}${runId ? `?run_id=${runId}` : ''}`);

export const getChartData = (symbol, timeframe = 'daily', days = 180) =>
  api.get(`/analysis/${symbol}/chart-data?timeframe=${timeframe}&days=${days}`);

export const getMatePro = (symbol) =>
  api.get(`/analysis/${symbol}/mate-pro`);

export const getMateProBatch = (symbols = [], runId = null) =>
  api.post(`/analysis/mate-pro/batch${runId ? `?run_id=${runId}` : ''}`, symbols);

export const getQuotes = (symbols = []) => {
  const qs = Array.isArray(symbols) ? symbols.join(',') : symbols;
  return api.get(`/stocks/quotes?symbols=${encodeURIComponent(qs)}`);
};

export const lookupStock = (symbol) =>
  api.post('/analysis/lookup', { symbol });

// Portfolio
export const getPortfolio = (status = 'open') =>
  api.get(`/portfolio/?status=${status}`);

export const addToPortfolio = (data) =>
  api.post('/portfolio/add', data);

export const refreshPortfolio = () =>
  api.post('/portfolio/refresh');

export const sellStock = (entryId, data) =>
  api.post(`/portfolio/${entryId}/sell`, data);

export const deletePortfolioEntry = (entryId) =>
  api.delete(`/portfolio/${entryId}`);

export default api;
