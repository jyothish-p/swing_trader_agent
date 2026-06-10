import { Outlet, Link, useLocation } from 'react-router-dom';
import { TrendingUp, BarChart3, Search, Briefcase } from 'lucide-react';

export default function Layout() {
  const location = useLocation();
  const isActive = (path) => location.pathname === path;

  const navLink = (to, label, Icon) => (
    <Link to={to} className={`text-sm no-underline flex items-center gap-1.5 px-3 py-1.5 rounded transition-colors ${
      isActive(to) ? 'bg-emerald-600/20 text-emerald-300' : 'text-slate-300 hover:text-white hover:bg-slate-700'
    }`}>
      <Icon className="w-4 h-4" /> {label}
    </Link>
  );

  return (
    <div className="min-h-screen bg-slate-900 text-slate-200">
      <header className="bg-slate-800 border-b border-slate-700 px-6 py-3 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-2 no-underline text-slate-200">
          <TrendingUp className="w-6 h-6 text-emerald-400" />
          <span className="text-xl font-bold">Swing Trader</span>
          <span className="text-xs text-slate-400 ml-1">NSE F&O</span>
        </Link>
        <nav className="flex items-center gap-1">
          {navLink('/', 'Dashboard', BarChart3)}
          {navLink('/lookup', 'Lookup', Search)}
          {navLink('/portfolio', 'Portfolio', Briefcase)}
        </nav>
      </header>
      <main className="p-6 max-w-7xl mx-auto">
        <Outlet />
      </main>
    </div>
  );
}
