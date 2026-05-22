import { getTavilyEnv } from "../env";

export type TavilyResult = {
  title: string;
  url: string;
  content?: string;
  score?: number;
};

export async function tavilySearch(input: { query: string; maxResults?: number }) {
  const env = getTavilyEnv();
  const response = await fetch("https://api.tavily.com/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      api_key: env.TAVILY_API_KEY,
      query: input.query,
      search_depth: "basic",
      max_results: input.maxResults ?? 5,
      include_answer: false,
      include_raw_content: false
    }),
    signal: AbortSignal.timeout(45_000)
  });
  const json = (await response.json().catch(() => null)) as { results?: TavilyResult[]; error?: string } | null;
  if (!response.ok) throw new Error(json?.error || `Tavily returned ${response.status}.`);
  return (json?.results ?? []).filter((result) => result.url && result.title);
}
