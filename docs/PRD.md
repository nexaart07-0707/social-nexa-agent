# PRD — Social Nexa Agent

> STATUS: DRAFT — project data confirmed by human 2026-09-19. Still blocked
> on the real Scoring-Spec.md (source spec docs not yet pasted into this
> session — see Scoring-Spec.md status note).

## 1. Problem / Goal

Identify local businesses on Instagram within a target city/niche set that show
clear, *mechanically detectable* signs of weak social media presence (stale
posting, low engagement, thin bios, no Highlights, etc.), so a human can
follow up with them as sales/outreach leads. The system runs unattended on a
schedule, produces a lead list with a rule-based "opportunity score," and
notifies a human via email. No AI/LLM judgment is used anywhere in the
scoring path — every rule is regex/math/date-comparison based, matching the
Zero-AI-in-runtime constraint.

## 2. Users

- Single operator (you) — receives the email report, reviews the CSV,
  manually does outreach. No multi-user auth, no web UI.

## 3. In Scope

- Collecting public Instagram data anonymously, with no login/session (Instagram login was removed; see constraint 3 below).
- Discovering candidate business accounts in a target city/niche via
  OpenStreetMap/Nominatim and/or public Google Maps scraping (Module 2).
- Collecting public profile + recent-post data for each candidate via an
  anonymous (logged-out) Instagram session (Module 3).
- Pre-scoring exclusion filters, applied between collection and scoring, so
  obviously-wrong candidates never reach a human's inbox:
  - **Follower cap**: candidates with more than 15,000 followers are
    excluded (too large to be a plausible weak-presence lead). A candidate
    whose follower count couldn't be collected is never excluded on this
    rule — "couldn't check" is not the same as "fails the check."
  - **Posting frequency floor**: candidates with fewer than 2 posts in the
    trailing 30 days are excluded as not a meaningfully active account to
    show opportunity gaps against. Again, missing/uncollectable post dates
    never trigger exclusion — only a confirmed low count does.
  - **Brand blocklist**: candidates matching a known major chain/franchise
    (e.g. Starbucks, McDonald's, Domino's — see
    `data/brand_blocklist.json`) are excluded; these are not local
    businesses needing outreach. The list is manually curated and
    editable (see `README.md`) — it only catches brands actually in the
    list, by design, not an algorithmic "is this famous" detection.
  - **Persistent already-sent dedup**: a business whose handle has already
    appeared in a previous day's successfully-sent email report is never
    re-sent, so the same lead isn't repeatedly surfaced run after run. This
    history persists across runs (see `docs/Architecture.md` §5 for how).
- Scoring each candidate against a fixed, documented rule set (Module 4) —
  see `Scoring-Spec.md`.
- Writing results to a timestamped CSV and notifying via email (Module 5).
- Running on a GitHub Actions schedule (1PM/5PM/8PM IST) with per-window
  min/max result enforcement (Module 6).

## 4. Out of Scope

- Any paid API/service (Instagram Graph API business verification, paid
  Maps API tiers, paid proxy/anti-detection services).
- Any LLM/AI call in the scoring or filtering path.
- Automated OTP/2FA handling or anything that disguises automation as human
  traffic — on session expiry or a verification challenge, the system halts
  and notifies a human instead.
- Visual or content-quality judgment (e.g. "is this photo good?") — anything
  requiring that judgment is left blank in the output and flagged
  `needs manual review`.
- Multi-account Instagram rotation, multi-user support, web dashboard.

## 5. Project Data (confirmed)

| Field | Value |
|---|---|
| Niches to target | All 6: cafes/bakeries/home-food, tutors/coaching, restaurants, salons/spas/beauty, boutiques/clothing/jewelry, clinics/doctors/dentists/vets |
| Follower count range | No hard min/max filter enforced. Scoring/prioritization favors accounts under ~5,000 followers, per spec's general guidance, but does not exclude accounts above that. |
| Run 1 window | 1:00 PM–5:00 PM IST. Minimum 2 qualifying results. **1–2 of the qualifying results MUST be from Hubli, Karnataka** (search radius: within Hubli city limits); any remaining results beyond that come from a true nationwide search (any city/state in India), best-fit by score. |
| Run 2 window | 5:00 PM–7:00 PM IST. Minimum 2, maximum 3 qualifying results. **No location restriction** — true nationwide search, purely results/score-based. |

All timings are IST. GitHub Actions cron is always UTC — Module 6
(`scheduler_rules.py`) and the workflow YAML convert explicitly (see
`docs/Architecture.md` §5), never assuming IST==UTC.

## 6. Constraints (hard, non-negotiable)

1. Zero budget — every library/service used must be free at every tier, forever.
2. No AI/LLM calls anywhere in runtime filtering/scoring logic.
3. Instagram (CHANGED, supersedes the original one-time-login plan):
   login/session persistence was removed entirely — collection is always
   anonymous/unauthenticated via instaloader, with responsible pacing (not
   evasion engineering). No credentials, no session file, no expiry/
   challenge handling needed; the tradeoff is more frequent `"unavailable"`
   fields due to Instagram's anonymous-access rate limiting (expected, not
   a failure — see `collector.py`'s module docstring and `README.md`'s
   known limitations).
4. Deployment target (CHANGED 2026-09-19, supersedes the original VM+cron
   decision): **GitHub Actions on a PUBLIC repository**, using its free
   scheduled-workflow cron triggers instead of a VM's crontab. A public
   repo is required for unlimited free Actions minutes with no card/cap.
   All secrets (Gmail) live in GitHub Actions
   repository secrets — never committed to the repo, which is public.
5. Anything requiring visual/content-quality judgment → leave blank, flag
   `needs manual review`. Never approximate with a rule.

## 7. Success Criteria (product-level)

- End-to-end pipeline runs unattended twice a day, produces a correctly
  structured CSV of scored leads, and a human is notified via email with
  a summary, without the process crashing on any single external-call failure.
- Every score is reproducible and traceable to a specific rule in
  `Scoring-Spec.md` — no opaque or approximated values.
