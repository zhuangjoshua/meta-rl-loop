export function StatusPill({ status }: { status: string }) {
  return <span className={`status-pill ${status}`}>{status.replace(/_/g, " ")}</span>;
}
