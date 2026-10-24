import { useEffect, useState } from 'react';
import { Plus, Pencil, ExternalLink } from 'lucide-react';
import { api } from '../lib/api';
import { hostAppLink } from '../lib/hostApp';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader } from '../components/ui/card';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { staggerStyle } from '../components/ui/motion';

interface Course {
  id: number;
  name: string;
  semester: string;
  created_at: string;
  hosts: { user_id: string; display_name: string }[];
  active_quiz_count: number;
  archived_quiz_count: number;
}

export default function CoursesPage() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [semester, setSemester] = useState('');
  const [saving, setSaving] = useState(false);
  const [editingCourse, setEditingCourse] = useState<Course | null>(null);
  const [editName, setEditName] = useState('');
  const [editSemester, setEditSemester] = useState('');
  const [editSaving, setEditSaving] = useState(false);

  async function load() {
    try {
      const data = await api.get<Course[]>('/admin/courses');
      setCourses(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load courses');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await api.post('/admin/courses', { name, semester });
      setName('');
      setSemester('');
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create course');
    } finally {
      setSaving(false);
    }
  }

  function startEdit(c: Course) {
    setEditingCourse(c);
    setEditName(c.name);
    setEditSemester(c.semester);
    setShowForm(false);
  }

  async function handleEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!editingCourse) return;
    setEditSaving(true);
    setError('');
    try {
      await api.put(`/admin/courses/${editingCourse.id}`, {
        name: editName,
        semester: editSemester,
      });
      setEditingCourse(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update course');
    } finally {
      setEditSaving(false);
    }
  }

  return (
    <div className="p-4 sm:p-6 max-w-4xl">
      <div className="flex items-center justify-between gap-3 mb-6">
        <div className="flex items-center gap-3">
          <h2 className="text-heading text-primary">Courses</h2>
          {!loading && <Badge tone="neutral" className="tabular-nums">{courses.length}</Badge>}
        </div>
        <Button onClick={() => { setShowForm(!showForm); setEditingCourse(null); }} size="sm">
          <Plus size={16} /> New Course
        </Button>
      </div>

      {error && <p role="alert" className="mb-4 rounded-lg bg-danger-tint px-3 py-2 text-sm text-danger">{error}</p>}

      {showForm && (
        <Card variant="glass" className="mb-6 animate-slide-up">
          <CardHeader><h3 className="text-subheading text-primary">Create Course</h3></CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="flex gap-3 flex-wrap">
              <Input
                placeholder="Course name (e.g. CS 537)"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="flex-1 min-w-48"
                required
              />
              <Input
                placeholder="Semester (e.g. Fall 2026)"
                value={semester}
                onChange={(e) => setSemester(e.target.value)}
                className="flex-1 min-w-48"
                required
              />
              <Button type="submit" loading={saving}>{saving ? 'Creating…' : 'Create'}</Button>
              <Button type="button" variant="ghost" onClick={() => setShowForm(false)}>Cancel</Button>
            </form>
          </CardContent>
        </Card>
      )}

      {editingCourse && (
        <Card variant="glass" className="mb-6 animate-slide-up">
          <CardHeader><h3 className="text-subheading text-primary">Edit Course</h3></CardHeader>
          <CardContent>
            <form onSubmit={handleEdit} className="flex gap-3 flex-wrap">
              <Input
                placeholder="Course name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="flex-1 min-w-48"
                required
              />
              <Input
                placeholder="Semester"
                value={editSemester}
                onChange={(e) => setEditSemester(e.target.value)}
                className="flex-1 min-w-48"
                required
              />
              <Button type="submit" loading={editSaving}>{editSaving ? 'Saving…' : 'Save'}</Button>
              <Button type="button" variant="ghost" onClick={() => setEditingCourse(null)}>Cancel</Button>
            </form>
          </CardContent>
        </Card>
      )}

      {loading ? (
        <div className="space-y-3" aria-busy="true" aria-label="Loading courses">
          {[0, 1, 2].map((i) => <div key={i} className="skeleton h-24 rounded-xl" />)}
        </div>
      ) : courses.length === 0 ? (
        <Card variant="glass-subtle" className="px-6 py-10 text-center">
          <p className="text-subheading text-primary">No courses yet</p>
          <p className="mt-1 text-body text-muted">Create one above.</p>
        </Card>
      ) : (
        <ul className="space-y-3">
          {courses.map((c, i) => (
            <li key={c.id} className="animate-slide-up" style={staggerStyle(i)}>
              <Card variant="raised" className="flex flex-wrap items-center justify-between gap-4 px-5 py-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <p className="text-subheading text-primary">{c.name}</p>
                    <Badge tone="accent">{c.semester}</Badge>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Badge tone="success" className="tabular-nums">{c.active_quiz_count} active</Badge>
                    <Badge tone="neutral" className="tabular-nums">{c.archived_quiz_count} archived quizzes</Badge>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-caption text-muted">
                    <span>Hosts:</span>
                    {c.hosts.length ? (
                      c.hosts.map((h) => <Badge key={h.user_id} tone="outline">{h.display_name}</Badge>)
                    ) : (
                      <span>none yet (grant HOST access from Users)</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => startEdit(c)}
                    aria-label={`Edit ${c.name}`}
                    title="Edit course"
                  >
                    <Pencil size={14} />
                  </Button>
                  <a
                    href={hostAppLink(`/courses/${c.id}/quizzes`)}
                    className="inline-flex min-h-8 items-center gap-1 rounded-lg border border-line-strong px-3 py-1 text-sm font-semibold text-primary transition-colors duration-fast hover:bg-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-page"
                    title="Quizzes, roster, rooms, and sessions are managed in the Host app"
                  >
                    Manage in Host app <ExternalLink size={13} />
                  </a>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
