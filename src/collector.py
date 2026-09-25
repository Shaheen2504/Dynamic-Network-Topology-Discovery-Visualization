"""Collect CDP/LLDP neighbour output from one Cisco switch over SSH.

The collector is a front end to the existing pipeline and nothing more. It
produces exactly what a human would produce by pasting a terminal session into
a file, so `parsers.parse_capture()` consumes its output with no change:

    collector -> <HOST>__cisco_session.txt -> parse_capture -> topology -> map

Netmiko's send_command() strips the prompt and the echoed command, but
parse_capture() splits a transcript on prompt lines. The collector therefore
reassembles the transcript from the device's own prompt, which is also where
the hostname comes from - no device name is configured anywhere.

Credentials come from the environment, an SSH key, or an interactive prompt.
They are never accepted as command-line arguments, never written to disk, and
never included in a repr, a log line or an exception message.
"""

import argparse
import getpass
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# `show cdp neighbors` and `show lldp neighbors`: the summary formats, which are
# the ones validated against real output.
COMMANDS = ("show cdp neighbors", "show lldp neighbors")

ENV_USERNAME = "NET_SSH_USERNAME"
ENV_PASSWORD = "NET_SSH_PASSWORD"
ENV_ENABLE = "NET_SSH_ENABLE"
ENV_KEY_FILE = "NET_SSH_KEY_FILE"


class CollectorError(Exception):
    """Collection failed. `kind` says why, for the caller to act on."""

    def __init__(self, kind, host, detail=""):
        self.kind = kind          # unreachable | auth | command | credentials
        self.host = host
        super().__init__(f"{kind}: {host}" + (f" - {detail}" if detail else ""))


@dataclass
class Target:
    """One device to collect from.

    `password` and `secret` are excluded from the generated repr so a target
    cannot leak into a traceback, a log line or a debugger session.
    """

    host: str
    username: str
    password: str = field(default="", repr=False)
    secret: str = field(default="", repr=False)
    key_file: str = ""
    device_type: str = "cisco_ios"
    port: int = 22
    timeout: int = 30

    @classmethod
    def from_env(cls, host, username=None, ask=False, **kwargs):
        """Build a target from the environment, optionally prompting.

        Deliberately no way to pass a password on the command line: arguments
        are visible in `ps` output and land in shell history.
        """
        username = username or os.environ.get(ENV_USERNAME, "")
        password = os.environ.get(ENV_PASSWORD, "")
        key_file = os.environ.get(ENV_KEY_FILE, "")

        if not username:
            if not ask:
                raise CollectorError("credentials", host,
                                     f"set {ENV_USERNAME} or pass --username")
            username = input(f"username for {host}: ").strip()
        if not password and not key_file:
            if not ask:
                raise CollectorError("credentials", host,
                                     f"set {ENV_PASSWORD} or {ENV_KEY_FILE}")
            password = getpass.getpass(f"password for {username}@{host}: ")

        return cls(host=host, username=username, password=password,
                   secret=os.environ.get(ENV_ENABLE, ""), key_file=key_file,
                   **kwargs)


def _netmiko_session(target):
    """Open a Netmiko session. Imported lazily so tests need no dependency."""
    try:
        from netmiko import ConnectHandler
    except ImportError as exc:                                # pragma: no cover
        raise CollectorError("command", target.host,
                             "netmiko is not installed (pip install netmiko)") from exc

    params = {
        "device_type": target.device_type,
        "host": target.host,
        "username": target.username,
        "port": target.port,
        "conn_timeout": target.timeout,
    }
    if target.key_file:
        params.update(use_keys=True, key_file=target.key_file)
    if target.password:
        params["password"] = target.password
    if target.secret:
        params["secret"] = target.secret

    try:
        return ConnectHandler(**params)
    except Exception as exc:
        raise CollectorError(_classify(exc), target.host, _safe(exc, target)) from None


def _classify(exc):
    """Map a driver exception onto a category without importing its classes."""
    name = type(exc).__name__.lower()
    if "auth" in name:
        return "auth"
    if "timeout" in name or "connection" in name or isinstance(exc, OSError):
        return "unreachable"
    return "command"


def _safe(exc, target):
    """Exception text with any credential removed.

    A driver can echo what it was given; nothing that came from the environment
    may reach a log or a traceback.
    """
    text = str(exc)
    for secret in (target.password, target.secret):
        if secret:
            text = text.replace(secret, "***")
    return text.splitlines()[0][:200] if text else type(exc).__name__


def collect(target, commands=COMMANDS, connect=_netmiko_session):
    """Run the commands and return a transcript parse_capture() understands.

    `connect` is injectable so the collector can be tested against a scripted
    device with no SSH and no Netmiko installed.
    """
    try:
        session = connect(target)
    except CollectorError:
        raise
    except Exception as exc:
        # Any transport, not just the built-in one, gets its failure classified.
        raise CollectorError(_classify(exc), target.host, _safe(exc, target)) from None

    try:
        prompt = session.find_prompt().strip()
        blocks = []
        for command in commands:
            try:
                output = session.send_command(command)
            except Exception as exc:
                raise CollectorError("command", target.host,
                                     f"{command!r}: {_safe(exc, target)}") from None
            blocks.append(f"{prompt}{command}\n{output.rstrip()}")
        return "\n".join(blocks) + f"\n{prompt}\n"
    finally:
        disconnect = getattr(session, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect()
            except Exception:
                pass          # a failed teardown must not mask a real result


def hostname_from(transcript):
    """Device name taken from its own prompt, not from configuration."""
    first = transcript.splitlines()[0] if transcript.strip() else ""
    for marker in ("#", ">"):
        if marker in first:
            return first.split(marker)[0].strip()
    return ""


def save(transcript, directory, hostname=None):
    """Write the transcript where the existing pipeline already looks for it."""
    hostname = hostname or hostname_from(transcript) or "unknown-device"
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hostname}__cisco_session.txt"
    path.write_text(transcript)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Collect CDP/LLDP neighbours from one Cisco switch over SSH.",
        epilog=f"Credentials come from {ENV_USERNAME}/{ENV_PASSWORD} "
               f"(or {ENV_KEY_FILE}), or are prompted for. They are never "
               f"accepted as arguments.")
    ap.add_argument("host")
    ap.add_argument("-u", "--username")
    ap.add_argument("-o", "--out-dir", default="captures")
    ap.add_argument("--port", type=int, default=22)
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args(argv)

    try:
        target = Target.from_env(args.host, args.username, ask=sys.stdin.isatty(),
                                 port=args.port, timeout=args.timeout)
        transcript = collect(target)
    except CollectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return {"unreachable": 2, "auth": 3, "credentials": 4}.get(exc.kind, 1)

    path = save(transcript, args.out_dir)
    print(f"wrote {path}")
    print(f"next: python3 src/mapview.py {args.out_dir} map.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
