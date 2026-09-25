"""SSH collector, driven by a scripted device.

No real switch, no SSH, and no netmiko installation is required: the transport
is injected. The device's scripted answers are the real CDP and LLDP tables
lifted out of the production capture, so the test proves a genuine round trip -
what the collector writes must parse back to exactly what the fixture parses to.
"""

from pathlib import Path

import pytest

import collector
import parsers
from parsers import cisco

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = (ROOT / "tests/fixtures/CORE-SW-1__cisco_session.txt").read_text()

# The command outputs exactly as the switch produced them, keyed by command.
_BLOCKS = {}
for _dialect, _host, _output in cisco.split_session(CAPTURE):
    _BLOCKS["show cdp neighbors" if "cdp" in _dialect else "show lldp neighbors"] = _output
PROMPT = f"{_host}#"

SECRET = "correct-horse-battery-staple"


class FakeDevice:
    """Minimal stand-in for a Netmiko session."""

    def __init__(self, blocks=None, prompt=PROMPT, fail_on=None, error=None):
        self.blocks = _BLOCKS if blocks is None else blocks
        self.prompt = prompt
        self.fail_on = fail_on
        self.error = error or RuntimeError("command rejected")
        self.commands = []
        self.disconnected = False

    def find_prompt(self):
        return self.prompt

    def send_command(self, command):
        self.commands.append(command)
        if command == self.fail_on:
            raise self.error
        return self.blocks.get(command, "")

    def disconnect(self):
        self.disconnected = True


def target(**kw):
    kw.setdefault("host", "198.51.100.10")
    kw.setdefault("username", "netops")
    kw.setdefault("password", SECRET)
    return collector.Target(**kw)


def collect_with(device, **kw):
    return collector.collect(target(**kw), connect=lambda _t: device)


# ------------------------------------------------------------------ round trip

def test_transcript_parses_with_the_existing_parser_unchanged():
    transcript = collect_with(FakeDevice())
    assert parsers.parse_capture(transcript)


def test_collected_records_match_the_original_capture_exactly():
    collected = parsers.parse_capture(collect_with(FakeDevice()))
    original = [r for r in parsers.parse_capture(CAPTURE)]
    # The capture ran `show cdp neighbors` twice; the collector runs it once.
    expected = [r for i, r in enumerate(original)
                if r.protocol == "lldp" or i < sum(x.protocol == "cdp" for x in original) // 2]
    assert collected == expected


def test_both_commands_are_issued_in_order():
    device = FakeDevice()
    collect_with(device)
    assert device.commands == list(collector.COMMANDS)


def test_transcript_reconstructs_the_prompt_the_parser_splits_on():
    # Netmiko strips the prompt and the echoed command; parse_capture needs both.
    transcript = collect_with(FakeDevice())
    for command in collector.COMMANDS:
        assert f"{PROMPT}{command}" in transcript


def test_hostname_comes_from_the_device_prompt():
    transcript = collect_with(FakeDevice(prompt="EDGE-SW-99#"))
    assert collector.hostname_from(transcript) == "EDGE-SW-99"
    assert collector.hostname_from("user@box>") == "user@box"


def test_saved_file_is_named_so_the_existing_loader_finds_it(tmp_path):
    import main
    path = collector.save(collect_with(FakeDevice()), tmp_path)
    assert path.name == f"{_host}__cisco_session.txt"
    assert main.collect(tmp_path)          # the pipeline loads it unmodified


def test_session_is_closed_even_when_a_command_fails():
    device = FakeDevice(fail_on="show lldp neighbors")
    with pytest.raises(collector.CollectorError):
        collect_with(device)
    assert device.disconnected


# ------------------------------------------------------------ failure handling

def test_command_failure_is_reported_with_its_command():
    device = FakeDevice(fail_on="show cdp neighbors")
    with pytest.raises(collector.CollectorError) as exc:
        collect_with(device)
    assert exc.value.kind == "command"
    assert "show cdp neighbors" in str(exc.value)


@pytest.mark.parametrize("exception, expected", [
    (type("NetmikoAuthenticationException", (Exception,), {})("bad password"), "auth"),
    (type("NetmikoTimeoutException", (Exception,), {})("timed out"), "unreachable"),
    (ConnectionRefusedError("refused"), "unreachable"),
    (OSError("no route to host"), "unreachable"),
])
def test_connection_failures_are_classified(exception, expected):
    assert collector._classify(exception) == expected


@pytest.mark.parametrize("raised, kind", [
    (type("NetmikoAuthenticationException", (Exception,), {})("denied"), "auth"),
    (type("NetmikoTimeoutException", (Exception,), {})("timed out"), "unreachable"),
    (ConnectionRefusedError("refused"), "unreachable"),
])
def test_connection_failures_surface_as_classified_collector_errors(raised, kind):
    def refuse(_target):
        raise raised

    with pytest.raises(collector.CollectorError) as exc:
        collector.collect(target(), connect=refuse)
    assert exc.value.kind == kind
    assert exc.value.host == "198.51.100.10"


def test_a_leaky_connection_error_still_scrubs_the_password():
    def refuse(_target):
        raise RuntimeError(f"ssh failed: password={SECRET}")

    with pytest.raises(collector.CollectorError) as exc:
        collector.collect(target(), connect=refuse)
    assert SECRET not in str(exc.value)


# ----------------------------------------------------------- secret handling

def test_password_never_appears_in_a_repr():
    t = target(secret="enable-secret-value")
    for text in (repr(t), str(t)):
        assert SECRET not in text
        assert "enable-secret-value" not in text


def test_password_is_scrubbed_from_driver_error_text():
    t = target()
    leaked = RuntimeError(f"auth failed for netops with password {SECRET}")
    assert SECRET not in collector._safe(leaked, t)
    assert "***" in collector._safe(leaked, t)


def test_no_credential_is_accepted_as_a_command_line_argument():
    parser_text = collector.main.__doc__ or ""
    import argparse
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        collector.main(["--help"])
    help_text = buf.getvalue().lower()
    for forbidden in ("--password", "--secret", "--enable"):
        assert forbidden not in help_text


def test_missing_credentials_fail_loudly_rather_than_connecting(monkeypatch):
    for var in (collector.ENV_USERNAME, collector.ENV_PASSWORD, collector.ENV_KEY_FILE):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(collector.CollectorError) as exc:
        collector.Target.from_env("198.51.100.10", ask=False)
    assert exc.value.kind == "credentials"


def test_key_file_is_accepted_instead_of_a_password(monkeypatch):
    monkeypatch.setenv(collector.ENV_USERNAME, "netops")
    monkeypatch.delenv(collector.ENV_PASSWORD, raising=False)
    monkeypatch.setenv(collector.ENV_KEY_FILE, "/home/netops/.ssh/id_ed25519")
    t = collector.Target.from_env("198.51.100.10", ask=False)
    assert t.key_file and not t.password


def test_transcript_contains_no_credential():
    transcript = collect_with(FakeDevice())
    assert SECRET not in transcript
    assert "netops" not in transcript
