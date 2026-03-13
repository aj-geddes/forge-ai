import { useEffect, type HTMLAttributes } from "react";
import { create } from "zustand";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";

type ToastVariant = "default" | "destructive";

interface Toast {
  id: string;
  title: string;
  description?: string;
  variant?: ToastVariant;
}

interface ToastState {
  toasts: Toast[];
  addToast: (toast: Omit<Toast, "id">) => void;
  removeToast: (id: string) => void;
}

const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  addToast: (toast) =>
    set((state) => ({
      toasts: [
        ...state.toasts,
        { ...toast, id: `toast-${Date.now()}-${Math.random().toString(36).slice(2)}` },
      ],
    })),
  removeToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),
}));

export function useToast() {
  const { addToast, removeToast } = useToastStore();
  return {
    toast: (props: Omit<Toast, "id">) => addToast(props),
    dismiss: (id: string) => removeToast(id),
  };
}

function ToastItem({ id, title, description, variant = "default" }: Toast) {
  const { removeToast } = useToastStore();

  useEffect(() => {
    const timer = setTimeout(() => removeToast(id), 5000);
    return () => clearTimeout(timer);
  }, [id, removeToast]);

  return (
    <div
      className={cn(
        "pointer-events-auto relative flex w-full items-center justify-between space-x-4 overflow-hidden rounded-md border p-4 shadow-lg transition-all",
        variant === "destructive"
          ? "border-destructive bg-destructive text-primary-foreground"
          : "border bg-background text-foreground",
      )}
    >
      <div className="flex-1">
        <p className="text-sm font-semibold">{title}</p>
        {description && (
          <p className="text-sm opacity-90">{description}</p>
        )}
      </div>
      <button
        onClick={() => removeToast(id)}
        className="rounded-md p-1 opacity-70 hover:opacity-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}

export function Toaster({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  const { toasts } = useToastStore();

  if (toasts.length === 0) return null;

  return (
    <div
      className={cn(
        "fixed bottom-4 right-4 z-[100] flex max-w-[420px] flex-col gap-2",
        className,
      )}
      {...props}
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} {...toast} />
      ))}
    </div>
  );
}
