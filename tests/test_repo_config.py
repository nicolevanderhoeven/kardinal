"""Consistency checks for the dependency lock and the deployment files."""

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

    def test_dockerfile_installs_from_the_lock_with_hashes(self):
        dockerfile = _read('Dockerfile')
        self.assertIn('--require-hashes -r requirements.lock', dockerfile)


class DeploymentFileTests(unittest.TestCase):
    def test_base_image_is_pinned_by_digest(self):
        self.assertRegex(_read('Dockerfile'), r'(?m)^FROM python:[\w.-]+@sha256:[0-9a-f]{64}$')

    def test_container_runs_as_non_root(self):
        self.assertRegex(_read('Dockerfile'), r'(?m)^USER appuser$')

    def test_compose_files_are_hardened(self):
        for name in ('docker-compose.yml', 'docker-compose.registry.yml'):
            text = _read(name)
            for needle in ('read_only: true', 'cap_drop:', '- ALL', 'no-new-privileges:true',
                           'mem_limit:', 'pids_limit:', 'max-size:', 'healthcheck:'):
                self.assertIn(needle, text, f'{name} lacks {needle}')

    def test_traefik_compose_does_not_mount_the_docker_socket(self):
        text = _read('traefik-docker-compose.yml')
        mounts = [l for l in text.splitlines() if 'docker.sock' in l and not l.strip().startswith('#')]
        self.assertEqual(mounts, [])
        self.assertNotIn('traefik:v3.6', text)

    def test_workflow_uses_least_privilege(self):
        text = _read('.github/workflows/docker-build.yml')
        self.assertNotIn('packages: write', text)
        self.assertIn('provenance: true', text)
        self.assertIn('sbom: true', text)
        self.assertIn('schedule:', text)

    def test_dependabot_covers_all_three_ecosystems(self):
        text = _read('.github/dependabot.yml')
        for ecosystem in ('pip', 'docker', 'github-actions'):
            self.assertIn(f'package-ecosystem: {ecosystem}', text)


if __name__ == '__main__':
    unittest.main()
