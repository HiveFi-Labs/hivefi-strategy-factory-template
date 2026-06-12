# LINEAR_API_KEY Setup

Symphony uses Linear's GraphQL API to poll issues, post result comments, and
move issues through the workflow. `LINEAR_API_KEY` should be a Linear personal
API key for the user account that owns those actions.

Sources:

- Linear Docs: https://linear.app/docs/api-and-webhooks
- Linear Developers: https://linear.app/developers/graphql

## Create The Key

1. Open Linear in the browser.
2. Go to `Settings > Account > Security & Access`.
3. In the API keys area, create a personal API key.
4. Name it clearly, for example `hivefi-strategy-factory-symphony`.
5. Prefer a restricted key scoped to the Linear team or workspace area used by
   `HiveFi Strategy Factory`.
6. Grant enough permissions for Symphony:
   - `Read`: poll projects, issues, states, teams, and comments.
   - `Write`: update issue state and metadata when the workflow advances.
   - `Create comments`: post `## 結果` comments.
   - `Create issues`: only needed when using scripts or automation to create
     Linear tickets from `STRATEGY_BATCH_TEMPLATE.md`.
7. Copy the key once. Do not commit it.

If the UI does not allow personal API key creation, ask a Linear admin to enable
member API keys under `Settings > Administration > API > Member API keys`, or
ask an admin to create/run the workflow. Linear admins can always create API
keys, and existing workspace keys can be reviewed or revoked from the same
administration area.

## Export Locally

Keep the key in your shell environment or local `.env`. `.env` is ignored by
git; `.env.example` must only contain placeholders.

```bash
export LINEAR_API_KEY="lin_api_xxx"
```

When using this repo's normal environment load:

```bash
cd /path/to/hivefi-strategy-factory
set -a
. ./.env
set +a
```

## Verify The Key

Linear personal API keys authenticate directly in the `Authorization` header.
Do not prefix them with `Bearer`; OAuth tokens use `Bearer`, personal API keys
do not.

```bash
curl -sS https://api.linear.app/graphql \
  -H "Content-Type: application/json" \
  -H "Authorization: ${LINEAR_API_KEY}" \
  --data '{"query":"query { viewer { id name } }"}'
```

The response should contain a `viewer` object and no top-level `errors` array.
If it fails:

- `401` or authentication errors: the key is missing, revoked, copied
  incorrectly, or was sent with the wrong header format.
- Permission errors while Symphony runs: add the missing permission, expand the
  allowed team scope, or use a full-access personal key for the dedicated
  workflow account.
- No issue activity: confirm the key's user can see the `HiveFi Strategy
  Factory` project and the target issues in Linear.

## Start Symphony

After `LINEAR_API_KEY`, `HIVEFI_API_KEY`, and `CLICKHOUSE_*` are exported:

```bash
cd ~/symphony/elixir
mise exec -- ./bin/symphony /path/to/hivefi-strategy-factory/WORKFLOW.md --port 4000 \
  --i-understand-that-this-will-be-running-without-the-usual-guardrails
```
