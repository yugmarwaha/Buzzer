import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ThemeToggle } from './components/ui/ThemeToggle';
import LoginPage from './pages/LoginPage';
import CoursesPage from './pages/CoursesPage';
import CourseLayout from './pages/course/CourseLayout';
import QuizzesPage from './pages/course/QuizzesPage';
import RoomsPage from './pages/course/RoomsPage';
import RosterPage from './pages/course/RosterPage';
import SessionsPage from './pages/course/SessionsPage';
import ImagesPage from './pages/course/ImagesPage';
import QuestionEditorPage from './pages/QuestionEditorPage';
import { hasLiveToken } from './lib/api';
import GameLayout from './pages/game/GameLayout';
import LobbyPage from './pages/game/LobbyPage';
import QuestionPage from './pages/game/QuestionPage';
import ResultsPage from './pages/game/ResultsPage';
import GameOverPage from './pages/game/GameOverPage';

function RequireAuth({ children }: { children: React.ReactNode }) {
  if (!localStorage.getItem('token')) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** "/" and unknown paths: users with a live token go to the course list, others to login. */
function RootRedirect() {
  return <Navigate to={hasLiveToken() ? '/courses' : '/login'} replace />;
}

/** Top-right everywhere, but top-left on game screens where End game sits top-right. */
function ThemeCorner() {
  const onGame = useLocation().pathname.startsWith('/game/');
  return <ThemeToggle className={`fixed top-3 z-40 ${onGame ? 'left-3' : 'right-3'}`} />;
}

export default function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <main>
      <ThemeCorner />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        {/* Course-first: pick a course, then its quizzes / rooms / roster / sessions */}
        <Route path="/courses" element={<RequireAuth><CoursesPage /></RequireAuth>} />
        <Route path="/courses/:courseId" element={<RequireAuth><CourseLayout /></RequireAuth>}>
          <Route index element={<Navigate to="quizzes" replace />} />
          <Route path="quizzes" element={<QuizzesPage />} />
          <Route path="rooms" element={<RoomsPage />} />
          <Route path="roster" element={<RosterPage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="images" element={<ImagesPage />} />
        </Route>
        <Route path="/quizzes/:gameId/edit" element={<RequireAuth><QuestionEditorPage /></RequireAuth>} />
        <Route path="/home" element={<Navigate to="/courses" replace />} />
        <Route path="/game/:code" element={<RequireAuth><GameLayout /></RequireAuth>}>
          <Route path="lobby" element={<LobbyPage />} />
          <Route path="question" element={<QuestionPage />} />
          <Route path="results" element={<ResultsPage />} />
          <Route path="gameover" element={<GameOverPage />} />
        </Route>
        <Route path="*" element={<RootRedirect />} />
      </Routes>
      </main>
    </BrowserRouter>
  );
}
