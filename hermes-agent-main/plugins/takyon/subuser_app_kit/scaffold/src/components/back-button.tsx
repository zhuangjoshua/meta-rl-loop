import { useNavigate } from "react-router-dom";

export function BackButton({ fallback = "/" }: { fallback?: string }) {
  const navigate = useNavigate();

  return (
    <button
      type="button"
      onClick={() => {
        if (window.history.length > 1) navigate(-1);
        else navigate(fallback);
      }}
      className="inline-flex w-fit items-center gap-2 rounded px-3 py-2 text-sm font-medium text-foreground hover:bg-muted"
      aria-label="Go back"
    >
      <span aria-hidden="true">←</span>
      Back
    </button>
  );
}
