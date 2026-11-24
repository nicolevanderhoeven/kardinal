"""Consistency checks for the dependency lock."""

import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


def _pins(text):
    """{normalised name: version} for lines like name==1.2.3 (hash lines ignored)."""
    pins = {}
    for line in text.splitlines():
        m = re.match(r'^([A-Za-z0-9_.-]+)==([^\s\;]+)', line)
        if m:
            pins[m.group(1).lower().replace('_', '-')] = m.group(2)
    return pins


class RequirementsLockTests(unittest.TestCase):
    def test_lock_matches_requirements_txt(self):
        wanted = _pins(_read('requirements.txt'))
        locked = _pins(_read('requirements.lock'))
        self.assertTrue(wanted)
        for name, version in wanted.items():
            self.assertEqual(
                locked.get(name), version,
                f'requirements.lock is out of date for {name}; regenerate it '
                '(see the header of requirements.txt)')

    def test_every_locked_package_has_hashes(self):
        text = _read('requirements.lock')
        entries = re.split(r'\n(?=[A-Za-z0-9_.-]+==)', text)
        packages = [e for e in entries if re.match(r'[A-Za-z0-9_.-]+==', e)]
        self.assertGreater(len(packages), 5)
        for entry in packages:
            self.assertRegex(entry, r'--hash=sha256:[0-9a-f]{64}', entry.splitlines()[0])


if __name__ == '__main__':
    unittest.main()
