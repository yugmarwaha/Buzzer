import { BrowserRouter, Navigate, Outlet, Route, Routes, useNavigate, NavLink } from 'react-router-dom';
import { BookOpen, Users, UserX, LogOut, ExternalLink } from 'lucide-react';
import LoginPage from './pages/LoginPage';
import CoursesPage from './pages/CoursesPage';
import UsersPage from './pages/UsersPage';
import UserDetailPage from './pages/UserDetailPage';
import GuestsPage from './pages/GuestsPage';
import { hostAppLink } from './lib/hostApp';
import { hasLiveAdminToken } from './lib/api';
import { ThemeToggle } from './components/ui/ThemeToggle';

function RequireAdmin() {
  // An expired or non-admin token counts as signed out: clear it and return to the login page.
  if (!hasLiveAdminToken()) {
    localStorage.removeItem('token');
    return <Navigate to="/login" replace />;
  }
  return <Outlet />;
}

function AdminLayout() {
  const navigate = useNavigate();

  function logout() {
    localStorage.removeItem('token');
    navigate('/login');
  }

  // Active link: tinted pill plus an accent bar that grows in (scale-y) when the route matches.
  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `relative flex shrink-0 items-center gap-3 rounded-lg px-4 py-2.5 text-body font-semibold transition-colors duration-fast ease-smooth before:absolute before:left-0 before:top-2 before:bottom-2 before:w-1 before:rounded-pill before:bg-accent-text before:transition-transform before:duration-base before:ease-spring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
      isActive
        ? 'bg-accent-hint text-accent-text before:scale-y-100'
        : 'text-secondary before:scale-y-0 hover:bg-raised hover:text-primary'
    }`;

  const footerLinkClass =
    'flex w-full items-center gap-3 rounded-lg px-4 py-2.5 text-body font-medium text-secondary transition-colors duration-fast ease-smooth hover:bg-raised hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

  return (
    <div className="flex min-h-screen flex-col gap-3 p-2 md:flex-row md:p-3">
      {/* Sidebar: floating glass panel on desktop, top bar on small screens */}
      <aside className="glass-strong flex shrink-0 flex-col rounded-2xl animate-fade-in md:sticky md:top-3 md:h-[calc(100vh-1.5rem)] md:w-60">
        <div className="flex items-center gap-3 px-5 py-4 md:py-5">
          <span
            aria-hidden="true"
            className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-subheading font-bold text-accent-fg shadow-button"
          >
            B
          </span>
          <h1 className="text-subheading text-primary">Buzzer Admin</h1>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-1 md:flex-col md:overflow-y-auto md:overflow-x-hidden md:pb-3">
          {/* Admin-first: users, courses, access. Host work lives in the Host app. */}
          <NavLink to="/users" className={linkClass}>
            <Users size={18} aria-hidden="true" /> Users
          </NavLink>
          <NavLink to="/courses" className={linkClass}>
            <BookOpen size={18} aria-hidden="true" /> Courses
          </NavLink>
          <NavLink to="/guests" className={linkClass}>
            <UserX size={18} aria-hidden="true" /> Guests
          </NavLink>
        </nav>
        <div className="flex flex-wrap gap-1 border-t border-line p-3 md:flex-col md:flex-nowrap">
          <a
            href={hostAppLink('/courses')}
            className={`${footerLinkClass} md:w-full`}
            title="Quizzes, rosters, rooms, and session results"
          >
            <ExternalLink size={16} aria-hidden="true" /> Open Host app
          </a>
          {/* ThemeToggle must stay identical in all three apps, so this wrapper lines its row up
              with the footer links (left-aligned, same text size); the child selector wins
              over the toggle's own classes by specificity. */}
          <div className="w-full [&>button]:justify-start [&>button]:text-body">
            <ThemeToggle variant="row" />
          </div>
          <button onClick={logout} className={footerLinkClass}>
            <LogOut size={16} aria-hidden="true" /> Logout
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <Routes>
        <Route path="/login" element={<main><ThemeToggle className="fixed top-3 right-3 z-40" /><LoginPage /></main>} />
        <Route element={<RequireAdmin />}>
          <Route element={<AdminLayout />}>
            <Route path="/users" element={<UsersPage />} />
            <Route path="/users/:userId" element={<UserDetailPage />} />
            <Route path="/courses" element={<CoursesPage />} />
            <Route path="/guests" element={<GuestsPage />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/users" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
