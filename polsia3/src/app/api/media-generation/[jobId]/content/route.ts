import { requireProfileForApi } from "@/lib/auth";
import { db } from "@/lib/db";
import { NotFoundError } from "@/lib/errors";
import { jsonError } from "@/lib/http";
import { downloadOpenAiVideoContent } from "@/lib/vendors/openai-video";

type RouteContext = {
  params: Promise<{ jobId: string }>;
};

export async function GET(_request: Request, context: RouteContext) {
  try {
    const requestUrl = new URL(_request.url);
    const token = requestUrl.searchParams.get("token")?.trim() || "";
    const { jobId } = await context.params;
    const sql = db();

    let job: { provider: string; provider_job_id: string | null } | undefined;
    if (token.length >= 24) {
      const tokenRows = await sql<{ provider: string; provider_job_id: string | null }[]>`
        SELECT provider, provider_job_id
        FROM media_generation_jobs
        WHERE id = ${jobId}
          AND input->>'public_media_token' = ${token}
        LIMIT 1
      `;
      job = tokenRows[0];
    }

    if (!job) {
      const profile = await requireProfileForApi();
      const rows = await sql<{ provider: string; provider_job_id: string | null }[]>`
        SELECT mj.provider, mj.provider_job_id
        FROM media_generation_jobs mj
        JOIN business_memberships bm ON bm.business_id = mj.business_id
        WHERE mj.id = ${jobId}
          AND bm.profile_id = ${profile.id}
        LIMIT 1
      `;
      job = rows[0];
    }

    if (!job?.provider_job_id || job.provider !== "openai") throw new NotFoundError("Media generation content not found.");

    const upstream = await downloadOpenAiVideoContent(job.provider_job_id);
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") || "video/mp4",
        "Cache-Control": "private, max-age=300"
      }
    });
  } catch (error) {
    return jsonError(error);
  }
}
