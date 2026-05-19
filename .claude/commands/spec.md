When I run /spec <feature-name>:

STEP 1 — Before writing anything, ask me these questions and wait for answers:
- What problem does this feature solve, and who is the user?
- What already exists in the codebase that this builds on or touches?
- What are the inputs and outputs of this feature?
- Any constraints: performance requirements, security concerns, third-party integrations?
- What does done look like — how do we verify it works end to end?
- Anything explicitly out of scope that someone might assume is included?

STEP 2 — After I answer, generate a detailed spec and save it to
docs/specs/<feature-name>.md using this exact structure:

---

# Spec: <Feature Name>

## Goal
One sentence. What problem does this solve and for whom?

## Background
What exists today? What are the limitations that motivated this feature?
What prior decisions constrain the design?

## Scope
### In scope
Explicit list of everything being built.
### Out of scope
Explicit list of things someone might assume are included but are not.
This section is as important as in scope.

## User flow
Step by step, what does the user do and what does the system do in response?
Cover the happy path first, then every edge case and error state.

## Detailed requirements
Numbered list. Every requirement must be:
- Specific (no vague words like "fast" or "good")
- Testable (you can write a test or manually verify it)
- Atomic (one thing per requirement)
Cover: functional requirements, error handling, security, performance,
and logging/observability.

## Data model changes
For every table being created or modified:
- Full table definition with column names, types, nullability, defaults
- All indexes with justification for why each index exists
- Foreign keys with cascade behavior
- Any migrations needed and in what order

## API contracts
For every new or modified endpoint:
- Method and path
- Auth required (yes/no, what role)
- Request headers
- Request body (full schema with types and validation rules)
- Response body (full schema for success and every error case)
- HTTP status codes used and when
- Rate limiting if applicable

## Component and file structure
List every file being created and every file being modified.
For each file: one line on what it does and why it's needed.
Group by: backend, frontend, tests, config.

## External dependencies
Any third-party APIs, libraries, or services this feature depends on.
For each: what it does, what happens if it's unavailable, any rate limits.


## Testing plan
- Unit tests: what functions/modules need tests and what cases to cover
- Integration tests: what end-to-end flows to verify
- Manual verification steps for things that can't be automated

## Observability
- What gets logged and at what level
- What metrics or traces are emitted
- What does a healthy vs unhealthy state look like

## Risks and open questions
- What could go wrong during implementation
- What decisions were deferred and will need revisiting
- What assumptions were made that might be wrong

---
