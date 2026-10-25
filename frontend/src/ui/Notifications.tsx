import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import * as Toast from "@radix-ui/react-toast";

export type NoticeKind = "info" | "ok" | "warn" | "error";
interface Notice { id: number; kind: NoticeKind; title: string; body?: string }

const Ctx = createContext<(n: Omit<Notice, "id">) => void>(() => {});
export const useNotify = () => useContext(Ctx);

let seq = 0;

// Completion and failure notices (C-3). Errors stay until dismissed so a
// failure is never missed; others clear themselves.
export function NotificationsProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Notice[]>([]);
  const notify = useCallback((n: Omit<Notice, "id">) => setItems((s) => [...s, { ...n, id: ++seq }]), []);
  const drop = (id: number) => setItems((s) => s.filter((n) => n.id !== id));
  const value = useMemo(() => notify, [notify]);

  return (
    <Ctx.Provider value={value}>
      <Toast.Provider label="Notifications" swipeDirection="right">
        {children}
        {items.map((n) => (
          <Toast.Root
            key={n.id}
            className="toast"
            data-kind={n.kind}
            type={n.kind === "error" ? "foreground" : "background"}
            duration={n.kind === "error" ? Infinity : 8000}
            onOpenChange={(open) => !open && drop(n.id)}
          >
            <Toast.Title className="toast-title">{n.title}</Toast.Title>
            {n.body && <Toast.Description>{n.body}</Toast.Description>}
            <Toast.Close className="toast-close" aria-label="Dismiss">×</Toast.Close>
          </Toast.Root>
        ))}
        <Toast.Viewport className="toast-viewport" />
      </Toast.Provider>
    </Ctx.Provider>
  );
}
