# Case-study template / 案例模板

This directory is for consented, redacted outcome evidence. It is not a testimonials folder and must not contain invented examples presented as real users.

Create one Markdown file per case with the following fields:

```markdown
# Case title

- Evidence class: maintainer self-test / independent individual / independent organization
- Author or organization: public identity, approved pseudonym, or “anonymous with maintainer-held consent”
- Consent recorded: yes/no and date
- Environment: OS family, architecture, VSG version; no hostname or local path
- Observation window: start/end dates

## Baseline problem
What decision could not be made before VSG? Include a measurable baseline where possible.

## VSG evidence used
Which attribution, stop assessment, verification, runtime, capacity, or impact-report evidence changed the decision?

## Action and outcome
What did the user decide? What happened afterward? Separate measured fact from interpretation.

## Measures
- candidate services reviewed:
- confirmed stale / not stale / uncertain:
- stops attempted and verified:
- relaunches detected:
- prediction-error samples, if relevant:
- time or incident reduction, including the measurement method:

## Counterfactuals and limitations
What else could explain the outcome? What was not visible or not tested?

## Reproduction evidence
Link to a redacted impact export digest, public issue, or reproducible steps. Never attach the local database or raw logs.
```

Before merging a case, verify that claims match the evidence, permission covers publication, and no personal or machine-identifying data remains.
