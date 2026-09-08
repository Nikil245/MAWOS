import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { api, ApiError, setUnauthorizedHandler } from '../services/api';

const AuthContext = createContext(null);
const TOKEN_KEY = 'mawos_token';
const USER_KEY = 'mawos_user';
const TRANSIENT_KEYS = ['mawos_return_to', 'mawos_session_only'];

function clearRoleState() {
  for (const storage of [localStorage, sessionStorage]) {
    for (let i = storage.length - 1; i >= 0; i -= 1) {
      const key = storage.key(i);
      if (TRANSIENT_KEYS.includes(key) || key?.startsWith('mawos_role_') || key?.startsWith('mawos_scholarship_')) storage.removeItem(key);
    }
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => JSON.parse(localStorage.getItem(USER_KEY) || 'null'));
  const [checking, setChecking] = useState(Boolean(localStorage.getItem(TOKEN_KEY)) && !user);

  const clear = () => { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); clearRoleState(); setToken(null); setUser(null); setChecking(false); };
  useEffect(() => { setUnauthorizedHandler(clear); return () => setUnauthorizedHandler(null); });
  const login = async (username, password) => {
    // Login may follow an account switch; remove the previous identity and any
    // role-scoped navigation/cache state before accepting the next account.
    clear();
    const data = await api.login(username, password);
    localStorage.setItem(TOKEN_KEY, data.token); localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    setToken(data.token); setUser({ ...data.user, ai_mode: data.ai_mode });
    return data.user;
  };

  useEffect(() => {
    if (!token || user) { setChecking(false); return; }
    api.me(token).then((next) => { localStorage.setItem(USER_KEY, JSON.stringify(next)); setUser(next); })
      .catch((error) => { if (error instanceof ApiError && error.status === 401) clear(); })
      .finally(() => setChecking(false));
  }, [token, user]);

  const value = useMemo(() => ({ token, user, checking, login, logout: clear }), [token, user, checking]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}
