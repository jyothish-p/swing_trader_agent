import { Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import StockDetail from './pages/StockDetail';
import Portfolio from './pages/Portfolio';
import Lookup from './pages/Lookup';

function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="stock/:symbol" element={<StockDetail />} />
        <Route path="portfolio" element={<Portfolio />} />
        <Route path="lookup" element={<Lookup />} />
      </Route>
    </Routes>
  );
}

export default App;
