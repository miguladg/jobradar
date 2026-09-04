"""Offline test: fakes the OpenAI responses to prove the tool loop works."""
import json
from types import SimpleNamespace
from job_evaluator import evaluator

def fake_call(id_, name, args):
    return SimpleNamespace(id=id_, function=SimpleNamespace(
        name=name, arguments=json.dumps(args)))

# Turn 1: model asks for skill overlap. Turn 2: asks for years.
# Turn 3: submits the final verdict.
SCRIPT = [
    [fake_call("c1", "calculate_skill_overlap",
               {"required_skills": ["python", "sql", "airflow", "aws"]})],
    [fake_call("c2", "get_experience_years", {"technology": "aws"})],
    [fake_call("c3", "submit_evaluation", {
        "fit_score": 68, "verdict": "good_match",
        "matching_skills": ["python", "sql", "aws"],
        "missing_requirements": ["airflow"],
        "strengths": "Strong SQL and Python background.",
        "concerns": "Only 0.7 years of AWS; no Airflow experience.",
        "recommended_action": "apply_with_tailored_cv"})],
]

class FakeCompletions:
    def __init__(self): self.turn = 0
    def create(self, **kwargs):
        calls = SCRIPT[self.turn]; self.turn += 1
        msg = SimpleNamespace(tool_calls=calls, content=None, role="assistant")
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

class FakeClient:
    def __init__(self): self.chat = SimpleNamespace(completions=FakeCompletions())

_fake = FakeClient()          # ONE instance, reused — otherwise the
evaluator.get_client = lambda: _fake   # turn counter resets every call

job = {"id": 1, "title": "Data Engineer", "company": "TestCo",
       "location": "Remote", "seniority_level": "Mid-Senior",
       "employment_type": "Full-time", "is_remote": "Sí",
       "salary": "N/A", "description": "Python, SQL, Airflow, AWS required."}

print("Running tool loop...\n")
result = evaluator.evaluate_job(job)
print("\nFINAL RESULT:")
print(json.dumps(result, indent=2, ensure_ascii=False))
assert result["fit_score"] == 68 and "error" not in result
print("\n✅ Loop works: 2 tools executed, verdict returned.")
