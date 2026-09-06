# Deploying Altiplano as a shared HTTP service

How to run `altiplano-http` as a managed service on an always-on host. Every client
on your network then reaches one Altiplano process holding one Vikunja token.

The transport itself, the environment variables, the client tokens, and how to point
a client at the endpoint are all in [`README.md`](./README.md) under `Use over HTTP`.
Read that first. What follows covers the parts a first-time deployment usually gets
wrong.

What it assumes you already have:

- A host that stays on: a NAS, a mini PC, a VM, or a Raspberry Pi.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) installed on it.
- A Vikunja API token, and a Vikunja instance the host can reach.

## Install with uv

`uv tool install` builds a virtual environment of its own and puts the commands in a
bin directory. Four environment variables make every path it touches deterministic:

```bash
sudo useradd --system --shell /usr/sbin/nologin altiplano
sudo install -d -o altiplano -g altiplano \
  /opt/altiplano /opt/altiplano/cache /etc/altiplano

sudo -u altiplano env \
  UV_TOOL_DIR=/opt/altiplano/tools \
  UV_TOOL_BIN_DIR=/opt/altiplano/bin \
  UV_CACHE_DIR=/opt/altiplano/cache \
  UV_PYTHON_INSTALL_DIR=/opt/altiplano/python \
  uv tool install "altiplano==1.3.0"
```

`UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` place the environment and the commands.
[`UV_CACHE_DIR`](https://docs.astral.sh/uv/concepts/cache/) is separate and matters
just as much: uv's cache defaults to `$HOME/.cache/uv`, and an account created
without a home directory has nowhere writable to put it. The install fails with
`Failed to initialize cache at ... Permission denied`.

[`UV_PYTHON_INSTALL_DIR`](https://docs.astral.sh/uv/reference/environment/) covers
the case where uv downloads an interpreter. With a suitable system Python already
present, pass `--python /usr/bin/python3.13` and leave that variable out.

The commands land at `/opt/altiplano/bin/altiplano-http` and
`/opt/altiplano/bin/altiplano-clientkey`. Confirm the install with:

```bash
sudo -u altiplano /opt/altiplano/bin/altiplano-http --version
```

Pin the version. An unattended restart should not pick up a release nobody has
looked at yet. To upgrade, install the new version explicitly and restart the
service.

## Configure

Put the settings in `/etc/altiplano/service.env`, owned by the service account:

```dotenv
VIKUNJA_URL=https://vikunja.home.arpa/api/v2
VIKUNJA_API_TOKEN=tk_xxxxxxxx
ALTIPLANO_CLIENTS=/etc/altiplano/clients
ALTIPLANO_HTTP_HOST=0.0.0.0
ALTIPLANO_HTTP_PORT=8000
ALTIPLANO_HTTP_ALLOWED_HOSTS=altiplano.home.arpa,altiplano.home.arpa:*
```

```bash
sudo chown altiplano:altiplano /etc/altiplano/service.env
sudo chmod 600 /etc/altiplano/service.env
```

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

`VIKUNJA_URL` and `VIKUNJA_API_TOKEN` are read the same way here as for a local
install. [`README.md`](./README.md) covers the resolution order and the file
permissions Altiplano warns about.

`ALTIPLANO_HTTP_ALLOWED_HOSTS` has to contain the `Host` value clients actually
send. A client using `http://192.168.1.50:8000/mcp` sends `192.168.1.50:8000`, which
`192.168.1.50:*` covers. A client using `https://altiplano.home.arpa/mcp` sends a
bare `altiplano.home.arpa`, with HTTPS on its default port. Listing both forms costs
nothing.

`0.0.0.0` is a bind address and not a `Host` value. No client connects to it, and it
does not belong in the allowlist.

Test the allowlist from a client machine. A check pointed at `127.0.0.1` on the host
exercises a `Host` value the allowlist accepts by default. A misconfigured allowlist
then goes unnoticed until a real client tries.

> `ALLOWED_HOSTS` and `ALLOWED_ORIGINS` prevent DNS rebinding. They are not
> authentication. A device can send any `Host` header it likes. The client tokens are
> the access control.

## Register clients

Run `altiplano-clientkey` as the service account. The store then belongs to the user
that reads it:

```bash
sudo -u altiplano env ALTIPLANO_CLIENTS=/etc/altiplano/clients \
  /opt/altiplano/bin/altiplano-clientkey add stefan-laptop
```

The token prints once. Give it to that one client and mint a separate one for the
next. Revoking is per client:

```bash
sudo -u altiplano env ALTIPLANO_CLIENTS=/etc/altiplano/clients \
  /opt/altiplano/bin/altiplano-clientkey list

sudo -u altiplano env ALTIPLANO_CLIENTS=/etc/altiplano/clients \
  /opt/altiplano/bin/altiplano-clientkey revoke stefan-laptop
```

A revocation applies to the next request. The service keeps running.

Register at least one client before starting the service on a non-loopback address.
Altiplano refuses to start otherwise. The missing key surfaces at startup.

Authentication is on either way: an empty store denies every request, and an
unreadable store refuses to start. The policy never follows from whether any keys
happen to exist: "nobody is authorised" and "authorise everybody" are different
answers.

Check the whole configuration without opening a socket:

```bash
sudo -u altiplano sh -eu -c '
  set -a
  . /etc/altiplano/service.env
  set +a
  exec /opt/altiplano/bin/altiplano-http --check
'
```

That prints the bind address, the Host allowlist, the store path, the client count,
and whether authentication is on.

The file is sourced inside the service account's own shell, for two reasons. It is
`chmod 600` and owned by that account, and an administrator's shell cannot read it
before `sudo` runs. And sourcing keeps the Vikunja token out of the command line,
where `ps` would show it to every user on the host. This form needs
`service.env` to hold shell-compatible `KEY=VALUE` lines. Systemd's `EnvironmentFile`
accepts the same format.

> `ALTIPLANO_HTTP_ALLOW_UNAUTHENTICATED` has no place in a service unit. It is
> refused on any bind address other than loopback, and behind a proxy or a tunnel a
> loopback bind says nothing about who is calling.

## systemd, for Debian and its derivatives

`/etc/systemd/system/altiplano.service`:

```ini
[Unit]
Description=Altiplano MCP server over HTTP
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=altiplano
Group=altiplano
EnvironmentFile=/etc/altiplano/service.env
ExecStart=/opt/altiplano/bin/altiplano-http
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now altiplano
sudo systemctl status altiplano
journalctl -u altiplano -f
```

`ProtectSystem=strict` mounts the filesystem read-only for this unit. The running
server only ever reads its configuration and its client store. No `ReadWritePaths`
exception is needed. `altiplano-clientkey` writes the store from your shell, outside
the unit's sandbox.

## OpenRC, for Alpine

`/etc/init.d/altiplano`, `chmod 755`:

```sh
#!/sbin/openrc-run

name="altiplano"
description="Altiplano MCP server over HTTP"

supervisor="supervise-daemon"
command="/opt/altiplano/bin/altiplano-http"
command_user="altiplano:altiplano"
output_log="/var/log/altiplano/altiplano.log"
error_log="/var/log/altiplano/altiplano.log"

depend() {
    need net
}

start_pre() {
    checkpath --directory --owner altiplano:altiplano --mode 0755 /var/log/altiplano
}
```

`supervise-daemon` restarts the process on failure with no further configuration.
Its defaults are a 2 second delay and at most 5 restarts in 30 minutes, so
`supervise_daemon_args` is left out.

OpenRC sources `/etc/conf.d/altiplano` on its own. Variables there need exporting to
reach the daemon:

```sh
export VIKUNJA_URL="https://vikunja.home.arpa/api/v2"
export VIKUNJA_API_TOKEN="tk_xxxxxxxx"
export ALTIPLANO_CLIENTS="/etc/altiplano/clients"
export ALTIPLANO_HTTP_HOST="0.0.0.0"
export ALTIPLANO_HTTP_PORT="8000"
export ALTIPLANO_HTTP_ALLOWED_HOSTS="altiplano.home.arpa,altiplano.home.arpa:*"
```

```bash
sudo chmod 600 /etc/conf.d/altiplano
sudo rc-update add altiplano default
sudo rc-service altiplano start
sudo rc-service altiplano status
```

Alpine's `useradd` comes from the `shadow` package. With busybox alone, use
`adduser -S -D altiplano`.

## Firewall the listener

Binding `0.0.0.0` means the process accepts connections on every interface. The
client tokens decide who gets a reply, and a firewall decides who gets to ask.

```bash
# Debian, with ufw
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp

# Alpine, with awall or plain iptables
sudo iptables -A INPUT -p tcp --dport 8000 -s 192.168.1.0/24 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8000 -j DROP
```

Adapt the subnet. Do not port-forward this from the internet on plain HTTP. The
tunnel below is the better answer.

## Behind a Cloudflare tunnel

The tunnel authenticates the connection and Altiplano authenticates the client, and
the two are worth keeping separate: revoking one client stays a local operation, and
it survives a change of transport.

Two things change when the tunnel goes up. `ALTIPLANO_HTTP_ALLOWED_HOSTS` needs the
public hostname, which is the `Host` the tunnel presents. And Cloudflare Access
authenticates browsers through SSO, while an MCP client posting a bearer token is
not a browser: non-interactive clients need a Cloudflare service token, sent as
`CF-Access-Client-Id` and `CF-Access-Client-Secret` alongside their Altiplano
bearer.

Bind to loopback once the tunnel reaches the server, and let `cloudflared` be the
only thing that connects:

```dotenv
ALTIPLANO_HTTP_HOST=127.0.0.1
ALTIPLANO_HTTP_ALLOWED_HOSTS=altiplano.example.com,altiplano.example.com:*
```

The listener is then unreachable from the network, and the firewall rule above
becomes unnecessary.

## Is it working?

Verification runs from a client machine, against the endpoint's real hostname, with a
token minted for that machine.

### Acceptance, before the deployment counts as done

The test suite covers the token store and the gate. It cannot cover your hostname,
your firewall, or your tunnel. Four checks close that gap, and each one has to run
against the endpoint clients will actually use, never against `127.0.0.1` on the
server:

1. `altiplano-http --check` on the host reports the store you configured, a non-zero
   client count, and `authenticated: yes`.
2. From a client machine, a token-bearing `initialize` and `tools/list` succeed
   through the public hostname. The full tool set comes back.
3. The same request with the `Authorization` header removed gets a `401`.
4. Revoke that client's token on the host, then repeat check 2. It gets a `401`
   with no restart. Mint a fresh token afterwards.

Run all four again after the tunnel goes up. The `Host` value changes at that point,
`ALTIPLANO_HTTP_ALLOWED_HOSTS` has to name the public hostname, and a
non-interactive client needs its Cloudflare service token alongside its Altiplano
bearer. Check 4 is the one worth repeating most: it proves revocation still reaches
the running service through the proxy in front of it.

Common failures, and where to look first:

| Symptom | Cause | Fix |
|---|---|---|
| Refuses to start, names the client store | Non-loopback bind with no client tokens | Register a client, or bind to `127.0.0.1` |
| Refuses to start, names permissions | The store exists and cannot be read | `chown` it to the service account and `chmod 600` |
| `401` on every call | No token, a typo in it, or a revoked one | Mint a fresh token and check the client sends `Authorization: Bearer` |
| `421` or an opaque transport error | The `Host` clients send is not allowlisted | Read the server log for the rejected value, then add it |
| Connection refused from another machine | Bound to loopback, or the firewall drops it | Set `ALTIPLANO_HTTP_HOST=0.0.0.0`, check the firewall |
| Connects, no tools | A stale install | `altiplano-http --version` on the host |
| Tool calls fail with a Vikunja `401` | The Vikunja token, not the client token | Check `VIKUNJA_URL` and `VIKUNJA_API_TOKEN` on the host |
| Install fails on a cache path | `UV_CACHE_DIR` is unset and `$HOME` is not writable | Set `UV_CACHE_DIR` under `/opt/altiplano` |
| A client is missing from `list` | Its record failed validation on read | The log names the skipped line number |

The log names the client label on every accepted request and the source address on
every rejected one. Tokens never appear in it.

## What a shared deployment does not give you

One Vikunja token serves every client. A client token answers "may this machine
connect" and nothing else. Every connected client acts as the same Vikunja identity
with the same permissions.

Give the host a dedicated Vikunja service account holding only the scopes the tools
you expose need. Per-user Vikunja identity would mean choosing credentials from the
request context.

Altiplano stores nothing of its own, and Vikunja stays the system of record. Back up
Vikunja, the service definition, and the client store. If the host goes down, the
local `uvx altiplano` stdio configuration is the fallback on any machine that needs
one.
