/**
 * GET /api/health — liveness: this web server process is answering.
 *
 * What a hosting platform polls to decide whether to restart the process, so
 * it deliberately depends on nothing else. An API or database outage must
 * leave a running web app telling people the API is unreachable, not get the
 * web app restarted in a loop. Whether the deployment can actually serve
 * someone is /api/health/ready.
 *
 * No Host check, unlike every other route here: a platform's own probe need
 * not arrive with the public host, and the answer is a constant that says
 * nothing about anyone.
 */

export const dynamic = "force-dynamic";

export function GET() {
  return Response.json({ status: "ok", service: "easyapply-web" }, { headers: { "Cache-Control": "no-store" } });
}
