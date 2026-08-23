# Hook Frameworks

Templates with variables: ICP, pain, proof, CTA.

## Framework: PAS (Pain-Agitate-Solve)

### Template
```
{icp} struggle with {pain_point}.

It gets worse: {agitate_consequence}.

Here's the fix: {solution}.

{proof}.

{cta}
```

### Example
```
Solo founders struggle with cold outreach.

It gets worse: every day without outreach is revenue left on the table.

Here's the fix: personalized sequences that book meetings while you sleep.

Result: 47 meetings booked in 30 days.

Start free →
```

## Framework: AIDA (Attention-Interest-Desire-Action)

### Template
```
{attention_grabber}

{interest_builder}

{desire_creator}

{cta}
```

### Example
```
Your competitor just raised $10M.

Here's what they're not telling you: their growth came from one channel.

You can replicate it without the funding.

See the playbook →
```

## Framework: Before-After-Bridge

### Template
```
Before: {painful_state}

After: {desired_state}

Bridge: {how_to_get_there}

{cta}
```

### Example
```
Before: Spending 4 hours/day on manual outreach.

After: 50 personalized emails sent while you sleep.

Bridge: AI-powered outreach automation.

Try it free →
```

## Framework: Feature-Benefit-Outcome

### Template
```
{feature} → {benefit} → {outcome}

{proof}

{cta}
```

### Example
```
Auto-scheduling → No back-and-forth → 10 hours saved/week.

Used by 1,000+ founders.

Start saving time →
```

## Framework: Question-Tease-Answer

### Template
```
{provocative_question}?

{tease_that_hints_at_answer}

{answer_that_leads_to_product}

{cta}
```

### Example
```
Why do 90% of cold emails fail?

Hint: It's not your offer.

It's your opening line. Here's the 3-word fix.

Get the template →
```

## Variable Definitions

| Variable | Description | Example |
|----------|-------------|---------|
| `{icp}` | Ideal customer profile | "SaaS founders" |
| `{pain_point}` | Specific pain | "no time for outreach" |
| `{agitate_consequence}` | What happens if ignored | "competitors steal your deals" |
| `{solution}` | Your solution | "automated personalization" |
| `{proof}` | Social proof | "47 meetings in 30 days" |
| `{cta}` | Call to action | "Book a demo →" |

## Usage

```bash
# Generate hook
/hook generate --framework PAS --vars "icp=SaaS founders,pain_point=no time for outreach"

# List frameworks
/hook list

# Add custom framework
/hook add --name "My Framework" --template "..."
```
