import { expect, it } from "vitest";
import { connectionStatus } from "./health";

it("removes cached online presence after120 seconds even without another API response", () => {
  const now = Date.parse("2026-09-14T08:00:00Z");
  const device = {
    connection_status: "ONLINE" as const,
    last_seen_at: new Date(now).toISOString(),
  };
  expect(connectionStatus(device, now + 120_000)).toBe("ONLINE");
  expect(connectionStatus(device, now + 120_001)).toBe("STALE_REPORT");
});
it.each([null, "invalid", "2099-01-01T00:00:00Z"])(
  "does not turn missing or invalid timing into confirmed presence: %s",
  (last_seen_at) => {
    expect(
      connectionStatus(
        { connection_status: "ONLINE", last_seen_at },
        Date.parse("2026-09-14T08:00:00Z"),
      ),
    ).toBe("STALE_REPORT");
  },
);
it("never promotes offline/revoked reports because a timestamp is recent", () => {
  const now = Date.now();
  expect(
    connectionStatus(
      {
        connection_status: "REVOKED",
        last_seen_at: new Date(now).toISOString(),
      },
      now,
    ),
  ).toBe("REVOKED");
});
