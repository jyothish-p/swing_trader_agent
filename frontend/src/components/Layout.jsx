import { Outlet, Link, useLocation } from 'react-router-dom';
import {
  Bell,
  BarChart3,
  Briefcase,
  ChevronDown,
  LineChart,
  Moon,
  Search,
  Settings,
  ShieldAlert,
  SlidersHorizontal,
  Star,
  TrendingUp,
} from 'lucide-react';

export default function Layout() {
  const location = useLocation();
  const isActive = (path) => location.pathname === path;

  const navLink = (to, label, Icon, exact = false) => {
    const active = exact ? isActive(to) : location.pathname.startsWith(to);
    return (
      <Link
        to={to}
        className={`group flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium no-underline transition-all ${
          active
            ? 'bg-gradient-to-r from-sky-500 to-indigo-600 text-white shadow-[0_12px_35px_rgba(59,130,246,0.35)]'
            : 'text-slate-300 hover:bg-white/6 hover:text-white'
        }`}
      >
        <Icon className="h-5 w-5" /> {label}
      </Link>
    );
  };

  return (
    <div className="min-h-screen overflow-hidden bg-[#030816] text-slate-100">
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(circle_at_top_left,rgba(34,211,238,0.18),transparent_34%),radial-gradient(circle_at_top_right,rgba(99,102,241,0.22),transparent_30%),linear-gradient(180deg,#030816_0%,#071225_48%,#020617_100%)]" />
      <div className="relative flex min-h-screen">
        <aside className="hidden w-[264px] shrink-0 border-r border-white/8 bg-slate-950/50 p-4 backdrop-blur-2xl lg:block">
          <Link to="/" className="mb-8 flex items-center gap-3 px-1 text-white no-underline">
            <div className="grid h-11 w-11 place-items-center rounded-2xl bg-cyan-400/10 text-cyan-300 shadow-[0_0_30px_rgba(34,211,238,0.25)]">
              <TrendingUp className="h-8 w-8" />
            </div>
            <div>
              <div className="text-xl font-black tracking-tight">Swing Trader</div>
              <div className="text-xs font-medium text-slate-400">NSE F&O</div>
            </div>
          </Link>

          <nav className="space-y-2">
            {navLink('/', 'Dashboard', BarChart3, true)}
            {navLink('/lookup', 'Stock Screener', SlidersHorizontal)}
            {navLink('/lookup', 'Lookup', Search)}
            {navLink('/portfolio', 'Portfolio', Briefcase)}
            <span className="flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium text-slate-400">
              <Star className="h-5 w-5" /> Watchlist
            </span>
            <span className="flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium text-slate-400">
              <Bell className="h-5 w-5" /> Alerts
            </span>
            <span className="flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium text-slate-400">
              <LineChart className="h-5 w-5" /> Reports
            </span>
            <span className="flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-medium text-slate-400">
              <Settings className="h-5 w-5" /> Settings
            </span>
          </nav>

          <div className="mt-12 rounded-3xl border border-white/8 bg-gradient-to-br from-slate-800/80 to-indigo-950/70 p-5 shadow-2xl">
            <div className="mx-auto mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-indigo-500/20 text-cyan-300">
              <ShieldAlert className="h-8 w-8" />
            </div>
            <div className="text-center text-lg font-bold text-white">Upgrade to Pro</div>
            <p className="mt-2 text-center text-sm leading-relaxed text-slate-400">
              Unlock advanced filters, alerts and backtesting.
            </p>
            <button className="mt-5 w-full rounded-xl bg-gradient-to-r from-sky-500 to-indigo-600 px-4 py-3 text-sm font-bold text-white shadow-[0_12px_35px_rgba(79,70,229,0.35)]">
              Upgrade Now
            </button>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-20 border-b border-white/8 bg-slate-950/45 px-4 py-3 backdrop-blur-2xl sm:px-6 lg:px-8">
            <div className="flex items-center justify-between gap-4">
              <Link to="/" className="flex items-center gap-2 text-white no-underline lg:hidden">
                <TrendingUp className="h-7 w-7 text-cyan-300" />
                <span className="text-lg font-black">Swing Trader</span>
              </Link>

              <div className="ml-auto flex items-center gap-3">
                <div className="hidden w-[320px] items-center gap-2 rounded-xl border border-white/10 bg-slate-950/50 px-4 py-2.5 text-slate-400 shadow-inner shadow-black/20 md:flex">
                  <span className="text-sm">Search stocks, sectors...</span>
                  <Search className="ml-auto h-4 w-4" />
                </div>
                <button className="rounded-xl border border-white/10 bg-slate-950/40 p-2.5 text-slate-300 hover:text-white">
                  <Moon className="h-5 w-5" />
                </button>
                <button className="relative rounded-xl border border-white/10 bg-slate-950/40 p-2.5 text-slate-300 hover:text-white">
                  <Bell className="h-5 w-5" />
                  <span className="absolute right-2 top-2 h-2.5 w-2.5 rounded-full bg-sky-400 ring-2 ring-slate-950" />
                </button>
                <button className="flex items-center gap-2 rounded-2xl bg-indigo-600/20 px-3 py-2 text-sm font-bold text-indigo-100">
                  <span className="grid h-8 w-8 place-items-center rounded-full bg-indigo-600">J</span>
                  <ChevronDown className="h-4 w-4 text-slate-400" />
                </button>
              </div>
            </div>
          </header>

          <main className="min-w-0 p-4 sm:p-6 lg:p-8">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
