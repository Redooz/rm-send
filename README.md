# rm-send

Clipboard → reMarkable tablet, with one keystroke.

`rm-send` watches your system clipboard and delivers its contents — typed text
or an image — to a reMarkable 1/2/Paper Pro/Move tablet as a native document,
organized in a dedicated `zz Inbox` library folder. It runs on macOS and Linux,
needs no cloud account replacement tools, and never modifies the tablet's software.

```
┌──────────────┐      ┌───────────────┐      ┌────────────────────┐
│  Clipboard   │ ──►  │  typst → PDF  │ ──►  │  reMarkable tablet │
│ (text/image) │      │  (0.3 s)      │      │  zz Inbox folder   │
└──────────────┘      └───────────────┘      └────────────────────┘
```

## How it works

1. Clipboard content is classified automatically: images take precedence,
   otherwise plain text is used.
2. Content is rendered to a PDF matching the tablet's page geometry
   (1404 × 1872 pt). Text documents use a clean typographic layout; images are
   placed full-page.
3. The PDF is delivered through one of two transports:
   - **Cloud** (default) — `rmapi` uploads to your reMarkable account; the
     tablet's `rm-sync` service fetches it in the background. No interaction
     with the tablet UI is required.
   - **USB** (offline fallback) — the document is written directly into
     xochitl's document store over SSH, followed by a xochitl restart.
4. The upload runs detached from the invoking session. The user receives an
   immediate *Queued* notification on keypress, and a *Delivered* notification
   upon completion.

Documents appear as `zz Inbox · <date time> (text|image)` in the tablet library.
From there, use the tablet's built-in Capture/selection tool to paste content
into any notebook.

## Requirements

| Component | Linux (Wayland) | macOS |
|---|---|---|
| Python 3 | pacman: `python` | preinstalled |
| Rendering | pacman: `typst` | `brew install typst` |
| Clipboard | pacman: `wl-clipboard` (X11: `xclip`) | `brew install pngpaste` |
| Cloud transport | `rmapi` binary — [releases](https://github.com/ddvk/rmapi/releases) | `brew rmapi` is **not available**; download `rmapi-macos-arm64.zip` (or `-intel`) from [releases](https://github.com/ddvk/rmapi/releases), unzip, copy to `~/.local/bin/rmapi` |
| USB transport | root SSH key on the tablet (`root@10.11.99.1`) | same |

## Installation

```bash
git clone https://github.com/Redooz/rm-send && cd rm-send
./install.sh
rm-send --auth     # one-time; see below
```

`install.sh` deploys a launch shim to `~/.local/bin/rm-send`, verifies each
dependency, and reports whether the cloud and USB transports are available.

### Authentication (cloud transport)

```bash
rm-send --auth
```

This runs `rmapi`'s device-auth flow: paste the 8-character code shown at
[my.remarkable.com/device/browser/connect](https://my.remarkable.com/device/browser/connect).
The resulting token is stored in rmapi's platform config location:
`~/Library/Application Support/rmapi/rmapi.conf` on macOS and
`~/.config/rmapi/rmapi.conf` on Linux (gitignored).
Neither the repository nor the script embeds any credentials.

To migrate auth to another machine, either copy the conf file or repeat
`rm-send --auth` there. To revoke access, delete the conf file or remove the
device from your reMarkable account settings.

## Usage

```bash
rm-send            # auto: cloud if configured, else USB
rm-send --cloud    # force cloud
rm-send --usb      # force USB (offline/private)
rm-send --auth     # configure cloud credentials
```

Hyprland (Omarchy) keybinding — add to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + SHIFT + I", "reMarkable: send clipboard", "rm-send")
```

On macOS, add a shortcut via the Shortcuts app, Raycast, or Alfred pointing to
`rm-send.py`.

## Configuration

Settings live in `~/.config/rm-send.toml` (see `config.example.toml`); the same
settings can be provided as environment variables, which take precedence.
Resolution order: CLI flags > environment > TOML > defaults.

| Key (toml) | Env var | Default | Purpose |
|---|---|---|---|
| `rm_host` | `RM_HOST` | `root@10.11.99.1` | Tablet SSH target |
| `inbox_name` | `RM_INBOX_NAME` | `zz Inbox` | Library folder / doc-name prefix |
| `text_size` | `RM_TEXT_SIZE` | `26` | Prose font size (pt; 4–72) |
| `json_size` | `RM_JSON_SIZE` | `16` | JSON/code font size (pt) |

Sizes relate to the tablet's physical screen (~6.2 in wide): 26 pt prose ≈
2.7 mm tall, 16 pt mono ≈ 1.9 mm.

## Design notes

- **Fault tolerance**: the transport phase runs as a detached process; a lock
  file prevents concurrent sends (a second keypress raises a busy alert instead
  of racing the first).
- **Diagnostics**: every run is logged to `~/.local/state/rm-send.log`; failures
  surface as notifications rather than invisible errors.
- **Backups**: before its first write of each day, the tool snapshots the
  tablet's entire document store to `~/rm-backups/docs-<date>.tgz`.
- **Tablet integrity**: documents are only *added*; the tool installs nothing,
  patches nothing, and writes no configuration on the tablet.
- **Latency**: rendering and validation complete in ~0.4 s; slow transports
  never block the user.

## Limitations

- Cloud delivery latency depends on the tablet's sync cycle (seconds to
  minutes); USB delivery restarts xochitl (~4–8 s), closing any open view.
- Rich-text clipboard content is exported as plain text.
- Cloud transport depends on the `rmapi` CLI, which is no longer maintained
  upstream (v0.0.35 remains functional); [`remarkable-toolkit`](https://github.com/Oellix/remarkable-toolkit)
  is a compatible alternative should the API change.

## Security

Credentials live only in rmapi's platform config (`~/Library/Application Support/rmapi/rmapi.conf` on macOS; `~/.config/rmapi/rmapi.conf` on Linux, both gitignored). All cloud
traffic is HTTPS, directly to reMarkable's endpoints. The device token grants
read/write access to the account's document storage — treat it accordingly.
