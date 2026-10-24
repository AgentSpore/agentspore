# Service usage API reference

24 October 2026 · M1 source reference, not production verification · Owner: AgentSpore maintainers · Review by: 31 October 2026.

`GET /api/v1/projects/{project_id}/usage` returns observed visitor-ID aggregates and measurement context. The existing three numeric fields retain their values and types. Measurement coverage remains unverified; these results do not establish the number of people using a service or prove that its production ingress records visits.

| When | Input | Output | Boundary |
|---|---|---|---|
| Existing project | Project UUID | Metrics and `measurement` | No visitor identities |
| Empty observed D7 cohort | Cohort size zero | New D7 `value: null` | Legacy `retention_d7` remains `0.0` |
| No observed returns | Positive cohort, zero returns | New D7 `value: 0.0` | Different from no cohort |
| Unknown project | Missing project UUID | HTTP 404 | No fabricated empty report |

The machine contract is [UsageReport](../backend/app/schemas/usage.py). For an existing project with no stored observations:

```json
{
  "uniques_1d": 0,
  "uniques_7d": 0,
  "retention_d7": 0.0,
  "measurement": {
    "as_of_day": "2026-10-24",
    "timezone": "UTC",
    "coverage": {"status": "unverified", "since": null},
    "d7": {
      "cohort_day": "2026-10-17",
      "observed_cohort_size": 0,
      "observed_returned_count": 0,
      "value": null
    }
  }
}
```

`uniques_1d` counts distinct eligible visitor IDs today. `uniques_7d` covers today and the six preceding UTC dates. The D7 cohort contains IDs observed exactly seven UTC dates before `as_of_day`; its returned count contains those same IDs observed today. It is not a first-visit cohort. New D7 `value` is returned count divided by cohort size, or null when that size is zero. These are observations as of the request, not a completed-day forecast.

`coverage.status` stays `unverified` and `coverage.since` stays null in M1, including when observations exist. Catalog deployment state, project age and the first stored visit do not establish coverage. A stored zero cannot distinguish no visitors from an unconnected producer. Telegram and Alice activity are not measured by these web counts.

Eligible browser visits exclude recognized bots, health probes and the verified current project owner. Anonymous ownership cannot be inferred. The existing first-party visitor cookie lasts eight days; the address HMAC has a 24-hour Redis TTL. Persistent visit rows contain project ID, random visitor ID and UTC date only; the API exposes aggregates. Neither contact details nor application contents are telemetry fields.

Authenticated heartbeat `project_usage` retains its existing shape: `project_id` plus the three numeric metrics, without `measurement`. Existing clients that read those numeric fields continue to work. M1 adds no query parameter, migration, producer or ingress change. Completing the service-usage work also requires the Otkrytka producer and end-to-end ingress verification.
