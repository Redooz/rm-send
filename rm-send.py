#!/usr/bin/env python3
"""
rm-send — send clipboard content (text or image) to the reMarkable tablet.

Transports:
  usb   (default when tablet reachable): renders clipboard to PDF and injects
        it directly into xochitl's document store over SSH, then restarts
        xochitl so it appears in the tablet's library.
  cloud (--cloud): uses rmapi (requires rmapi auth + tablet WiFi; syncs after
        next rm-sync cycle).

Usage:  rm-send [--usb|--cloud] [--notebook-name NAME]
"""

import os, sys, json, uuid, subprocess, shutil, tempfile, time, re, traceback, datetime

# ---- environment hardening (launch context may have a bare PATH) ----
for p in ("/home/linuxbrew/.linuxbrew/bin", os.path.expanduser("~/.local/bin"),
          "/usr/local/bin", "/usr/bin"):
    if os.path.isdir(p) and p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = p + ":" + os.environ.get("PATH", "")
LOG = os.path.expanduser("~/.local/state/rm-send.log")

def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

log(f"start argv={sys.argv} PATH={os.environ['PATH'][:120]}")

def _validated(name, val, default):
    try:
        v = float(val)
        assert 4 <= v <= 72
        return v
    except Exception:
        return float(default)

# ---- configuration: CLI flags > env > ~/.config/rm-send.toml > defaults ----
try:
    import tomllib
except ModuleNotFoundError:      # python < 3.11
    tomllib = None

def load_config():
    path = (os.environ.get("RM_SEND_CONFIG") or
            os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                         "rm-send.toml"))
    if tomllib and os.path.exists(path):
        try:
            with open(path, "rb") as f:
                cfg = tomllib.load(f)
            log(f"config: {path}")
            return cfg
        except Exception as e:
            log(f"config parse failed ({path}): {e} — using defaults")
    return {}

CFG = load_config()
RM_DOCS = "/home/root/.local/share/remarkable/xochitl"
RM_HOST = os.environ.get("RM_HOST") or str(CFG.get("rm_host", "root@10.11.99.1"))
INBOX_NAME = os.environ.get("RM_INBOX_NAME") or str(CFG.get("inbox_name", "zz Inbox"))
TEXT_SIZE = _validated("RM_TEXT_SIZE", os.environ.get("RM_TEXT_SIZE") or CFG.get("text_size", 26), 26)
JSON_SIZE = _validated("RM_JSON_SIZE", os.environ.get("RM_JSON_SIZE") or CFG.get("json_size", 16), 16)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def clipboard_linux():
    """Return ('text', str) or ('image', png_bytes) or None."""
    try:
        types = sh("wl-paste --list-types").stdout.strip().split()
        if "image/png" in types or "image/jpeg" in types:
            t = "image/png" if "image/png" in types else "image/jpeg"
            data = subprocess.run(["wl-paste", "--type", t], capture_output=True).stdout
            if len(data) > 100:
                return ("image", data)
        txt = subprocess.run(["wl-paste", "--type", "text/plain", "--no-newline"], capture_output=True, text=True).stdout
        if txt.strip():
            return ("text", txt)
        # X11 fallback
        txt = sh("xclip -o -selection clipboard").stdout
        if txt.strip():
            return ("text", txt)
    except FileNotFoundError:
        txt = sh("xclip -o -selection clipboard").stdout
        if txt.strip():
            return ("text", txt)
    return None


def clipboard_mac():
    txt = sh("pbpaste").stdout
    if txt.strip():
        data = subprocess.run("pngpaste - 2>/dev/null", shell=True, capture_output=True).stdout
        if len(data) > 100:
            return ("image", data)
        return ("text", txt)
    data = subprocess.run("pngpaste - 2>/dev/null", shell=True, capture_output=True).stdout
    if len(data) > 100:
        return ("image", data)
    return None


def get_clipboard():
    if sys.platform == "darwin":
        return clipboard_mac()
    return clipboard_linux()


def typst_escape(s):
    out = []
    for ch in s:
        if ch in "#$*_/\\<>@-[]+=~'`\"":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def build_pdf(kind, data, workdir):
    main = os.path.join(workdir, "main.typ")
    out = os.path.join(workdir, "out.pdf")
    if kind == "text":
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        esc = typst_escape(data)
        # format-preservation mode: valid JSON renders verbatim (monospace,
        # syntax-highlighted, indentation intact) instead of prose flow
        s = data.strip()
        is_json = False
        if s and s[0] in "{[":
            try:
                json.loads(s)
                is_json = True
            except (ValueError, TypeError):
                pass
        if is_json:
            longest = max((len(m) for m in re.findall(r"`+", s)), default=0)
            ticks = "`" * max(3, longest + 1)
            with open(main, "w") as f:
                f.write(
                    "#set page(width: 1404pt, height: 1872pt, margin: 72pt)\n"
                    f"== {typst_escape(stamp)}\n\n"
                    f"#show raw: set text(size: {JSON_SIZE}pt)\n"
                    f"{ticks}json\n{s}\n{ticks}\n"
                )
        else:
            with open(main, "w") as f:
                f.write(
                    "#set page(width: 1404pt, height: 1872pt, margin: 96pt)\n"
                    f"#set text(size: {TEXT_SIZE}pt, lang: \"en\")\n"
                    "#set par(justify: false, leading: 0.7em)\n\n"
                    f"== {typst_escape(stamp)}\n\n{esc}\n"
                )
    else:  # image
        img = os.path.join(workdir, "img.png")
        with open(img, "wb") as f:
            f.write(data)
        pw, ph = 1404, 1872  # defer ratio detection; scale to fit
        with open(main, "w") as f:
            f.write(
                "#set page(width: 1404pt, height: 1872pt, margin: 24pt)\n"
                '#image("img.png", width: 100%, height: 100%, fit: "contain")\n'
            )
    r = sh(f"typst compile main.typ out.pdf", cwd=workdir)
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise SystemExit("typst failed")
    # page count from PDF /Count
    raw = open(out, "rb").read()
    pages = 1
    import re as _re
    counts = _re.findall(rb"/Pages[^0-9]{0,20}/Count\s+([0-9]+)", raw)
    if counts:
        pages = max(int(c) for c in counts)
    else:
        pages = raw.count(b"/Type /Page")
    return out, max(1, pages)


def content_json(pdfname, pages):
    page_ids = [str(uuid.uuid4()) for _ in range(pages)]
    pages_list = []
    letters = "abcdefghih"  # NOTE: ensure idx hex strings
    hexidx = "%x" % (0x11 + 0)
    for i, pid in enumerate(page_ids):
        entry = {"id": pid, "idx": {"timestamp": "1:2", "value": format(0xb5 + i, "x")},
                 "redir": {"timestamp": "1:2", "value": i}}
        if i == 0:
            entry["template"] = {"timestamp": "1:2", "value": "Blank"}
        pages_list.append(entry)
    return json.dumps({
        "cPages": {
            "original": {"timestamp": "1:2", "value": pages},
            "pages": pages_list,
        },
        "coverPageNumber": -1,
        "dummyDocument": False,
        "extraMetadata": {},
        "fileType": "pdf",
        "fontName": "",
        "formatVersion": 2,
        "lineHeight": -1,
        "margins": 125,
        "orientation": "portrait",
        "pageCount": pages,
        "pageTags": [],
        "sizeInBytes": "0",
        "tags": [],
        "textAlignment": "justify",
        "textScale": 1,
        "zoomMode": "bestFit",
    }, indent=4)


def metadata_json(name):
    ms = str(int(time.time() * 1000))
    return json.dumps({
        "createdTime": ms,
        "deleted": False,
        "lastModified": ms,
        "lastOpened": "0",
        "lastOpenedPage": 0,
        "metadatamodified": False,
        "modified": False,
        "new": False,
        "parent": "",
        "pinned": False,
        "source": "",
        "synced": False,
        "type": "DocumentType",
        "version": 0,
        "visibleName": name,
    }, indent=4)


def rm_reachable():
    r = sh(f"ssh -o ConnectTimeout=4 -o BatchMode=yes {RM_HOST} echo ok")
    return r.returncode == 0 and "ok" in r.stdout


def send_usb(pdf, pages, name):
    iid = str(uuid.uuid4())
    content = content_json(iid + ".pdf", pages)
    meta = metadata_json(name)
    pagedata = "Blank" + "\n" * (pages - 1)
    if not rm_reachable():
        return "off"
    # backup doc store once per day
    bdir = os.path.expanduser("~/rm-backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    bfile = f"{bdir}/docs-{stamp}.tgz"
    if not os.path.exists(bfile):
        print("Backing up tablet document store (once today)...")
        subprocess.run(f"ssh {RM_HOST} 'cd {RM_DOCS} && tar -czf - . -X /dev/null' > {bfile}", shell=True)
    tmpd = tempfile.mkdtemp(prefix="rmsend")
    cp = open(os.path.join(tmpd, "u.content"), "w"); cp.write(content); cp.close()
    mp = open(os.path.join(tmpd, "u.metadata"), "w"); mp.write(meta); mp.close()
    pp = open(os.path.join(tmpd, "u.pagedata"), "w"); pp.write(pagedata); pp.close()
    # copy files to tablet staging, then move into place + restart xochitl on the tablet
    subprocess.run(f"scp -q {pdf} {RM_HOST}:/tmp/rmsend.pdf && "
                   f"scp -q {tmpd}/u.content {RM_HOST}:/tmp/rmsend.content && "
                   f"scp -q {tmpd}/u.metadata {RM_HOST}:/tmp/rmsend.metadata && "
                   f"scp -q {tmpd}/u.pagedata {RM_HOST}:/tmp/rmsend.pagedata", shell=True)
    rc = subprocess.run(
        f"ssh {RM_HOST} 'cp /tmp/rmsend.pdf {RM_DOCS}/{iid}.pdf && "
        f"cp /tmp/rmsend.content {RM_DOCS}/{iid}.content && "
        f"cp /tmp/rmsend.metadata {RM_DOCS}/{iid}.metadata && "
        f"cp /tmp/rmsend.pagedata {RM_DOCS}/{iid}.pagedata && "
        f"rm /tmp/rmsend.* && systemctl restart xochitl'", shell=True,
        capture_output=True, text=True, timeout=120)
    if rc.returncode != 0:
        print(rc.stderr, file=sys.stderr)
        raise SystemExit("tablet write failed")
    r2 = sh(f'rm -rf {tmpd}')
    return iid


def send_cloud(pdf, name):
    """Upload via rmapi (reMarkable cloud). Tablet syncs on its own — no restart."""
    if not shutil.which("rmapi"):
        return "no-tool"
    # rmapi derives the tablet-visible name from the file basename
    nicename = name.replace("/", "-") + ".pdf"
    nicepdf = os.path.join(tempfile.gettempdir(), nicename)
    shutil.copy(pdf, nicepdf)
    r = subprocess.run(["rmapi", "put", nicepdf, "/zz Inbox"], capture_output=True, text=True, timeout=120)
    os.remove(nicepdf)
    if r.returncode == 0:
        return "ok"
    print(r.stdout + r.stderr, file=sys.stderr)
    return "fail"


def rmapi_config_paths():
    configured = os.environ.get("RMAPI_CONFIG")
    if configured:
        return [os.path.expanduser(configured)]
    paths = []
    if sys.platform == "darwin":
        paths.append(os.path.expanduser(
            "~/Library/Application Support/rmapi/rmapi.conf"))
    paths.extend([
        os.path.expanduser("~/.config/rmapi/rmapi.conf"),
        os.path.expanduser("~/.rmapi"),
    ])
    return paths


def rmapi_ready():
    return any(os.path.exists(path) for path in rmapi_config_paths())


STATE_DIR = os.path.expanduser("~/.local/state/rm-send")


def queue_and_spawn(pdf, name, kind, transport):
    """Fast path: hand off transport to a detached child, return instantly."""
    os.makedirs(STATE_DIR, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    pending = os.path.join(STATE_DIR, f"pending-{stamp}.pdf")
    shutil.copy(pdf, pending)
    extra = "--usb" if transport == "usb" else "--cloud"
    subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), extra, "--bg", pending, name, kind],
        start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if transport == "usb":
        notify("Queued ✓ — sending to tablet over USB (tablet will restart xochitl)")
    else:
        notify("Queued ✓ — appears in the zz Inbox folder on the tablet shortly")


def transport_bg(pdf, name, kind, transport):
    """Detached: performs the actual delivery (hold single-flight lock here)."""
    import fcntl
    os.makedirs(OUTSTATE := os.path.dirname(pdf), exist_ok=True)
    shutil.copy = shutil.copy  # noqa
    LOCK_F = os.path.expanduser("~/.local/state/rm-send.lock")
    try:
        lf = open(LOCK_F, "w")
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lf.write(str(os.getpid()))
        lf.flush()
    except OSError:
        log("bg: previous send still running — dropping this one")
        notify("⚠ rm-send is already busy — previous send still in progress")
        os.remove(pdf)
        return
    log(f"bg transport={transport} name={name}")
    try:
        if transport == "cloud" and rmapi_ready() and shutil.which("rmapi"):
            res = send_cloud(pdf, name)
            if res == "ok":
                notify(f"Delivered ✓ (cloud) — “{name}”")
            elif rm_reachable():
                notify("Cloud send failed — retrying over USB")
                send_usb(pdf, count_pdf_pages(pdf), name)
                notify(f"Delivered ✓ (USB) — “{name}”")
            else:
                notify("✗ rm-send: cloud failed and tablet not on USB — kept in " + OUTSTATE)
                return  # keep pending file for inspection
        elif transport == "usb" and rm_reachable():
            pages = count_pdf_pages(pdf)
            send_usb(pdf, pages, name)
            notify(f"Delivered ✓ (USB) — “{name}”")
        else:
            notify("✗ rm-send: nothing to send — see ~/.local/state/rm-send.log")
            return
        os.remove(pdf)
    except Exception:
        log("bg EXCEPTION:\n" + traceback.format_exc())
        notify("✗ rm-send failed — details in ~/.local/state/rm-send.log")


def count_pdf_pages(path):
    raw = open(path, "rb").read()
    counts = re.findall(rb"/Pages[^0-9]{0,20}/Count\s+([0-9]+)", raw)
    return max([int(c) for c in counts], default=1)


def main():
    args = sys.argv[1:]
    # ---- interactive onboarding: exchange the 8-char code for a device token ----
    if "--auth" in args:
        print("rm-send cloud onboarding:")
        print("  1. open  https://my.remarkable.com/device/browser/connect")
        print("  2. paste the 8-character code when rmapi asks for it\n")
        r = subprocess.run(["rmapi"])
        if rmapi_ready() and r.returncode == 0:
            notify("✓ rmapi configured — cloud transport enabled")
        else:
            notify("✗ rmapi auth did not complete — see terminal")
        return
    # ---- detached background run: only the transport ----
    if "--bg" in args:
        i = args.index("--bg")
        transport_bg(args[i + 1], args[i + 2], args[i + 3],
                     "usb" if "--usb" in args else "cloud")
        return
    # ---- fast path ----
    cloud_ok = rmapi_ready() and shutil.which("rmapi")
    if "--usb" in args:
        transport = "usb"
    elif "--cloud" in args:
        transport = "cloud"
    elif cloud_ok:
        transport = "cloud"
    else:
        if rm_reachable():
            log("cloud not configured; falling back to USB")
        else:
            notify("Cloud not configured (run rm-send --auth) and tablet not reachable over USB")
        transport = "usb"
    cb = get_clipboard()
    if not cb:
        notify("Nothing usable on the clipboard")
        raise SystemExit(1)
    kind, data = cb
    notify(f"Sending {kind} to reMarkable…")
    workdir = tempfile.mkdtemp(prefix="rmsend-t")
    try:
        pdf, pages = build_pdf(kind, data, workdir)
        name = (INBOX_NAME + " · " + time.strftime("%d %b %H:%M:%S") +
                (" (text)" if kind == "text" else " (image)"))
        queue_and_spawn(pdf, name, kind, transport)
    finally:
        sh(f"rm -rf {workdir}")


def notify(msg):
    log(msg)
    print(msg)
    if sys.platform == "darwin" or os.uname().sysname == "Darwin":
        try:
            subprocess.run(["osascript", "-e",
                            f'display notification "{msg}" with title "rm-send"'],
                           timeout=10, capture_output=True)
        except Exception:
            pass
    else:
        sh(f"notify-send -a rm-send \"{msg}\"")


if __name__ == "__main__":
    try:
        main()
        log("done ok")
    except SystemExit as e:
        log(f"exit {e.code}")
    except Exception:
        err = traceback.format_exc()
        log("EXCEPTION:\n" + err)
        notify("rm-send failed — see ~/.local/state/rm-send.log")
        raise SystemExit(99)
