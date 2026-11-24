"""
Tests for the server update scripts, run against fake `docker` / `docker-compose`
commands (no Docker needed). The fakes log every call and take their behaviour
from environment variables.
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DIGEST_A = 'a' * 64
DIGEST_B = 'b' * 64
IMAGE_A = f'nvanderhoeven/kardinal@sha256:{DIGEST_A}'
IMAGE_B = f'nvanderhoeven/kardinal@sha256:{DIGEST_B}'

FAKE_DOCKER = r'''#!/bin/bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
  pull) exit "${FAKE_PULL_RC:-0}" ;;
  image)  # image inspect --format F IMAGE
    case "$4" in
      *RepoDigests*) echo "${FAKE_REPO_DIGESTS:-[]}" ;;
      *.Id*) echo "${FAKE_NEW_ID:-sha256:new}" ;;
    esac ;;
  inspect)  # inspect --format F CONTAINER
    case "$3" in
      *State.Health*)
        if [ -n "${FAKE_BAD_IMAGE:-}" ] && [ "$(cat "$FAKE_STATE" 2>/dev/null)" = "$FAKE_BAD_IMAGE" ]; then
          echo unhealthy; else echo healthy; fi ;;
      *State.Running*) echo "${FAKE_RUNNING:-true}" ;;
      *.Image*) echo "${FAKE_RUNNING_ID:-sha256:old}" ;;
    esac ;;
esac
exit 0
'''

FAKE_COMPOSE = r'''#!/bin/bash
echo "docker-compose $* KARDINAL_IMAGE=${KARDINAL_IMAGE:-}" >> "$FAKE_LOG"
case "$*" in
  *" config"*) echo "    image: ${KARDINAL_IMAGE:-nvanderhoeven/kardinal:latest}" ;;
  *" up -d"*) echo "${KARDINAL_IMAGE:-}" > "$FAKE_STATE" ;;
esac
exit 0
'''

COMPOSE_OK = 'services:\n  kardinal:\n    image: ${KARDINAL_IMAGE:-nvanderhoeven/kardinal:latest}\n'


class ScriptTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, 'bin')
        os.mkdir(self.bin)
        for name, body in (('docker', FAKE_DOCKER), ('docker-compose', FAKE_COMPOSE)):
            path = os.path.join(self.bin, name)
            with open(path, 'w') as f:
                f.write(body)
            os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        self.kdir = os.path.join(self.tmp, 'kardinal')
        os.mkdir(self.kdir)
        self.compose = os.path.join(self.kdir, 'docker-compose.registry.yml')
        self.env_file = os.path.join(self.kdir, 'kardinal.env')
        self.last_good = os.path.join(self.kdir, 'kardinal.last-good')
        self.log = os.path.join(self.tmp, 'calls.log')
        self.state = os.path.join(self.tmp, 'state')
        self.write(self.compose, COMPOSE_OK)

    def write(self, path, text):
        with open(path, 'w') as f:
            f.write(text)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().splitlines()

    def run_script(self, script, **extra_env):
        env = {
            'PATH': self.bin + os.pathsep + os.environ['PATH'],
            'KARDINAL_DIR': self.kdir,
            'LOG_FILE': os.path.join(self.tmp, 'update.log'),
            'LAST_GOOD_FILE': self.last_good,
            'FAKE_LOG': self.log,
            'FAKE_STATE': self.state,
            'HEALTH_WAIT_SECONDS': '3',
        }
        env.update(extra_env)
        return subprocess.run(
            ['bash', os.path.join(ROOT, script)], env=env,
            capture_output=True, text=True, timeout=60,
        )

    def ups(self):
        return [c for c in self.calls() if ' up -d' in c]


class PinnedUpdateScriptTests(ScriptTestBase):
    SCRIPT = 'update-kardinal-pinned.sh'

    def _pin(self, image=IMAGE_A):
        self.write(self.env_file, f'KARDINAL_IMAGE={image}\n')

    def _good_pull(self, image=IMAGE_A):
        return {'FAKE_REPO_DIGESTS': f'["{image}"]'}

    def test_deploys_the_pinned_digest_and_records_it(self):
        self._pin()
        result = self.run_script(self.SCRIPT, **self._good_pull())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'docker pull {IMAGE_A}', self.calls())
        self.assertEqual(len(self.ups()), 1)
        self.assertTrue(self.ups()[0].endswith(f'KARDINAL_IMAGE={IMAGE_A}'))
        with open(self.last_good) as f:
            self.assertEqual(f.read().strip(), IMAGE_A)

    def test_nothing_to_do_when_the_pinned_image_is_already_running(self):
        self._pin()
        result = self.run_script(
            self.SCRIPT, FAKE_RUNNING_ID='sha256:same', FAKE_NEW_ID='sha256:same', **self._good_pull())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.ups(), [])

    def test_refuses_a_mutable_tag(self):
        self.write(self.env_file, 'KARDINAL_IMAGE=nvanderhoeven/kardinal:latest\n')
        result = self.run_script(self.SCRIPT)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [])  # docker never touched

    def test_refuses_a_malformed_digest(self):
        self.write(self.env_file, 'KARDINAL_IMAGE=nvanderhoeven/kardinal@sha256:1234\n')
        self.assertEqual(self.run_script(self.SCRIPT).returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_env_file_is_parsed_not_executed(self):
        marker = os.path.join(self.tmp, 'pwned')
        self.write(self.env_file, f'touch {marker}\nKARDINAL_IMAGE={IMAGE_A}\n')
        self.run_script(self.SCRIPT, **self._good_pull())
        self.assertFalse(os.path.exists(marker))

    def test_missing_env_file_does_nothing(self):
        self.assertEqual(self.run_script(self.SCRIPT).returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_refuses_a_compose_file_that_mounts_the_docker_socket(self):
        self._pin()
        self.write(self.compose, COMPOSE_OK + '    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n')
        self.assertEqual(self.run_script(self.SCRIPT, **self._good_pull()).returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_refuses_a_compose_file_that_ignores_the_pin(self):
        self._pin()
        self.write(self.compose, 'services:\n  kardinal:\n    image: nvanderhoeven/kardinal:latest\n')
        self.assertEqual(self.run_script(self.SCRIPT, **self._good_pull()).returncode, 1)
        self.assertEqual(self.calls(), [])

    def test_failed_pull_leaves_the_container_alone(self):
        self._pin()
        result = self.run_script(self.SCRIPT, FAKE_PULL_RC='1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.ups(), [])

    def test_pulled_image_with_the_wrong_digest_is_rejected(self):
        self._pin()
        result = self.run_script(self.SCRIPT, FAKE_REPO_DIGESTS=f'["{IMAGE_B}"]')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.ups(), [])

    def test_unhealthy_new_image_rolls_back_to_the_last_good_one(self):
        self._pin(IMAGE_B)
        self.write(self.last_good, IMAGE_A + '\n')
        result = self.run_script(self.SCRIPT, FAKE_BAD_IMAGE=IMAGE_B, **self._good_pull(IMAGE_B))
        self.assertEqual(result.returncode, 1)
        ups = self.ups()
        self.assertEqual(len(ups), 2)
        self.assertTrue(ups[0].endswith(f'KARDINAL_IMAGE={IMAGE_B}'))
        self.assertTrue(ups[1].endswith(f'KARDINAL_IMAGE={IMAGE_A}'))
        with open(self.last_good) as f:
            self.assertEqual(f.read().strip(), IMAGE_A)  # unchanged

    def test_unhealthy_first_deploy_has_nothing_to_roll_back_to(self):
        self._pin()
        result = self.run_script(self.SCRIPT, FAKE_BAD_IMAGE=IMAGE_A, **self._good_pull())
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(self.ups()), 1)
        self.assertFalse(os.path.exists(self.last_good))


class FollowLatestScriptTests(ScriptTestBase):
    SCRIPT = 'update-kardinal.sh'

    def test_logs_the_pulled_digest_and_recreates_on_change(self):
        result = self.run_script(
            self.SCRIPT, COMPOSE_FILE=self.compose, FAKE_REPO_DIGESTS=f'["{IMAGE_A}"]',
            FAKE_RUNNING_ID='sha256:old', FAKE_NEW_ID='sha256:new')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(IMAGE_A, result.stdout)
        self.assertEqual(len(self.ups()), 1)

    def test_unchanged_image_causes_no_restart(self):
        result = self.run_script(
            self.SCRIPT, COMPOSE_FILE=self.compose, FAKE_REPO_DIGESTS=f'["{IMAGE_A}"]',
            FAKE_RUNNING_ID='sha256:same', FAKE_NEW_ID='sha256:same')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.ups(), [])

    def test_failed_pull_leaves_the_container_alone(self):
        # The fake compose always succeeds, so make `pull` fail via a wrapper.
        wrapper = os.path.join(self.bin, 'docker-compose')
        self.write(wrapper, FAKE_COMPOSE.replace('case "$*" in', 'case "$*" in\n  *" pull"*) exit 1 ;;'))
        os.chmod(wrapper, os.stat(wrapper).st_mode | stat.S_IEXEC)
        result = self.run_script(self.SCRIPT, COMPOSE_FILE=self.compose)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.ups(), [])


if __name__ == '__main__':
    unittest.main()
