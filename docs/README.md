# Team documentation

Keep the implementation and the reasons behind it together in version control.

- [MVP roadmap](MVP_ROADMAP.md): the seven agreed improvements, current status and acceptance criteria.
- [Decision log](DECISIONS.md): dated design decisions, rationale, tradeoffs and follow-up work.
- [Change log](CHANGELOG.md): what changed, affected files, verification and limitations for each increment.
- [Case management guide](CASE_MANAGEMENT.md): running, using, testing and backing up improvement 1.

## Working agreement

For every implementation increment, update the roadmap and change log in the same change as the code. Record decisions affecting behavior, data, architecture, dependencies or the team's workflow in the decision log. Include test results and explicitly distinguish completed work from future plans. Use the guide for reproducible manual checks; do not put customer data, credentials or local case databases in Git.

Each teammate should review the relevant decision and change entries before building on an increment. These entries record implementation decisions, not an assertion that every team member has approved them.
