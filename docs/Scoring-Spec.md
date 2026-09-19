# Scoring Spec — Social Nexa Agent

> STATUS: Source-of-truth confirmed. Rules below are transcribed from the
> two spec documents the operator provided ("Local Business Social Media
> Prospecting Agent" and "Professional Local Business Social Media
> Prospecting Agent"), 2026-09-19. Where the spec is qualitative rather
> than numeric, an explicit deterministic rule has been added and is
> labeled `[OPERATIONALIZED]` — flag these for review since Module 4's
> Definition of Done requires exact-match output against mocks, so these
> become the real contract.
>
> **Deliberate deviation from the spec, required by this project's own
> constraints:** the spec asks for scored judgments of image/video quality,
> lighting, composition, and "brand consistency." This project's
> constraints (PRD.md §4/§6) forbid approximating visual/content-quality
> judgment with rules — no AI/LLM calls, ever. So "Visual content quality
> gaps" and "Brand consistency issues" are **not auto-scored**; they are
> emitted as `null` + `needs_manual_review: true` with the reason "requires
> visual judgment," for a human to assess from the linked profile.

## Data collected (feeds every category below)

Per candidate, per the spec: business name, niche, city/locality, IG/FB/
website/Google Maps links, follower count, following count, post count,
last 10–15 posts (date, likes, comments, media type: image/video/reel),
bio text, Story Highlight names, and — for the last **3 eligible posts
only** — likes/comments used for engagement.

Any metric the platform doesn't return or that's private is marked
`"unavailable"` — **never treated as zero**, per spec rule #8 ("Do not
count unavailable metrics as zero").

## Reported metrics (not directly scored, but must appear in output)

### Last-post recency classification (spec's exact buckets)
- `0–7 days`: Recently active
- `8–14 days`: Moderate activity gap
- `15–30 days`: Potential inactivity signal
- `31–90 days`: Strong inactivity signal
- `90+ days`: Highly inactive — flag `verify_still_operating: true`
- No posts at all: treat as `90+ days` bucket, `unavailable_reason: "no posts"`

### Follower tier (context only — spec explicitly says never a hard filter)
- `< 1,000`
- `1,000–3,000`
- `3,000–5,000`
- `> 5,000` — still eligible; spec: "may still be relevant if they have
  clear content, branding or conversion gaps"

Per this project's confirmed PRD.md §5: no hard follower min/max filter is
enforced; the above tiers are reported for context and to weight
prioritization toward under-~5,000, not to exclude anyone.

### Engagement rate (spec's exact formula — reported, not itself a scored category)
For the **last 3 eligible posts** (posts with both likes and comments
visible; skip posts where either is `unavailable` and use the next eligible
post instead):
```
engagement_rate_per_post = (likes + comments) / followers × 100
average_engagement_rate  = mean of the 3 per-post rates
```
- If fewer than 3 eligible posts exist, average over however many are
  eligible and set `engagement_sample_size` accordingly; if zero eligible
  posts, `average_engagement_rate = "unavailable"`.
- If `followers` is `unavailable`, `average_engagement_rate = "unavailable"`.
- 2% is recorded as a **reference annotation only** (`below_reference: true/false`)
  per spec: "Use a 2% engagement rate as a research reference point, not as
  a universal industry benchmark or automatic failure threshold." It does
  not feed any 0–2 score directly.
- Always tag each figure `estimated` vs `directly_observed` per spec rule.

## Scored categories (0–2 each, per spec §6 "Lead Opportunity Scoring")

`0` = no significant gap. `1` = moderate gap. `2` = clear, observable gap.
Total = sum of all 8 (max 16), used only to prioritize research order —
never presented as a sales/quality guarantee, per spec §6 and §9.

### 1. Posting inactivity or inconsistency
Combines last-post recency + consistency across the last 10–15 posts
(posts in last 30 days, posts in last 90 days, gap sizes, burst-then-silence
patterns — spec §4D).
`[OPERATIONALIZED]` exact mapping:
- `0`: last post ≤ 14 days **and** ≥ 3 posts in the last 30 days with no
  gap > 10 days between consecutive posts
- `1`: last post 15–30 days, **or** last post ≤14 days but with an
  irregular pattern (a gap > 10 days somewhere in the last 30 days, or a
  visible burst-then-silence pattern in the last 90 days)
- `2`: last post > 30 days (the spec's 31–90 or 90+ buckets), **or** fewer
  than 3 total posts exist to evaluate consistency

### 2. Profile optimization gaps
Checklist from spec §4E (bio clarity, business category, location,
contact info, website/booking link, WhatsApp/ordering pathway, clear CTA,
organized Highlights, accurate info). Profile picture presence/absence is
mechanically checkable (present or not); its *professionalism* is not
scored here — that visual judgment stays out of this category too.
`[OPERATIONALIZED]` — count missing mechanical elements from: {bio
non-empty, location mentioned, a contact method (phone/email regex or
known booking-link domain), a CTA keyword present, ≥1 Highlight present}:
- `0`: 0–1 missing
- `1`: 2–3 missing
- `2`: 4–5 missing

### 3. Visual content quality gaps — **NOT auto-scored**
Per the deviation noted at the top: output `null`, `needs_manual_review: true`,
`reason: "requires visual judgment (lighting/composition/image quality)"`.

### 4. Limited Reels or video content
Spec §4G: Reels vs static ratio.
`reels_ratio = video_or_reel_posts / total_posts` over the collected window.
`[OPERATIONALIZED]` thresholds (carried from prior draft, since spec gives
no exact cutoff — flagged for review):
- `0`: ratio ≥ 0.4
- `1`: 0.15–0.4
- `2`: ratio < 0.15, or `total_posts == 0`

### 5. Niche-specific content gaps
Apply only the mechanically-checkable items from the matching niche
section (spec §5) — presence/absence via bio + Highlight-name + caption
keyword regex, never visual assessment of the content itself:

| Niche | Mechanical checks (regex/presence) | Explicitly out (visual → manual review) |
|---|---|---|
| Cafes/bakeries/home-food | Menu Highlight or menu link present; ordering/delivery keyword present; location tag/mention present | Food photography quality, styling |
| Tutors/coaching | Subjects/grade/board mentioned in bio; enrollment/contact info present; ≥1 educational-content caption keyword (e.g. "tips", "exam", "syllabus") in recent captions | Testimonial authenticity, video teaching quality |
| Restaurants | Menu/ordering link present; location + hours present; reservation/contact info present | Food/ambience photography quality |
| Salons/spas/beauty | Booking CTA / DM-to-book / WhatsApp link present; services listed; hours present | Before/after transformation quality, staff video quality |
| Boutiques/clothing/jewelry | Highlights include sizing/pricing/delivery-named sections; product/catalog link present | Product photography quality, styling Reels quality |
| Clinics/doctors/dentists/vets | Appointment/contact pathway present; services listed; hours present | Educational content accuracy/quality, team photo quality |

`[OPERATIONALIZED]` scoring — count missing mechanical checks for the
matched niche (out of the niche's listed items):
- `0`: all present
- `1`: exactly one missing
- `2`: two or more missing

### 6. Missing conversion information
Spec: booking/ordering/contact/menu/sizing pathway missing.
`[OPERATIONALIZED]` — checks a fixed set {phone or email regex, booking/
ordering link or keyword, address/location present}:
- `0`: all 3 present
- `1`: 2 of 3 present
- `2`: 0–1 of 3 present

### 7. Limited visible social proof
Spec §4G/§5 (testimonials, reviews, customer-generated content). Presence
of a review/testimonial signal is mechanical (a Highlight named
"Reviews"/"Testimonials", or a caption/bio mention of Google reviews);
**judging whether testimonials are authentic or compelling is visual/
content judgment and is excluded** — only presence is checked.
`[OPERATIONALIZED]`:
- `0`: a reviews/testimonials Highlight or explicit review-count mention exists
- `1`: no dedicated Highlight/mention, but ≥1 recent caption tags/mentions a customer
- `2`: neither present

### 8. Brand consistency issues — **NOT auto-scored**
Per the deviation noted at the top: output `null`, `needs_manual_review: true`,
`reason: "requires visual judgment (brand/visual consistency)"`.

## Output contract (feeds Module 5)

Per spec §7/§8: business info block, profile metrics block (including
engagement + recency + follower tier), weak-signals list (plain-language,
e.g. "Last post was 42 days ago", "No booking link in bio"), niche-specific
observations, one-line opportunity note, the 8 category scores (2 of which
may be `null`/manual-review), total score (sum of the 6 numeric
categories only — the two manual-review categories are excluded from the
numeric total and called out separately), data-quality block (research
date, sources checked, unavailable fields, estimated-vs-observed flags),
and a `flags` list (`needs_manual_review`, `verify_still_operating`, etc).

A summary table (rank | business | niche | location | followers | last
post | engagement | key gap | score) precedes the detailed per-prospect
records, per spec §8 — explicitly labeled "research priority order," not a
quality ranking, per spec §6/§9.

## Hard rules carried through unchanged (spec §9 / §10)

No fabrication of businesses, links, figures, or testimonials. No
assuming inactivity without checking Stories/other channels. No treating
low followers as poor performance. No labeling a business "bad." No
guaranteed-results language anywhere in output. No outreach messages sent
by the system itself. No duplicate businesses. Respect platform terms.
Protect any personal/patient information encountered. Flag uncertain
findings for manual review rather than guessing.
