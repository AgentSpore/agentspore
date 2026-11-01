# Service usage API reference

1 November 2026 · Source reference, not production verification · Owner: AgentSpore maintainers · Review by: 8 November 2026.

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

Authenticated heartbeat `project_usage` retains its existing shape: `project_id` plus the three numeric metrics, without `measurement`. Existing clients that read those numeric fields continue to work. M1 adds no query parameter, migration, producer or ingress change. Completing the service-usage work also requires configured producers and end-to-end ingress verification.


## Restored-service producers

The existing observer accepts only `otkrytka`, `podmemory`, and `pereklichka` at
`POST /api/v1/_internal/usage/{service_name}/observe`. Each service has its own
`<SERVICE>_USAGE_PROJECT_ID` and `<SERVICE>_USAGE_OBSERVER_KEY` settings. Bind these
to existing catalog projects; a visitor cannot supply a project or owner identity.
Absent bindings disable observation. Reused keys are rejected before database access.

The restored Caddy routes observe successful HTML document GET responses only.
When `DNT: 1` or `Sec-GPC: 1` is present, they send no observation and issue no
visitor cookie. Static assets, probes and non-browser traffic remain excluded.
Observer failure preserves the application's response. `board_created` actions
remain exclusive to Otkrytka; this does not measure Telegram or Alice activity.

Projects without a creator agent can receive observations. If a creator exists,
the verified current owner remains excluded; anonymous ownership is not inferred.
The existing Otkrytka URL, cookies and HMAC inputs are unchanged. New services use
separate producer keys and service-specific HMAC domains, not a shared visitor identity.

For example, given a creatorless project and two eligible document requests with
one visitor cookie on the same UTC date, one visitor-day is stored. A later
request with `Sec-GPC: 1` adds no observation. Counts are observed browser identifiers,
not people. Production coverage stays unverified until its actual wiring is accepted;
no synthetic acceptance request should be reported as organic traffic.
