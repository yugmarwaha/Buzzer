import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronRight, GraduationCap, LogOut, Radio } from 'lucide-react';
import { api, errorMessage } from '../lib/api';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import type { Course } from '../types/host';

interface MySession {
  session_id: string;
  room_code: string;
  status: string;
  game_title: string;
  course_id: number;
  course_name: string;
  course_semester: string;
}

/** Host landing page: pick a course. Admins see every course. */
export default function CoursesPage() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [mine, setMine] = useState<MySession[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    Promise.all([
      api.get<Course[]>('/game/my-courses'),
      api.get<MySession[]>('/game/my-active-sessions'),
    ])
      .then(([c, s]) => {
        setCourses(c);
        setMine(s);
      })
      .catch((err) => setError(errorMessage(err, 'Failed to load courses. Are you still signed in?')))
      .finally(() => setLoading(false));
  }, []);

  function logout() {
    localStorage.removeItem('token');
    navigate('/login');
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-8 px-4 pb-16 pt-20 sm:px-8">
      <header className="flex animate-fade-in flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-title text-primary">Your courses</h1>
          <p className="mt-2 max-w-prose text-body-lg text-secondary">
            Choose a course to manage its quizzes, rooms, roster, and sessions.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={logout}>
          <LogOut size={16} aria-hidden="true" /> Sign Out
        </Button>
      </header>

      {error && (
        <p role="alert" className="animate-slide-down rounded-lg bg-danger-tint px-4 py-3 text-body text-danger">
          {error}
        </p>
      )}

      {mine.length > 0 && (
        <Card
          variant="question"
          tone="warning"
          edge="left"
          role="region"
          aria-labelledby="open-rooms-title"
          className="animate-slide-up p-5 pl-7 sm:p-6 sm:pl-8"
        >
          <div className="flex items-center gap-3">
            <Radio size={20} className="text-warning" aria-hidden="true" />
            <h2 id="open-rooms-title" className="text-subheading text-primary">Your open rooms</h2>
            <Badge tone="warning" className="tabular-nums">{mine.length}</Badge>
          </div>
          <ul className="mt-4 divide-y divide-line">
            {mine.map((s) => (
              <li key={s.session_id} className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <div className="min-w-0">
                  <p className="truncate text-body-lg font-semibold text-primary">{s.game_title}</p>
                  <p className="text-caption text-muted">
                    {s.course_name}, {s.course_semester}. Room{' '}
                    <span className="font-mono font-semibold tracking-widest text-secondary">{s.room_code}</span>
                  </p>
                </div>
                {/* The list is DB-based with no live flag, so send the host to the course's Rooms
                    tab, which shows whether the room is running and offers Rejoin/End. */}
                <Button size="sm" variant="secondary" onClick={() => navigate(`/courses/${s.course_id}/rooms`)}>
                  Manage
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <section aria-label="Courses" aria-busy={loading || undefined}>
        {loading && (
          <div className="grid gap-4 sm:grid-cols-2">
            <span className="sr-only">Loading courses</span>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="skeleton h-24 rounded-xl" aria-hidden="true" />
            ))}
          </div>
        )}

        {!loading && courses.length === 0 && (
          <div className="flex animate-scale-in flex-col items-center gap-3 rounded-2xl glass px-6 py-12 text-center">
            <GraduationCap size={32} className="text-muted" aria-hidden="true" />
            <p className="max-w-sm text-body-lg text-secondary">
              You don't have host access to any course yet. Ask an admin to grant it.
            </p>
          </div>
        )}

        {!loading && courses.length > 0 && (
          <ul className="stagger grid gap-4 sm:grid-cols-2">
            {courses.map((c) => (
              <li key={c.id} className="animate-slide-up">
                <button
                  onClick={() => navigate(`/courses/${c.id}/quizzes`)}
                  className="lift group flex w-full items-center gap-4 rounded-xl border border-line bg-surface p-5 text-left shadow-soft hover:border-line-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page"
                >
                  <span
                    aria-hidden="true"
                    className="flex size-12 shrink-0 items-center justify-center rounded-lg bg-accent-hint text-heading text-accent-text"
                  >
                    {c.name.trim().charAt(0).toUpperCase() || '?'}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-subheading text-primary">{c.name}</span>
                    <span className="mt-0.5 block text-caption text-muted">{c.semester}</span>
                  </span>
                  <ChevronRight
                    size={20}
                    aria-hidden="true"
                    className="shrink-0 text-muted transition-transform duration-fast ease-smooth group-hover:translate-x-1 group-hover:text-primary"
                  />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
