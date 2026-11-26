import { create } from "zustand";
import { api, onUnauthorized, setAccess } from "../lib/api";
import type { User } from "../types/api";

type Auth = {
  token: string | null; user: User | null; login: (username: string, password: string) => Promise<void>; logout: () => void;
};
export const useAuthStore = create<Auth>(set => ({
  token: null, user: null,
  async login(username, password) {
    const { data } = await api.post<{access_token: string}>("/auth/login", { username, password });
    setAccess(data.access_token);
    try {
      const { data: user } = await api.get<User>("/auth/me");
      set({ token: data.access_token, user });
    } catch (error) { setAccess(null); throw error; }
  },
  logout() { setAccess(null); set({ token: null, user: null }); },
}));
onUnauthorized(() => useAuthStore.getState().logout());
