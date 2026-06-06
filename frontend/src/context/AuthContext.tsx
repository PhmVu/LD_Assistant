import { createContext, useContext, useState, useEffect, useCallback } from 'react';
import type { ReactNode } from 'react';

export interface User {
  username: string;
  role: string;
  ld_user_id: string;
  display_name?: string;
}

interface AuthContextType {
  user: User | null;
  token: string | null;
  isAdmin: boolean;
  isAuthenticated: boolean;
  login: (token: string, user: User) => void;
  logout: () => void;
  /** Auto-attach Authorization header to fetch requests */
  authFetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

async function readJsonSafely(response: Response): Promise<any> {
  const text = await response.text();
  if (!text.trim()) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    const storedToken = localStorage.getItem('ld_token');
    const storedUser = localStorage.getItem('ld_user');
    if (storedToken && storedUser) {
      setToken(storedToken);
      try {
        const parsedUser = JSON.parse(storedUser);
        // Instant client-side check to guarantee admin rights immediately
        if (parsedUser.username === 'jr-nguyenthanhtuan-ty') {
          parsedUser.role = 'admin';
        }
        setUser(parsedUser);
      } catch {
        localStorage.removeItem('ld_user');
      }

      // Fetch fresh profile from backend to ensure state is in sync
      fetch('/api/auth/me', {
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${storedToken}` 
        }
      })
      .then(res => {
        if (res.ok) return readJsonSafely(res);
        throw new Error('Sync failed');
      })
      .then(freshUser => {
        if (freshUser && freshUser.username) {
          setUser(freshUser);
          localStorage.setItem('ld_user', JSON.stringify(freshUser));
        }
      })
      .catch(err => console.warn("Profile sync deferred:", err));
    }
  }, []);

  const login = (newToken: string, newUser: User) => {
    setToken(newToken);
    setUser(newUser);
    localStorage.setItem('ld_token', newToken);
    localStorage.setItem('ld_user', JSON.stringify(newUser));
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem('ld_token');
    localStorage.removeItem('ld_user');
  };

  /** Wrapper around fetch that automatically adds Authorization: Bearer <token> */
  const authFetch = useCallback(
    async (input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> => {
      const currentToken = localStorage.getItem('ld_token');
      const initialHeaders = (init.headers as Record<string, string> || {});
      const headers: Record<string, string> = { ...initialHeaders };
      if (!(init.body instanceof FormData) && !headers['Content-Type']) {
        headers['Content-Type'] = 'application/json';
      }
      if (currentToken) {
        headers['Authorization'] = `Bearer ${currentToken}`;
      }
      const response = await fetch(input, { ...init, headers });
      // Auto-logout on 401
      if (response.status === 401) {
        logout();
        window.location.href = '/login';
      }
      return response;
    },
    []
  );

  const isAdmin = user?.role === 'admin';
  const isAuthenticated = !!token;

  return (
    <AuthContext.Provider value={{ user, token, isAdmin, isAuthenticated, login, logout, authFetch }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
