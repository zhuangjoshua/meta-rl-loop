// Canonical product action — copy this SHAPE for any AI/backend action.
//
// `ctx` IS the shared runtime client: the SAME object the browser UI gets from
// `createSubuserRuntimeClient` (see _takyon/runtime-client.js). So you call the rails exactly like
// the browser does — `ctx.generate(...)`, `ctx.invokeAction(...)`, `ctx.saveRecord(...)`,
// `ctx.listRecords(...)`, `ctx.search(...)`. There is no separate "ctx" shape to learn and nothing
// to wire by hand: one client, both sides.
//
// Authority stays server-side: the runtime attaches the customer's business-scoped session token,
// and the rail enforces auth/plan/budget. Inside an action there is NO filesystem write, NO shell,
// NO provider credential or base-url env, NO provider SDK, and NO remote import.
//
// This file's name starts with "_", so the action runtime ignores it — it is a reference only.
// Copy it to `actions/<your-action>.ts` and rename the export's intent.
export default async function run(payload: TakyonActionPayload, ctx: TakyonActionContext) {
  const data = await ctx.generate({
    max_tokens: 1024,
    system: "You are a helpful assistant.",
    messages: [{ role: "user", content: String((payload && payload.prompt) || "") }],
  });
  // `ctx.generate` returns { text, content, model, usage }. Return plain JSON the UI can render.
  return { reply: data.text };
}
