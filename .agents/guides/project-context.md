# Project context

Discord Scribe turns opted-in team messages into traceable, reviewed context for
coding assistants. SQLite is authoritative; Markdown is a derived snapshot.
One configuration represents one project and guild. Hosted LLMs are the default;
local models are optional. Use a provider adapter instead of coupling domain logic
to a vendor. Credentials stay in environment variables.

Acceptance criteria: explicit channel/author scope; durable bounded retries;
strict output validation and exact source quotes; explicit approval before export;
idempotent replay; edit invalidation and deletion during inference; credential-free
demo and regression tests. Proposals must remain distinguishable from decisions.

Layers: domain contracts, persistence, extraction, orchestration, Discord adapter,
and operator CLI. Keep credentials and private context out of repository history.
Local work and verification are authorized; Git delivery remains user-managed.
