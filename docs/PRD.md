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
notifies a human via Telegram. No AI/LLM judgment is used anywhere in the
scoring path — every rule is regex/math/date-comparison based, matching the
Zero-AI-in-runtime constraint.

## 2. Users

- Single operator (you) — receives Telegram notifications, reviews the CSV,
  manually does outreach. No multi-user auth, no web UI.

## 3. In Scope

- One-time manual Instagram login; session reused across runs (Module 1).
- Discovering candidate business accounts in a target city/niche via
  OpenStreetMap/Nominatim and/or public Google Maps scraping (Module 2).
- Collecting public profile + recent-post data for each candidate via a
  persisted Instagram session (Module 3).
- Scoring each candidate against a fixed, documented rule set (Module 4) —
  see `Scoring-Spec.md`.
- Writing results to a timestamped CSV and notifying via Telegram (Module 5).
- Running twice daily on a cron schedule with per-window min/max result
  enforcement (Module 6).

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

All timings are IST. Module 6 (Scheduler) must explicitly verify/convert
the deployment VM's local timezone against IST — never assume the server
defaults to IST or UTC without checking.

## 6. Constraints (hard, non-negotiable)

1. Zero budget — every library/service used must be free at every tier, forever.
2. No AI/LLM calls anywhere in runtime filtering/scoring logic.
3. Instagram: one manual login only; session persisted via cookies; graceful
   halt + human notification on expiry or challenge; standard instaloader
   session-reuse pattern; responsible pacing (not evasion engineering).
4. Deployment target (CHANGED 2026-09-19, supersedes the original VM+cron
   decision): **GitHub Actions on a PUBLIC repository**, using its free
   scheduled-workflow cron triggers instead of a VM's crontab. A public
   repo is required for unlimited free Actions minutes with no card/cap.
   All secrets (Instagram session, Telegram, Gmail) live in GitHub Actions
   repository secrets — never committed to the repo, which is public.
5. Anything requiring visual/content-quality judgment → leave blank, flag
   `needs manual review`. Never approximate with a rule.

## 7. Success Criteria (product-level)

- End-to-end pipeline runs unattended twice a day, produces a correctly
  structured CSV of scored leads, and a human is notified via Telegram with
  a summary, without the process crashing on any single external-call failure.
- Every score is reproducible and traceable to a specific rule in
  `Scoring-Spec.md` — no opaque or approximated values.
