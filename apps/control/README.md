# AisleSignals online management interface

This React/TypeScript console manages a pharmacy group's accounts, branch records, laptop connection reports, observations and staff-reviewed cases. CCTV selection, local inference and speaker commissioning remain in the separate laptop application. The console never treats a connected laptop as proof of monitoring or theft-detection accuracy.

## Build and serve

```sh
npm --prefix apps/control ci
npm --prefix apps/control run build
npm --prefix apps/control test
```

The cloud application serves `apps/control/dist/index.html` at `/` and its generated assets at `/assets/`. All JSON requests use the same origin under `/control-api`; the interface contains no public API-host setting, external font, CDN or inline React style. Its production build works with the cloud application's `script-src 'self'; style-src 'self'` policy. Dependencies are pinned to the corresponding existing web-app versions; the large local vision dependency is not included here.

`npm --prefix apps/control run preview` uses isolated loopback port60642. This static preview alone does not provide accounts or management data: use the cloud backend with a separately configured local PostgreSQL test database for functional work. Real records must never be placed into frontend fixtures. The root integration suite in `tests/control-e2e` exercises the real backend independently.

## Access and operation

- A deployment administrator supplies the private first-owner setup token. The owner creates a password and verifies an authenticator before a session is issued.
- Owners create pharmacy records and private invitations. Invitations are shown once for manual sharing; the interface does not send email. Invitees create their own password and authenticator. Roles and pharmacy assignments are enforced by the API.
- The Downloads tab provides the tested unsigned Apple-silicon macOS pilot `.dmg`, plus the separate Python 3 connection utility for macOS and Windows, platform-specific requirements and a direct route into secure pairing. The page distinguishes the local pilot application from cloud connection and states that signed/notarised customer installers remain pending.
- Owners/managers generate a short-lived laptop connection code from Laptops. The screen shows the matching name/platform, private code prompt and exact platform command. Downloading the utility alone does not connect a laptop, select CCTV or activate monitoring.
- Staff acknowledge observations, record factual review outcomes and optionally create an incident. Cases can be updated or closed. Changed versions require a refresh; no review overwrites a competing decision.
- Lists show the newest200 available server records. Searches and the applicable status/role filters narrow those records. The pharmacy scope applies only to pharmacies the server permits.

## Client isolation and locking

HttpOnly session cookies are server-owned. CSRF is held only in memory. All reads/mutations have bounded requests and cancellation. An identity change aborts previous requests and clears rows, selected scopes, drawers and form continuations. A constant, non-sensitive BroadcastChannel signal locks sibling tabs when a login/logout changes their shared cookie; account details are never broadcast.

Pointer/keyboard/wheel activity maintains a15-minute local idle clock. Background polling does not. Expiry removes the console immediately, attempts server logout and requires credentials plus MFA again. A per-tab sessionStorage boolean named `aislesignals-control-locked` prevents a reload from silently restoring a cookie after failed offline logout. It stores no identity, token or records. When browser storage is blocked, the current page still locks but cannot persist that local reload marker. Normal manual sign-out reports a server failure and leaves a retry available.

The page refreshes while visible and labels connectivity separately from reported monitoring. A cached online device report loses its online presentation after its heartbeat freshness expires. Unknown, disconnected or stale reports never display confirmed active monitoring. Service errors and offline state are visible; no demonstration totals replace unavailable API data.

Automated tests are software evidence. They do not verify customer laptop access, real camera compatibility, actual model accuracy or physical speaker audibility.
