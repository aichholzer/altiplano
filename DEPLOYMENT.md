# Deploying Altiplano as a shared HTTP service

`altiplano-http` on an always-on host. Every client reaches one process, each acting as
its own Vikunja user.

[`README.md`](./README.md) covers the transport, the environment variables, the client
tokens, and pointing a client at the endpoint, under `Use over HTTP`.

You need a Vikunja instance the host can reach, and either Docker or
[`uv`](https://docs.astral.sh/uv/getting-started/installation/).

## Docker

Alpine image with both commands: `altiplano-http` serves, and `altiplano-clientkey`
registers the clients allowed to call it.

### Configure

```bash
git clone https://github.com/aichholzer/altiplano.git
cd altiplano
cp .env.example .env
```

Edit `.env`:

```ini
VIKUNJA_URL=https://vikunja.example.com/api/v2
ALTIPLANO_HTTP_PORT=8000
ALTIPLANO_HTTP_ALLOWED_HOSTS=altiplano.home.arpa,altiplano.home.arpa:*,10.0.0.5,10.0.0.5:*
```

Vikunja API tokens do not belong in `.env`, each client's token goes into the store in the next
step. `.env` holds no secret at all.

`ALTIPLANO_HTTP_PORT` drives both sides of the port mapping. Altiplano binds it inside
the container and Docker publishes the same number on the host, and several containers
on one host differ only by this line. Stay above 1024: the container runs unprivileged.

`ALTIPLANO_HTTP_ALLOWED_HOSTS` has to name every address clients will use, and setting
it replaces the loopback defaults outright. List both forms of each name: `host:*`
matches a `Host` header with a port, a bare `host` matches one without, and neither
covers the other. A missing value produces a `421`.

### Register a client, then start

A container binds every interface, which Altiplano treats as reachable, and it refuses
to start with an empty store:

```bash
docker compose run --rm altiplano altiplano-clientkey add my-laptop
```

That prompts for the Vikunja API token the client acts as, writes the store to the
volume, and prints the client token once.

Then start it:

```bash
docker compose up -d
docker compose logs -f altiplano
```

### Day to day operations

```bash
docker compose run --rm altiplano altiplano-clientkey list
docker compose exec altiplano altiplano-clientkey add partner-laptop
docker compose exec altiplano altiplano-clientkey revoke old-laptop
docker compose exec altiplano altiplano-http --check
```

`exec` reaches the running container and `run --rm` starts a throwaway one. Either
writes the same volume, and a client added or revoked takes effect on the next request
with no restart.

### What persists

`/var/lib/altiplano`, holding the `clients` store. **Back this one up.** Losing it
means re-registering every client. `clients.lock` and any `.clients-*` beside it are
disposable.

```bash
docker compose down            # keeps the volume
docker compose down -v         # deletes it, and every client token with it
```

### Surviving a reboot

`restart: unless-stopped` brings the container back after a crash, a daemon restart, or
a reboot of the host. It rests on the Docker daemon starting at boot, which is the
usual arrangement and takes one command to confirm:

```bash
rc-update show default | grep docker   # OpenRC, on Alpine
systemctl is-enabled docker            # systemd
```

Nothing back from either means the daemon is not enabled, and neither is Altiplano.

`unless-stopped` also means what it says. A container you stopped with
`docker compose stop` stays stopped through a reboot. Docker records that the stop was
deliberate. `restart: always` brings it back regardless, at the cost of a stop that
sticks.

`docker compose down` removes the container, which leaves no restart policy to act on.
Bringing it back needs another `up -d`.

### Healthcheck

It opens a TCP connection to the port. Every caller without a registered token gets a
`401`, which leaves no request a healthcheck could read a `200` from.

Vikunja being unreachable therefore leaves the container healthy and surfaces as a
failed tool call. A healthcheck that reached Vikunja would restart Altiplano for an
outage elsewhere.

### Pinning

`ALPINE_VERSION` defaults to `latest`. Pin it in `.env` for a build you can reproduce:

```ini
ALPINE_VERSION=3.22
```

## Run with uv

No clone. `--with altiplano` resolves the package from PyPI, and `--env-file` puts
every setting in the environment before the process starts.

Write `.env` next to where you run the commands:

```dotenv
VIKUNJA_URL=https://vikunja.home.arpa/api/v2
ALTIPLANO_HTTP_HOST=0.0.0.0
ALTIPLANO_HTTP_PORT=8000
ALTIPLANO_HTTP_ALLOWED_HOSTS=altiplano.home.arpa,altiplano.home.arpa:*,10.0.0.5,10.0.0.5:*
```

Register a client, then serve:

```bash
uv run --no-project --env-file .env --with altiplano altiplano-clientkey add my-laptop
uv run --no-project --env-file .env --with altiplano altiplano-http
```

- `--env-file` is not read by default. Without it `.env` is ignored and every setting
  falls back to its default.
- Altiplano needs Python 3.10 or newer. Add `--python 3.13` when the default
  interpreter is older, and uv fetches one.
- The store defaults to `~/.config/altiplano/clients`, under the account running the
  commands. Set `ALTIPLANO_CLIENTS` in `.env` to put it elsewhere, and both commands
  then agree on the path.
- Run as a dedicated account. The client store beside `.env` holds every client's
  Vikunja token in plaintext, and Altiplano writes it `chmod 600`.
- `--with altiplano` resolves the newest release on each run. Pin it,
  `--with altiplano==2.0.1`, for a service that restarts on its own.

To start on boot, wrap the serve line in whatever the host uses for services. On
systemd that is a unit with `ExecStart` set to the full `uv run` command and
`WorkingDirectory` set to the directory holding `.env`.

## Configure

Every setting the HTTP transport reads, with its default:

| Variable | Default | Meaning |
|---|---:|---|
| `ALTIPLANO_HTTP_HOST` | `127.0.0.1` | Bind address. `0.0.0.0` listens on every IPv4 interface. |
| `ALTIPLANO_HTTP_PORT` | `8000` | TCP port. |
| `ALTIPLANO_HTTP_PATH` | `/mcp` | MCP endpoint path. |
| `ALTIPLANO_HTTP_ALLOWED_HOSTS` | localhost patterns | Accepted HTTP `Host` values, comma separated. |
| `ALTIPLANO_HTTP_ALLOWED_ORIGINS` | localhost origins | Accepted browser `Origin` values, comma separated. |
| `ALTIPLANO_CLIENTS` | `~/.config/altiplano/clients` | Client token store. Altiplano creates it mode 600. |
| `ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED` | unset | Serves with no token. Loopback only. |

`VIKUNJA_URL` is read the same way here as for a local install, and
[`README.md`](./README.md) covers the resolution order and the file permissions
Altiplano warns about. It is server-wide: every client of one service reaches one
Vikunja instance, and the `/api/v1` or `/api/v2` suffix selects the API version for all
of them.

`ALTIPLANO_HTTP_ALLOWED_HOSTS` has to contain the `Host` value clients actually
send. A client using `http://192.168.1.50:8000/mcp` sends `192.168.1.50:8000`, which
`192.168.1.50:*` covers. A client using `https://altiplano.home.arpa/mcp` sends a
bare `altiplano.home.arpa`, with HTTPS on its default port.

`0.0.0.0` is a bind address. The allowlist matches the `Host` header a client sends,
and a client sends the hostname it dialled. `0.0.0.0` never appears there.

Test the allowlist from a client machine. A check pointed at `127.0.0.1` on the host
exercises a `Host` value the allowlist accepts by default, and a misconfigured
allowlist goes unnoticed until a real client tries.

> `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` prevent DNS rebinding. A device can send any
> `Host` header it likes, and the client tokens are the access control.

## Register clients

Every client needs two tokens.

- An **Altiplano client token** says which client is calling. Altiplano mints it, stores
only its SHA-256, and it goes in that client's MCP configuration as the
`Authorization` header.

- A **Vikunja API token** is the identity the client acts with. It is created in Vikunja
by the person who owns the account, it lives on this host, and Altiplano presents it
to Vikunja on every request that client makes. Give each person their own, created
from their own Vikunja account. Two clients belonging to one person can share one.

There is no server-wide Vikunja token for HTTP clients. A registered client with no
Vikunja token on its record is refused with a 403. A forgotten token therefore cannot
put somebody on the operator's account.

Run `altiplano-clientkey` as the account that serves. The store then belongs to the
user that reads it. Under Docker, prefix these with
`docker compose exec altiplano`; under uv, with
`uv run --no-project --env-file .env --with altiplano`.

```bash
altiplano-clientkey add my-laptop
altiplano-clientkey list
altiplano-clientkey revoke my-laptop
```

`add` asks for the Vikunja API token at a hidden prompt. To script it, pipe the token
in:

```bash
printf '%s\n' "$VIKUNJA_TOKEN" | altiplano-clientkey add my-laptop
```

Never pass a Vikunja token as an argument. `ps` shows a command line to every user on
the host.

The client token prints once. Give it to that one client and mint a separate one for
the next. A revocation applies to the next request, and the service keeps running.

`list` shows a VIKUNJA column. A client marked `MISSING` has no Vikunja token and is
refused on every request. Give it one with `update`, which leaves its Altiplano client
token alone:

```bash
printf '%s\n' "$VIKUNJA_TOKEN" | altiplano-clientkey update my-laptop
```

`update` is also how a client moves to a different Vikunja token. The client needs no
reconfiguring either way. Only the store changes. `add` is for a client that does not
exist yet, and it refuses a label already in the store.

Revoking a client removes its Vikunja token from the store, and it does nothing to the
token in Vikunja itself. To stop a token working everywhere, delete it in Vikunja under
Settings, API Tokens.

Altiplano refuses to start on a non-loopback address with an empty store, or when no
registered client has a Vikunja token.

Sessions are stateless. Altiplano issues no `mcp-session-id`, and every request is
authorised by the bearer token in it. A restart therefore costs a client nothing, and
there is no session state on the host to grow or expire.

Authentication is always on. An empty store denies every request, and an unreadable
store refuses to start.

Check the whole configuration without opening a socket:

```bash
uv run --no-project --env-file .env --with altiplano altiplano-http --check
```

That prints the bind address, the Host allowlist, the store path, the client count, how
many of those clients have a Vikunja token, and whether authentication is on. It
validates what startup validates. A configuration it approves is one the server can
serve.

> `ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED` has no place in a service definition. It is
> refused on any bind address other than loopback, and behind a proxy or a tunnel a
> loopback bind says nothing about who is calling.

## Encrypt the connection

A client token is a bearer credential. It goes in a header on every request, it is
reusable, and it grants that person's Vikunja permissions including writes and
deletions. Anyone who can observe the traffic can copy one and use it.
[RFC 6750](https://www.rfc-editor.org/rfc/rfc6750#section-5.3) requires TLS for bearer
tokens, and a home network is no exception: a phone, a television, a guest laptop, or
anything else on the same segment can watch plain HTTP.

Two ways to avoid plain HTTP across a network:

- A reverse proxy in front with a certificate, and Altiplano bound to loopback. Caddy
  or nginx with an internal certificate authority both work on a LAN.
- A tunnel terminating TLS and reaching Altiplano over loopback. Set
  `ALTIPLANO_HTTP_ALLOWED_HOSTS` to the public hostname it presents.

Plain HTTP on `127.0.0.1` is fine. Nothing observes loopback.

## Is it working?

Four checks, run from a client machine against the hostname clients will actually use:

1. `altiplano-http --check` on the host reports the store you configured, a non-zero
   client count, `with a token` matching that count, and `authenticated: yes`.
2. A token-bearing `initialize` and `tools/list` succeed through that hostname, and the
   full tool set comes back.
3. The same request with the `Authorization` header removed gets a `401`.
4. Revoke that client's token on the host, then repeat check 2. It gets a `401` with no
   restart. Mint a fresh token afterwards.

Run them again once a proxy or a tunnel goes in front. The `Host` value changes at that
point, and `ALTIPLANO_HTTP_ALLOWED_HOSTS` has to name the public hostname.

Serving two people is worth one more check: with a client registered under each
person's Vikunja token, confirm each reaches its own Vikunja account.

## FAQ

| Symptom | Cause | Fix |
|---|---|---|
| Refuses to start, names the client store | Non-loopback bind with no client tokens | Register a client, or bind to `127.0.0.1` |
| Refuses to start, names Vikunja tokens | No registered client has one | `altiplano-clientkey update <label>` for each |
| Refuses to start, names permissions | The store exists and cannot be read | `chown` it to the service account and `chmod 600` |
| `401` on every call | No token, a typo in it, or a revoked one | Mint a fresh token and check the client sends `Authorization: Bearer` |
| `421` or an opaque transport error | The `Host` clients send is not allowlisted | Read the server log for the rejected value, then add it |
| Connection refused from another machine | Bound to loopback, or the firewall drops it | Set `ALTIPLANO_HTTP_HOST=0.0.0.0`, check the firewall |
| Connects, no tools | A stale install | `altiplano-http --version` on the host |
| `403` naming a Vikunja identity | The client's record has no Vikunja token | `altiplano-clientkey list`, then `update` the client marked `MISSING` |
| Tool calls fail with a Vikunja `401` | Vikunja rejects that client's Vikunja token | Test the token against Vikunja directly, below. Then `altiplano-clientkey update <label>` |
| A client sees someone else's tasks | Two clients were registered with one Vikunja token | `altiplano-clientkey update <label>` on one of them |
| `No solution found`, naming Python | The default interpreter is older than 3.10 | Add `--python 3.13` to the `uv run` command |
| Settings ignored, defaults used | `--env-file` was left off | uv does not read `.env` on its own |
| A client is missing from `list` | Its record failed validation on read | The log names the skipped line number |

The log names the client label on every accepted request and the source address on
every rejected one. Tokens never appear in it.

### A Vikunja 401 is not Altiplano's

Altiplano presents a client's Vikunja token and reports what Vikunja says. Take
Altiplano out of the path before changing anything in it:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer tk_THE_VIKUNJA_TOKEN" \
  "$VIKUNJA_URL/projects"
```

A `401` there is between that token and Vikunja. Mint a new one under Settings, API
Tokens, tick the permissions it needs, curl it again, and only then hand it to
`altiplano-clientkey update`.

Vikunja returns `401` with `code 11` for more than an invalid token. The message reads
"missing, malformed, expired or otherwise invalid token provided", and a token that is
none of those gets it too when used on an endpoint its permissions do not cover. Read
that code as "Vikunja will not accept this token here" and check the token's
permissions alongside its validity.

## The client store

Every Vikunja token in the store is readable by whoever can read the file. There is no
encryption at rest. Altiplano presents each token to Vikunja on every request and needs
the plaintext to do it, and a key kept on the same host would be read by the same
reader. The file is `chmod 600` and owned by the account that serves, and the host is
trusted to stay that way. `systemd-creds` can hold it encrypted and decrypt it at
service start.

Vikunja does the authorising. A client reaches exactly what its Vikunja token reaches,
and narrowing a token's scopes in Vikunja to the tools you expose narrows what a leak
costs. Altiplano adds no permissions of its own and takes none away.

A crash mid-write can leave a `.clients-*` file beside the store, holding every Vikunja
token in it. The next store change deletes any it finds. To clear them sooner, remove
them while the service is idle.

Altiplano stores nothing of its own, and Vikunja stays the system of record. Back up
Vikunja, the service definition, and the client store. If the host goes down, a local
`uvx altiplano` stdio configuration is the fallback on any machine that needs one.
