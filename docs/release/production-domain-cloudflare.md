# Production domain and Cloudflare

This is an operator checklist. It does not create a domain, Cloudflare zone,
Render custom domain, certificate or paid resource.

## Required inputs

Record the registered domain, chosen production hostname, Cloudflare zone ID,
Render production service ID and hostname, DNS administrator, Render workspace
administrator and the approved maintenance window. Use a production hostname
such as `control.example.ie`; keep staging on a distinct hostname.

## Safe sequence

1. Deploy and verify the exact gated SHA on Render's production `onrender.com`
   hostname. Do not direct pharmacy users to it yet.
2. Add the chosen custom hostname to the production Render service. Copy the
   exact DNS target Render displays; do not infer it from a project name.
3. Put only the custom hostname in `CLOUD_ALLOWED_HOSTS`. Render automatically
   contributes `RENDER_EXTERNAL_HOSTNAME`. Never allow wildcards.
4. Create the exact Cloudflare DNS record Render requests. Begin DNS-only if
   Render needs direct validation. Wait for Render to show the custom domain and
   certificate as verified before enabling the Cloudflare proxy.
5. Set Cloudflare SSL/TLS to **Full (strict)**. Do not use Flexible mode. Keep
   WebSockets enabled and do not cache application HTML, API, authentication,
   health or evidence responses. The application emits `no-store`.
6. Apply rate limiting/WAF rules first in log or challenge mode and exercise
   login, MFA, downloads, dashboard refresh and evidence byte ranges. A generic
   body-size or bot rule can break these flows.
7. Run `deployment/check_cloud_health.py` and
   `deployment/verify_cloud_release.py` against the custom origin, then perform a
   named-owner MFA check. Record DNS values, certificate state, Cloudflare mode,
   Render deploy ID, exact SHA and UTC time.
8. Lower DNS TTL before a planned cutover. For rollback, point the custom record
   only to the last verified compatible Render service. Application rollback
   never reverses a database migration or deletes evidence.

Authenticated Origin Pulls cannot be treated as enabled merely because
Cloudflare proxying is on: Render must explicitly support and be configured to
validate the Cloudflare client certificate. Until that end-to-end evidence
exists, rely on Render TLS, Full (strict), host validation and the application
security boundary.

References: [Cloudflare Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)
and [Render custom domains](https://render.com/docs/custom-domains).
