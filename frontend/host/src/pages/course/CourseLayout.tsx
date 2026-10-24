import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, History, ImageIcon, ListChecks, Radio, ShieldAlert, Users } from 'lucide-react';
import { api } from '../../lib/api';
import { cn } from '../../lib/utils';
import { Badge } from '../../components/ui/badge';
import type { Course } from '../../types/host';

const TABS = [
  { to: 'quizzes', label: 'Quizzes', Icon: ListChecks },
  { to: 'rooms', label: 'Rooms', Icon: Radio },
  { to: 'roster', label: 'Roster', Icon: Users },
  { to: 'sessions', label: 'Sessions', Icon: History },
  { to: 'images', label: 'Images', Icon: ImageIcon },
] as const;

/** Shared header + tabs for everything inside one course. */
export default function CourseLayout() {
  const { courseId } = useParams<{ courseId: string }>();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [course, setCourse] = useState<Course | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let ignore = false;
    setCourse(null);
    setForbidden(false);
    setLoading(true);
    api
      .get<Course[]>('/game/my-courses')
      .then((cs) => {
        if (ignore) return;
        const c = cs.find((x) => String(x.id) === courseId) ?? null;
        setCourse(c);
        setForbidden(c === null);
      })
      .catch(() => { if (!ignore) setForbidden(true); })
      .finally(() => { if (!ignore) setLoading(false); });
    return () => { ignore = true; };
  }, [courseId]);

  // The last path segment names the open tab; the indicator slides to its column.
  const segment = pathname.replace(/\/+$/, '').split('/').pop();
  const activeIndex = TABS.findIndex((t) => t.to === segment);

  const tab = ({ isActive }: { isActive: boolean }) =>
    cn(
      'relative z-10 flex min-h-11 items-center justify-center gap-2 rounded-pill px-1 text-xs font-semibold min-[400px]:text-sm sm:px-5 sm:text-base',
      'transition-colors duration-base ease-smooth',
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page',
      isActive ? 'text-accent-text' : 'text-secondary hover:text-primary'
    );

  return (
    <div className="mx-auto w-full max-w-5xl px-4 pb-16 pt-20 sm:px-8">
      <button
        onClick={() => navigate('/courses')}
        className="group -ml-2 mb-4 inline-flex min-h-9 items-center gap-2 rounded-lg px-2 text-sm font-semibold text-secondary transition-colors duration-fast hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page"
      >
        <ArrowLeft
          size={16}
          aria-hidden="true"
          className="transition-transform duration-fast ease-smooth group-hover:-translate-x-0.5"
        />
        All courses
      </button>

      {loading ? (
        <div aria-busy="true">
          <span className="sr-only">Loading course</span>
          <div className="skeleton h-12 w-2/3 max-w-md rounded-lg" aria-hidden="true" />
          <div className="skeleton mt-6 h-[3.25rem] w-full max-w-xl rounded-pill" aria-hidden="true" />
          <div className="skeleton mt-8 h-40 w-full rounded-2xl" aria-hidden="true" />
        </div>
      ) : (
        <>
          <header className="flex animate-fade-in flex-wrap items-center gap-x-4 gap-y-2">
            <h1 className="min-w-0 break-words text-title text-primary">{course ? course.name : 'Course'}</h1>
            {course && <Badge tone="accent" size="md">{course.semester}</Badge>}
          </header>

          {forbidden ? (
            <div role="alert" className="mt-8 flex animate-scale-in items-center gap-3 rounded-2xl glass p-6">
              <ShieldAlert size={24} className="shrink-0 text-danger" aria-hidden="true" />
              <p className="text-body-lg text-danger">You don't have host access to this course.</p>
            </div>
          ) : (
            <>
              <nav aria-label="Course sections" className="mb-8 mt-6">
                <div className="relative grid w-full grid-cols-5 rounded-pill glass-subtle p-1 sm:inline-grid sm:w-auto">
                  {activeIndex >= 0 && (
                    <span
                      aria-hidden="true"
                      className="absolute inset-y-1 left-1 w-[calc((100%-0.5rem)/5)] rounded-pill bg-accent-hint shadow-soft transition-transform duration-base ease-spring"
                      style={{ transform: `translateX(${activeIndex * 100}%)` }}
                    />
                  )}
                  {TABS.map(({ to, label, Icon }) => (
                    <NavLink key={to} to={to} className={tab}>
                      <Icon size={16} aria-hidden="true" className="hidden shrink-0 sm:block" />
                      {label}
                    </NavLink>
                  ))}
                </div>
              </nav>
              <div key={segment} className="animate-fade-in">
                <Outlet />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
