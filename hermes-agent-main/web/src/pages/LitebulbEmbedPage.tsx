import { useLocation } from "react-router-dom";

import { TAKYON_BASE_PATH } from "@/lib/api";

export default function LitebulbEmbedPage() {
  const { search } = useLocation();
  const src = `${TAKYON_BASE_PATH}/litebulb/index.html${search || ""}`;

  return (
    <iframe
      title="Litebulb"
      src={src}
      className="block h-full w-full flex-1 border-0 bg-transparent"
    />
  );
}
