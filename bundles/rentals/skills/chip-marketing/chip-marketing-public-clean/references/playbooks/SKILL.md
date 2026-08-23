# Marketing Playbooks

Ready-to-run playbooks for launches, outreach, and partnerships.

## Product Hunt Launch Day

### Pre-Launch (T-7 days)
- [ ] Prepare assets: logo (240x240), gallery (min 3), maker avatars
- [ ] Write tagline (60 chars max)
- [ ] Draft first comment (story + CTA)
- [ ] Schedule hunter post (if using hunter)
- [ ] Warm up audience: X/Twitter thread preview

### Launch Day (00:00 PST)
- [ ] Post goes live at 00:01 PST
- [ ] First comment within 5 minutes
- [ ] Reply to every comment within 15 minutes (first 4 hours critical)
- [ ] X/Twitter thread at 09:00 PST
- [ ] LinkedIn post at 10:00 PST
- [ ] Email list at 11:00 PST
- [ ] Hacker News Show HN at 14:00 PST

### Hour-by-Hour (PST)
| Time | Action |
|------|--------|
| 00:00 | Go live, first comment |
| 00-04 | Rapid response mode (all comments) |
| 06:00 | Morning push (X/Twitter, LinkedIn) |
| 09:00 | Email blast to list |
| 12:00 | Midday check-in, respond to overnight |
| 15:00 | Afternoon push (communities, Slack) |
| 18:00 | Evening wind-down |
| 21:00 | Final check, schedule tomorrow |

### Post-Launch
- [ ] Thank all commenters individually
- [ ] Add to "Shipped" section of website
- [ ] Write launch retrospective
- [ ] Add PH badge to site

## Cold Outreach Cadence

### Day 0: Initial
```
Subject: {company} + quick win

Hi {name},

{specific_observation_from_recent_post}.

We helped {similar_company} {specific_result}.

Worth a 10-min call to see if relevant?

{signature}
```

### Day 3: Value Add
```
Hi {name},

Quick follow-up. Thought you'd find this useful:
{relevant_resource_link}

Still interested in exploring {outcome}?
```

### Day 7: Different Angle
```
Hi {name},

Different angle: {alternative_value_prop}.

{social_proof_specific_to_their_industry}

Worth a brief chat?
```

### Day 14: Breakup
```
Hi {name},

Tried a few times — don't want to be that person.

If {outcome} becomes a priority: {calendar_link}

{signature}
```

## Partnership Outreach

### Template
```
Subject: {their_company} + {our_company} — {partnership_type}

Hi {name},

Saw your {content_type} about {topic}. {genuine_insight}.

We're {what_we_do} and noticed overlap with your audience.

Idea: {specific_partnership_proposal}

Examples:
- {example_1}
- {example_2}

Worth exploring?

{signature}
```

### Partnership Types
| Type | Pitch | Example |
|------|-------|---------|
| Co-marketing | Joint content | Co-authored report |
| Integration | Technical connect | API partnership |
| Affiliate | Revenue share | 20% recurring |
| Bundle | Joint offering | "Startup stack" |
| Event | Joint webinar | Workshop series |

## Usage

```bash
# Load playbook
/playbook load product_hunt_launch

# Run checklist
/playbook run --playbook product_hunt_launch --date 2026-03-15

# Track progress
/playbook status --playbook product_hunt_launch
```
