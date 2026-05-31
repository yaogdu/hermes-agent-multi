import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const STORAGE_KEY = "agentops_session_token";
const SESSION_HEADER = "X-AgentOps-Session";

export interface ControlUser {
  id: string;
  username: string;
  display_name: string;
  role: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

export interface AuthState {
  token: string | null;
  user: ControlUser | null;
  isAdmin: boolean;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  getAuthHeaders: () => Record<string, string>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

function getStoredToken(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function storeToken(token: string) {
  try {
    localStorage.setItem(STORAGE_KEY, token);
  } catch {
    // localStorage might be unavailable (private browsing, etc.)
  }
}

function clearToken() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

async function fetchMe(token: string): Promise<{
  user: ControlUser;
  scope: { all: boolean; hermes_user_ids: string[] };
} | null> {
  try {
    const res = await fetch("/api/auth/me", {
      headers: { [SESSION_HEADER]: token },
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(getStoredToken);
  const [user, setUser] = useState<ControlUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Validate stored token on mount
  useEffect(() => {
    const stored = getStoredToken();
    if (!stored) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    fetchMe(stored).then((data) => {
      if (cancelled) return;
      if (data) {
        setToken(stored);
        setUser(data.user);
      } else {
        clearToken();
        setToken(null);
      }
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "Login failed");
      throw new Error(text);
    }
    const data = await res.json();
    storeToken(data.token);
    setToken(data.token);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    if (token) {
      try {
        await fetch("/api/auth/logout", {
          method: "POST",
          headers: { [SESSION_HEADER]: token },
        });
      } catch {
        // best-effort
      }
    }
    clearToken();
    setToken(null);
    setUser(null);
  }, [token]);

  const getAuthHeaders = useCallback((): Record<string, string> => {
    return token ? { [SESSION_HEADER]: token } : {};
  }, [token]);

  const value = useMemo<AuthState>(
    () => ({
      token,
      user,
      isAdmin: user?.role === "admin",
      loading,
      login,
      logout,
      getAuthHeaders,
    }),
    [token, user, loading, login, logout, getAuthHeaders],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
