import { useEffect } from "react";
import { Outlet, useSearchParams } from "react-router-dom";

export function AppLayout() {
  const [searchParams, setSearchParams] = useSearchParams();
  const checkout = searchParams.get("checkout");

  useEffect(() => {
    if (checkout !== "success" && checkout !== "cancel") return;
    const next = new URLSearchParams(searchParams);
    next.delete("checkout");
    setSearchParams(next, { replace: true });
  }, [checkout, searchParams, setSearchParams]);

  return (
    <div className="min-h-screen" data-takyon-scaffold="app-layout">
      <Outlet />
    </div>
  );
}
