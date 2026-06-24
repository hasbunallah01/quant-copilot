# Security

## Reporting a vulnerability

**Do not file a public issue for security bugs.**

Email the maintainer at the address listed on the GitHub profile of
[@hasbunallah01](https://github.com/hasbunallah01), or use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability).

Please include:

* A clear description and impact
* Reproduction steps
* The affected commit / version

You can expect a first response within 72 hours.

## What we care about most

* Anything that lets an attacker execute trades without going through
  the `RiskEngine`.
* Anything that leaks API keys (we use the official Bitget HMAC signing,
  never inline secret strings).
* Anything that lets a malicious log file trigger arbitrary code via
  the watcher / dashboard.

## Design choices that reduce blast radius

* The `RiskEngine` is the only path that calls `BitgetClient.place_order`.
  Even if a strategy is buggy, it cannot bypass the engine.
* `BitgetClient` never logs credentials. Headers are passed through
  `requests.Session.request` without ever being stored.
* The log parser (`parse_log_line`) is **regex-only** — no `eval`, no
  deserialization, no `subprocess`. A malformed line cannot escape
  the parser.
* The `.gitignore` excludes `.env`, `.env.local`, and every `*.local`
  variant so accidentally-created secret files do not get committed.
* The CI workflow runs in GitHub-hosted runners with no secrets; PR
  builds can never leak `BITGET_API_KEY` etc. even if a contributor
  pushes them locally.

## Bitget API keys

For local development:

```bash
export BITGET_API_KEY=...
export BITGET_API_SECRET=...
export BITGET_PASSPHRASE=...
```

`BitgetCredentials.from_env()` reads them. Never check them into git.
If you accidentally do, **revoke the key immediately** at
https://www.bitget.com and rotate.