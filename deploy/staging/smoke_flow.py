"""Smoke-test a deployment: the product workflow through the WEB PROXY.

The path the browser and the extension use (docs/staging-deployment.md §13):
upload resume -> processing completes -> confirm profile -> save job -> requirements
-> match -> tailor -> download DOCX. Prints one line per step; exits non-zero on the
first failure. `--check-artifact RUN_ID SHA` re-downloads an artifact (persistence).

    python deploy/staging/smoke_flow.py --base https://easyapply-staging.onrender.com \
        --cookie "<the session cookie, name=value>" [--resume path.pdf]

Without --cookie it works only against a development-identity build. The default
resume is the invented golden resume (resume_profile_v1). Needs httpx
(services/api/.venv has it).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

TERMINAL = {"completed", "failed"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--cookie", default="")
    ap.add_argument(
        "--resume",
        default=str(
            Path(__file__).resolve().parents[2]
            / "services/api/evaluation/datasets/resume_profile_v1/resume.pdf"
        ),
    )
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--check-artifact", nargs=2, metavar=("RUN_ID", "SHA256"))
    ap.add_argument("--tag", default="flow")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    headers = {"Origin": base, "Sec-Fetch-Site": "same-origin"}
    if args.cookie:
        headers["Cookie"] = args.cookie
    api = httpx.Client(base_url=f"{base}/api/backend", headers=headers, timeout=120, follow_redirects=False)

    def step(name: str, response: httpx.Response, ok=(200, 201)) -> dict:
        body = response.json() if "json" in response.headers.get("content-type", "") else {}
        print(f"{name:<28} {response.status_code}", flush=True)
        if response.status_code not in ok:
            print("   body:", response.text[:600])
            raise SystemExit(1)
        return body

    def poll(name: str, path: str, status_of) -> dict:
        deadline = time.monotonic() + args.timeout
        started = time.monotonic()
        while True:
            body = api.get(path).json()
            status = status_of(body)
            if status in TERMINAL:
                print(f"{name:<28} {status} after {time.monotonic() - started:.0f}s", flush=True)
                if status != "completed":
                    print("   body:", json.dumps(body)[:800])
                    raise SystemExit(1)
                return body
            if time.monotonic() > deadline:
                print(f"{name:<28} TIMEOUT (last status {status})")
                raise SystemExit(1)
            time.sleep(5)

    if args.check_artifact:
        run_id, expected = args.check_artifact
        r = api.get(f"/tailored-resumes/{run_id}/artifact")
        digest = hashlib.sha256(r.content).hexdigest()
        print(f"artifact re-download        {r.status_code} sha256 {'MATCH' if digest == expected else 'DIFFERENT ' + digest}")
        return 0 if r.status_code == 200 and digest == expected else 1

    pdf = Path(args.resume).read_bytes()
    up = step("upload resume", api.post("/resumes", files={"file": ("resume.pdf", pdf, "application/pdf")}), ok=(201, 200))
    doc = up["id"]
    result = poll("resume processing", f"/resumes/{doc}/result", lambda b: (b.get("run") or {}).get("status"))
    version = result["result"]["profile_version_number"]
    step("confirm profile", api.post(f"/profile/versions/{version}/confirm", json={}))

    job = step(
        "save job",
        api.post(
            "/jobs",
            json={
                "source_type": "company_careers",
                "source_url": f"https://example.com/careers/{args.tag}",
                "external_job_id": args.tag,
                "job_title": "Backend Engineer",
                "company_name": "Acme Corp",
                "description_text": (
                    "Requirements:\n- Strong proficiency in Python\n- Experience with PostgreSQL\n"
                    "- Experience with distributed systems\n\nPreferred:\n- Experience with Kubernetes\n\n"
                    "Responsibilities:\n- Build and operate backend services\n"
                ),
            },
        ),
        ok=(201, 200),
    )
    job_id = job["id"]
    poll("job processing", f"/jobs/{job_id}", lambda b: (b.get("latest_run") or {}).get("status") or b.get("status"))
    req = step("requirements", api.get(f"/jobs/{job_id}/requirements"))
    print("   requirements:", len(req.get("requirements") or req.get("items") or []))
    match = step("match", api.post(f"/jobs/{job_id}/matches", json={}), ok=(201, 200))
    print("   verdict:", match.get("overall_verdict") or match.get("verdict"), "| results:", len(match.get("requirement_results") or []))
    run = step("start tailoring", api.post(f"/jobs/{job_id}/tailored-resumes", json={"match_result_id": match["id"]}), ok=(201, 200))
    poll("tailoring", f"/tailored-resumes/{run['id']}", lambda b: b.get("status"))
    art = api.get(f"/tailored-resumes/{run['id']}/artifact")
    print(f"{'download DOCX':<28} {art.status_code} {len(art.content)} bytes zip={art.content[:2] == b'PK'} "
          f"{art.headers.get('content-disposition', '')}")
    if art.status_code != 200 or art.content[:2] != b"PK":
        return 1
    print(f"RESULT run_id={run['id']} docx_sha256={hashlib.sha256(art.content).hexdigest()} document_id={doc} job_id={job_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
