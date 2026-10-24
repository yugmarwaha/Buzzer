import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Card } from '../components/ui/card';
import { Spinner } from '../components/ui/spinner';

/**
 * Landing page for OAuth2 returns (/player/login?from=oauth2#oauth2_data=...).
 * Exchanges the temp token for a full access token, then sends the player to Past
 * Quizzes (sessionStorage.postLoginPath) or back to the room they were joining
 * (sessionStorage.joinRoomCode).
 */
export default function LoginPage() {
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  useEffect(() => {
    if (searchParams.get('from') !== 'oauth2') {
      sessionStorage.removeItem('postLoginPath');
      navigate('/join', { replace: true });
      return;
    }

    const oauthError = searchParams.get('error');
    if (oauthError) {
      sessionStorage.removeItem('postLoginPath');
      setError(oauthError);
      return;
    }

    const match = window.location.hash.match(/oauth2_data=([^&]+)/);
    if (!match) {
      sessionStorage.removeItem('postLoginPath');
      setError('Invalid authentication response');
      return;
    }

    let tempToken: string;
    try {
      const data = JSON.parse(decodeURIComponent(match[1]));
      tempToken = data.temp_token;
      if (!tempToken) throw new Error();
    } catch {
      sessionStorage.removeItem('postLoginPath');
      setError('Invalid authentication response');
      return;
    }

    fetch('/api/auth/exchange-temp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tempToken}` },
    })
      .then(res => res.ok ? res.json() : res.json().then((e: { detail?: string }) => Promise.reject(e.detail ?? 'Sign-in failed')))
      .then((data: { access_token: string }) => {
        localStorage.setItem('token', data.access_token);
        window.history.replaceState(null, '', window.location.pathname);
        // Past Quizzes sign-in wins over a pending room join; both are cleared after use
        // so a later room join isn't sent to /past.
        const postLoginPath = sessionStorage.getItem('postLoginPath');
        sessionStorage.removeItem('postLoginPath');
        const roomCode = sessionStorage.getItem('joinRoomCode');
        sessionStorage.removeItem('joinRoomCode');
        if (postLoginPath === '/past') {
          navigate('/past', { replace: true });
        } else {
          navigate(roomCode ? `/name/${roomCode}` : '/join', { replace: true });
        }
      })
      .catch((msg: unknown) => {
        sessionStorage.removeItem('postLoginPath');
        setError(typeof msg === 'string' ? msg : 'Sign-in failed. Please try again.');
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center p-4">
        <Card variant="glass-strong" className="w-full max-w-sm animate-scale-in space-y-4 p-6 text-center">
          <p role="alert" className="text-body-lg text-danger">{error}</p>
          <button
            onClick={() => navigate('/join')}
            className="min-h-11 text-body font-semibold text-accent-text hover:underline"
          >
            ← Back to join
          </button>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <p className="flex animate-fade-in items-center gap-3 text-body-lg text-secondary">
        <Spinner /> Signing in…
      </p>
    </div>
  );
}
