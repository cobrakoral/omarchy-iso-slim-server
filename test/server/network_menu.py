#!/usr/bin/env python3
"""Exercise the actual Bash/Gum network menu without changing host links."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class NetworkMenuTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="server-network-menu-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.queue = self.root / "steps.json"
        self.events = self.root / "events.json"
        self.gum = self.root / "gum"
        self.config = self.root / "server-config.py"
        self.gum.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
kind = args[0]
queue = Path(os.environ['MOCK_GUM_QUEUE'])
events = Path(os.environ['MOCK_GUM_EVENTS'])
steps = json.loads(queue.read_text())
if not steps or steps[0]['kind'] != kind:
    raise SystemExit('Unexpected Gum action: ' + kind)
step = steps.pop(0)
queue.write_text(json.dumps(steps))
event = {'kind': kind}
if kind == 'pager':
    event['content'] = sys.stdin.read()
else:
    header = args[args.index('--header') + 1]
    event['header'] = header
    event['answer'] = step['answer']
    if kind == 'choose':
        options = args[args.index('--header') + 2:] or sys.stdin.read().splitlines()
        if step['answer'] not in options:
            raise SystemExit('Choice not presented: ' + step['answer'])
        event['options'] = options
    elif kind != 'input':
        raise SystemExit('Unknown Gum action: ' + kind)
    sys.stdout.write(step['answer'] + '\\n')
past = json.loads(events.read_text())
past.append(event)
events.write_text(json.dumps(past))
''')
        self.gum.chmod(0o700)
        self.config.write_text(f'''#!/usr/bin/env python3
import json, runpy, sys
if sys.argv[1:] == ['--list-interfaces']:
    print(json.dumps([
        {{'name': 'ens18', 'mac': '52:54:00:12:34:56'}},
        {{'name': 'ens19', 'mac': '52:54:00:12:34:57'}},
    ]))
else:
    runpy.run_path({str(ROOT / 'builder/server/config.py')!r}, run_name='__main__')
''')

    @staticmethod
    def choose(answer):
        return {"kind": "choose", "answer": answer}

    @staticmethod
    def entry(answer):
        return {"kind": "input", "answer": answer}

    def run_menu(self, steps):
        self.queue.write_text(json.dumps(steps))
        self.events.write_text("[]")
        env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                   MOCK_GUM_QUEUE=str(self.queue), MOCK_GUM_EVENTS=str(self.events))
        script = ("set -euo pipefail\n" +
                  f"source {str(ROOT / 'builder/server/network-menu.sh')!r}\n" +
                  f"network_menu {str(self.config)!r}\n" +
                  "printf 'CONFIG_JSON=%s\\n' \"$NETWORK_INTERFACES_JSON\"\n")
        result = subprocess.run(["bash", "-c", script], text=True, env=env, capture_output=True)
        return result

    def settings(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.queue.read_text()), [])
        return json.loads(result.stdout.split("CONFIG_JSON=", 1)[-1].strip())

    def test_default_is_one_question_with_no_manual_configuration(self):
        result = self.run_menu([self.choose("Automatic dual-stack (default)")])
        self.assertEqual(self.settings(result), {})
        events = json.loads(self.events.read_text())
        self.assertEqual(len(events), 1)
        self.assertIn("automatic IPv4 + IPv6", events[0]["header"])

    def test_multiple_interfaces_independent_manual_ipv4_ipv6(self):
        result = self.run_menu([
            self.choose("Configure interfaces"),
            self.choose("ens18"),
            self.choose("Configure IPv4"),
            self.choose("Manual (static)"),
            self.entry("192.0.2.20/24"), self.entry("192.0.2.1"), self.entry("192.0.2.53"),
            self.choose("Configure IPv6"),
            self.choose("Manual (static)"),
            self.entry("2001:db8:20::20/64"), self.entry("fe80::1"), self.entry("2001:db8::53"),
            self.choose("Show current configuration"), {"kind": "pager"},
            self.choose("Back"),
            self.choose("ens19"),
            self.choose("Configure IPv4"), self.choose("Disabled"),
            self.choose("Back"),
            self.choose("Apply and return"),
        ])
        settings = self.settings(result)
        self.assertEqual(settings["ens18"]["ipv4"], "static")
        self.assertEqual(settings["ens18"]["ipv6"], "static")
        self.assertEqual(settings["ens18"]["gateway6"], "fe80::1")
        self.assertEqual(settings["ens19"]["ipv4"], "disabled")
        self.assertNotIn("ipv6", settings["ens19"])
        events = json.loads(self.events.read_text())
        pager = next(event["content"] for event in events if event["kind"] == "pager")
        self.assertIn("Address=192.0.2.20/24", pager)
        self.assertIn("Address=2001:db8:20::20/64", pager)
        self.assertIn("UseHostname=no", pager)

    def test_invalid_address_does_not_replace_current_settings(self):
        result = self.run_menu([
            self.choose("Configure interfaces"), self.choose("ens18"),
            self.choose("Configure IPv6"), self.choose("Manual (static)"),
            self.entry("192.0.2.4/24"), self.entry(""), self.entry(""),
            self.choose("Back"), self.choose("Apply and return"),
        ])
        self.assertEqual(self.settings(result), {})
        self.assertIn("Invalid IPv6 configuration", result.stderr)

    def test_restore_defaults_removes_previous_override(self):
        result = self.run_menu([
            self.choose("Configure interfaces"), self.choose("ens19"),
            self.choose("Configure IPv6"), self.choose("Disabled"),
            self.choose("Restore defaults"),
            self.choose("Show current configuration"), {"kind": "pager"},
            self.choose("Back"), self.choose("Apply and return"),
        ])
        self.assertEqual(self.settings(result), {})
        pager = next(item["content"] for item in json.loads(self.events.read_text())
                     if item["kind"] == "pager")
        self.assertIn("IPv6AcceptRA=yes", pager)

    def test_optional_gateway_and_dns_are_accepted(self):
        result = self.run_menu([
            self.choose("Configure interfaces"), self.choose("ens18"),
            self.choose("Configure IPv4"), self.choose("Manual (static)"),
            self.entry("192.0.2.88/24"), self.entry(""), self.entry(""),
            self.choose("Back"), self.choose("Apply and return"),
        ])
        self.assertEqual(self.settings(result)["ens18"]["gateway4"], "")


if __name__ == "__main__":
    unittest.main()
