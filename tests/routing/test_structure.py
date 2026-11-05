"""Architecture acceptance checks use import execution and parsed control flow."""

import ast
import subprocess
import sys
from pathlib import Path


def test_router_imports_with_all_provider_modules_forbidden():
    source = Path(__file__).resolve().parents[2] / "src"
    code = """
import sys
class Reject:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('aipc.adapters', 'openai', 'claude_agent_sdk', 'google')):
            raise AssertionError('provider coupling: ' + fullname)
sys.meta_path.insert(0, Reject())
sys.path.insert(0, sys.argv[1])
from aipc.agents.router import AgentRouter
from aipc.agents.registry import AgentRegistry
from aipc.agents.routing_models import RoutingRequest, RoutingPolicy, TaskRequirements
request=RoutingRequest(project_id='p',run_id='r',phase_id='16',attempt=1,task_id='t',task_type='phase',task_digest='0'*64,requirements=TaskRequirements())
decision=AgentRouter(AgentRegistry(),qualifications=lambda:()).select(request,RoutingPolicy())
assert decision.outcome=='NO_ELIGIBLE_AGENT'
print('independent')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(source)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "independent"


def test_core_controller_has_no_provider_identity_conditionals():
    source = Path(__file__).resolve().parents[2] / "src/aipc/controller"
    for path in source.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.If):
                values = {
                    c.value
                    for c in ast.walk(node.test)
                    if isinstance(c, ast.Constant) and isinstance(c.value, str)
                }
                assert not values & {"codex", "claude", "gemini"}, path
