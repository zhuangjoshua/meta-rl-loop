import { Skeleton } from "../components/ui/skeleton";
import { useSession } from "../lib/hooks";

export function AppHomeScreen() {
  const { loading } = useSession();

  if (loading) {
    return (
      <div className="flex min-h-screen flex-col gap-4" aria-busy="true">
        <Skeleton className="h-8 w-1/3" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  return <section aria-hidden="true" className="min-h-screen" data-takyon-scaffold="app-home" />;
}
