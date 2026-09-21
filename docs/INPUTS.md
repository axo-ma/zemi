# ZEMI inputs

Inputs belong to the universal System → Component → Playbook core and are not
an Arsenal feature. The canonical wrapper is:

```toml
token = { input = { prompt = "Token", type = "string", env = "API_TOKEN", validate = "non_empty", secret = true } }
```

- no `env`: ephemeral value for the current job;
- `env = "NAME"`: persist and reuse `NAME` in `@inst/_inputs/values.env`;
- `validate`: validate only; it does not imply persistence;
- `secret = true`: hidden entry and `***` masking in reports only.

Thus ephemeral visible, persistent visible, ephemeral secret, and persistent
secret inputs are all supported. Persistent values are never copied to or read
from `os.environ`. The UTF-8 store uses locking, atomic replacement, and
best-effort restrictive Windows ACLs. On first lookup, a missing key may be
migrated from the legacy `@inst/_secrets/arsenal.env` store and is immediately
written to the neutral store.
