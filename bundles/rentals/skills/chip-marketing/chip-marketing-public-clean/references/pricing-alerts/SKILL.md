# Pricing Alerts

Automated scenarios with triggers and SLA.

## Competitor Pricing Monitor

### Trigger: Price Change
```yaml
alert:
  name: competitor_price_change
  trigger:
    type: price_delta
    competitors: [competitor_a, competitor_b]
    threshold: any_change
  action:
    - notify_slack: "#pricing-alerts"
    - create_task: "Review pricing strategy"
    - draft_response: "Competitive positioning memo"
  sla: 15_minutes
```

### Trigger: New Tier
```yaml
alert:
  name: competitor_new_tier
  trigger:
    type: new_plan
    competitors: all
  action:
    - notify_email: pricing@company.com
    - analyze: "Gap analysis vs our tiers"
    - suggest: "Response options"
  sla: 1_hour
```

## Own Pricing Guardrails

### Alert: Discount Request Spike
```yaml
alert:
  name: discount_request_spike
  trigger:
    type: metric_threshold
    metric: discount_requests_per_day
    threshold: "> 5"
  action:
    - notify: sales_lead
    - analyze: "Common objections"
    - suggest: "Pricing or packaging changes"
  sla: 4_hours
```

### Alert: Churn Due to Pricing
```yaml
alert:
  name: pricing_churn
  trigger:
    type: churn_reason
    reason: "too_expensive"
    threshold: "> 3 in 7 days"
  action:
    - notify: product_team
    - create_survey: "Price sensitivity"
    - analyze: "Segment impact"
  sla: 24_hours
```

## Market Pricing Intelligence

### Benchmark Report
```python
class PricingBenchmark:
    def generate(self, competitors: List[Competitor]) -> Report:
        return {
            "our_position": self.percentile_rank(competitors),
            "price_per_seat": self.compare_metric("seat_price", competitors),
            "price_per_feature": self.compare_metric("feature_price", competitors),
            "trends": self.detect_trends(competitors),
            "recommendations": self.suggest_changes(competitors)
        }
```

## Usage

```bash
# Add competitor
/pricing monitor add https://competitor.com/pricing

# Check status
/pricing status

# Generate benchmark
/pricing benchmark --competitors all --output report.md

# Set alert
/pricing alert --trigger "price_change > 10%" --notify slack
```
