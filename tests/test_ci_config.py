from __future__ import annotations
import subprocess
import unittest
import yaml
from tests.support import ROOT

class WorkflowSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader keeps GitHub's `on` key as a string instead of YAML 1.1 boolean.
        cls.workflow=yaml.load((ROOT/'.github/workflows/ci.yml').read_text(),Loader=yaml.BaseLoader)
    def test_main_push_is_a_trigger(self):
        self.assertEqual(self.workflow['on']['push']['branches'],['main'])
    def test_no_privileged_pr_target(self):
        self.assertNotIn('pull_request_target',self.workflow['on'])
        self.assertNotIn('workflow_run',self.workflow['on'])
    def test_read_only_token(self):
        self.assertEqual(self.workflow['permissions'],{'contents':'read'})
        for job in self.workflow['jobs'].values():self.assertNotIn('permissions',job)
    def test_no_self_hosted_runners_or_secrets(self):
        text=(ROOT/'.github/workflows/ci.yml').read_text()
        self.assertNotIn('secrets.',text)
        for job in self.workflow['jobs'].values():self.assertEqual(job['runs-on'],'ubuntu-24.04')
    def test_actions_are_sha_pinned(self):
        import re
        for job in self.workflow['jobs'].values():
            for step in job['steps']:
                if 'uses' in step:self.assertRegex(step['uses'],r'^[\w-]+/[\w-]+@[0-9a-f]{40}$')
    def test_git_credentials_not_persisted(self):
        for job in self.workflow['jobs'].values():
            for step in job['steps']:
                if step.get('uses','').startswith('actions/checkout@'):
                    self.assertEqual(step['with']['persist-credentials'],'false')
    def test_native_job_not_on_pull_requests(self):
        cond=self.workflow['jobs']['native-prerequisites']['if']
        self.assertIn("github.ref == 'refs/heads/main'",cond)
        self.assertNotIn("== 'pull_request'",cond)
    def test_native_script_refuses_ordinary_machine(self):
        proc=subprocess.run(['bash',str(ROOT/'scripts/ci/native_smoke.sh')],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=5)
        self.assertEqual(proc.returncode,2)
        self.assertIn('Refusing',proc.stderr)
    def test_native_shell_syntax(self):
        for name in ('native_smoke.sh','secret_scan.sh'):
            subprocess.run(['bash','-n',str(ROOT/'scripts/ci'/name)],check=True)

if __name__=='__main__':unittest.main()
