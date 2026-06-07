import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, TAKYON_BASE_PATH } from "@/lib/api";

export type AuthStatus = "loading" | "in" | "out";

export interface AuthUser {
  email: string;
  name: string;
  sub?: string;
}

export interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
  login: (returnTo?: string) => void;
  logout: (returnTo?: string) => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function currentReturnPath() {
  if (typeof window === "undefined") return "/chat";
  const path = `${window.location.pathname || "/"}${window.location.search || ""}${window.location.hash || ""}`;
  return path || "/chat";
}

function authPath(path: string, returnTo?: string) {
  const target = `${TAKYON_BASE_PATH}${path}`;
  const destination = returnTo || currentReturnPath();
  return `${target}?return_to=${encodeURIComponent(destination)}`;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);

  const refresh = async () => {
    try {
      const state = await api.getDashboardAuthState();
      if (state.authenticated) {
        setUser({
          email: String(state.user?.email || ""),
          name: String(state.user?.name || state.user?.email || "Operator"),
          sub: state.user?.sub,
        });
        setStatus("in");
        return;
      }
      setUser(null);
      setStatus("out");
    } catch {
      setUser(null);
      setStatus("out");
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const value = useMemo<AuthState>(() => ({
    status,
    user,
    login: (returnTo?: string) => {
      if (typeof window === "undefined") return;
      window.location.assign(authPath("/auth/login", returnTo));
    },
    logout: (returnTo?: string) => {
      if (typeof window === "undefined") return;
      window.location.assign(authPath("/auth/logout", returnTo));
    },
    refresh,
  }), [status, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return context;
}
