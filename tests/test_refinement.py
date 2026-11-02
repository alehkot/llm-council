"""Offline finite refinement transitions and saved-prompt continuation checks."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import run_admission, run_interaction, storage
from backend.consultation_contract import (
    AnswerRequest,
    CreateRunRequest,
    PromptInstance,
)
from backend.interactive import PromptResponse, get_prompt_refinement_prompt
from backend.refinement import RefinementState

ORIGINAL = "  Исходный вопрос\nwith details  "
SUGGESTION = {
    "intent": "  intent  ",
    "clarifying_questions": ["", " Which? "],
    "refined_prompt": "  suggested query  ",
}


def response(**values: object) -> PromptResponse:
    return PromptResponse(prompt_id="prompt_refinement", responses=values)


def waiting() -> RefinementState:
    return RefinementState.start(ORIGINAL).accept_suggestion(deepcopy(SUGGESTION))


class RefinementTests(unittest.TestCase):
    def test_start_arguments_are_original_and_detached(self) -> None:
        state = RefinementState.start(ORIGINAL)
        self.assertEqual(
            state.provider_arguments(),
            {
                "user_query": ORIGINAL,
                "clarifying_answers": None,
                "additional_context": None,
                "previous_refined_prompt": None,
                "iteration": 1,
            },
        )
        saved = state.to_continuation().model_dump(mode="json")
        self.assertEqual(
            RefinementState.from_continuation(
                run_interaction.Continuation.model_validate(
                    json.loads(json.dumps(saved))
                )
            ),
            state,
        )

    def test_original_exact_and_edited_refined_derived_text(self) -> None:
        self.assertEqual(
            waiting()
            .apply_response(
                response(refinement_action="use_original", refined_prompt=" edit ")
            )
            .state.refined_prompt,
            ORIGINAL,
        )
        result = waiting().apply_response(
            response(
                refinement_action="use_refined", refined_prompt="  edited\nquery  "
            )
        )
        self.assertEqual(result.state.refined_prompt, "edited\nquery")
        self.assertEqual(result.edited_prompt_length, len("edited\nquery"))
        self.assertEqual(
            result.state.metadata()["prompt_refinement"]["original_prompt"], ORIGINAL
        )

    def test_empty_edit_falls_back_to_suggestion_then_previous(self) -> None:
        result = waiting().apply_response(
            response(refinement_action="use_refined", refined_prompt=" \n ")
        )
        self.assertEqual(result.state.refined_prompt, "suggested query")
        state = RefinementState.start(ORIGINAL).accept_suggestion(
            {**SUGGESTION, "refined_prompt": ""}
        )
        self.assertEqual(
            state.apply_response(response()).state.refined_prompt, ORIGINAL
        )

    def test_legacy_unknown_and_missing_action_default_refined(self) -> None:
        for value in (None, "invalid", "", 42, [], {}):
            with self.subTest(value=value):
                self.assertEqual(
                    waiting()
                    .apply_response(response(refinement_action=value))
                    .state.decision,
                    "use_refined",
                )

    def test_clarification_and_insight_force_repeat_preserving_indices(self) -> None:
        for action in ("use_original", "use_refined", "refine_again"):
            with self.subTest(action=action):
                raw = response(
                    refinement_action=action,
                    refined_prompt=" edited ",
                    clarifying_answer_1="  answer  ",
                    additional_insights=" extra ",
                )
                before = deepcopy(raw.to_dict())
                result = waiting().apply_response(raw)
                state = result.state
                self.assertEqual(state.phase, "needs_suggestion")
                self.assertEqual(state.iteration, 2)
                self.assertEqual(
                    state.clarifying_answers,
                    [{"question": " Which? ", "answer": "answer"}],
                )
                self.assertEqual(state.additional_context_parts, ["extra"])
                self.assertEqual(
                    state.provider_arguments()["previous_refined_prompt"], "edited"
                )
                self.assertEqual(result.user_action, action)
                self.assertEqual(result.action, "refine_again")
                self.assertEqual(raw.to_dict(), before)

    def test_whitespace_clarifications_do_not_force_repeat(self) -> None:
        result = waiting().apply_response(
            response(
                refinement_action="use_original",
                clarifying_answer_1=" \n",
                additional_insights="  ",
            )
        )
        self.assertEqual(result.state.decision, "use_original")

    def test_three_round_limit_and_accumulated_context(self) -> None:
        state = RefinementState.start(ORIGINAL)
        for round_number in range(1, 4):
            state = state.accept_suggestion(
                {
                    "intent": f"intent{round_number}",
                    "clarifying_questions": [f"question{round_number}"],
                    "refined_prompt": f"suggestion{round_number}",
                }
            )
            result = state.apply_response(
                response(
                    refinement_action="refine_again",
                    clarifying_answer_0=f"answer{round_number}",
                    additional_insights=f"extra{round_number}",
                )
            )
            state = RefinementState.from_continuation(result.state.to_continuation())
        self.assertEqual(state.phase, "complete")
        self.assertEqual(state.iteration, 3)
        self.assertEqual(state.decision, "use_refined")
        self.assertEqual(state.refined_prompt, "suggestion3")
        self.assertEqual(len(state.clarifying_answers), 3)
        self.assertEqual(state.additional_context_parts, ["extra1", "extra2", "extra3"])
        self.assertEqual(state.metadata()["prompt_refinement"]["intent"], "intent3")
        self.assertEqual(result.action, "refine_again")

    def test_skip_and_legacy_timeout_preserve_original(self) -> None:
        state = waiting()
        skipped = state.apply_response(
            PromptResponse(
                prompt_id="prompt_refinement",
                responses={"additional_insights": "ignored"},
                skipped=True,
            )
        ).state
        self.assertEqual(skipped, state.finish_original())
        self.assertEqual(skipped.refined_prompt, ORIGINAL)
        self.assertEqual(skipped.clarifying_answers, [])

    def test_finite_transitions_reject_wrong_phase(self) -> None:
        initial = RefinementState.start(ORIGINAL)
        ready = waiting()
        complete = ready.apply_response(response()).state
        for state in (initial, complete):
            with self.assertRaises(ValueError):
                state.apply_response(response())
            with self.assertRaises(ValueError):
                state.finish_original()
        for state in (ready, complete):
            with self.assertRaises(ValueError):
                state.accept_suggestion(SUGGESTION)
            with self.assertRaises(ValueError):
                state.provider_arguments()
        for state in (initial, ready):
            with self.assertRaises(ValueError):
                state.metadata()

    def test_checkpoint_missing_fields_unknown_versions_and_lossy_types_fail(
        self,
    ) -> None:
        state = waiting()
        payload = state.to_continuation().model_dump(mode="json")
        for key in state.model_dump():
            broken = deepcopy(payload)
            del broken["state"][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                RefinementState.from_continuation(
                    run_interaction.Continuation.model_validate(broken)
                )
        for changes in (
            {"version": 2},
            {"version": True},
            {"iteration": True},
            {"iteration": 4},
            {"phase": "running"},
            {"decision": "use_refined"},
            {"last_suggestion": None},
            {"unknown": "x"},
            {"original_query": 1},
            {"clarifying_answers": [{"question": "x", "answer": 1}]},
        ):
            broken = deepcopy(payload)
            broken["state"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                RefinementState.from_continuation(
                    run_interaction.Continuation.model_validate(broken)
                )
        with self.assertRaises(ValueError):
            RefinementState.from_continuation(
                run_interaction.Continuation(point="other", state=state.model_dump())
            )

    def test_mutated_models_and_non_json_are_revalidated(self) -> None:
        state = waiting()
        state.iteration = True
        for call in (
            state.to_continuation,
            lambda: state.apply_response(response()),
            state.finish_original,
        ):
            with self.assertRaises(ValueError):
                call()
        state = waiting()
        state.last_suggestion.intent = object()
        with self.assertWarns(UserWarning), self.assertRaises((ValueError, TypeError)):
            state.to_continuation()

    def test_no_aliasing_between_saved_state_inputs_and_results(self) -> None:
        suggestion = deepcopy(SUGGESTION)
        initial = RefinementState.start(ORIGINAL)
        state = initial.accept_suggestion(suggestion)
        suggestion["clarifying_questions"].append("other")
        self.assertEqual(len(state.last_suggestion.clarifying_questions), 2)
        next_state = state.apply_response(response(clarifying_answer_1="answer")).state
        arguments = next_state.provider_arguments()
        arguments["clarifying_answers"][0]["answer"] = "modified"
        self.assertEqual(next_state.clarifying_answers[0]["answer"], "answer")
        encoded = next_state.to_continuation()
        encoded.state["additional_context_parts"].append("modified")
        self.assertEqual(next_state.additional_context_parts, [])
        self.assertEqual(initial.phase, "needs_suggestion")

    def test_fresh_interpreter_restores_saved_suggestion_without_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkpoint.json"
            path.write_text(
                waiting().to_continuation().model_dump_json(), encoding="utf-8"
            )
            program = """
import json, pathlib, socket, sys
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network'))
from backend.refinement import RefinementState
from backend.run_interaction import Continuation
from backend.interactive import PromptResponse
saved = Continuation.model_validate_json(pathlib.Path(sys.argv[1]).read_text())
state = RefinementState.from_continuation(saved)
result = state.apply_response(PromptResponse('prompt_refinement', {'refinement_action': 'use_refined'}))
print(json.dumps(result.state.metadata()))
"""
            result = subprocess.run(
                [sys.executable, "-c", program, str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            final = json.loads(result.stdout)["prompt_refinement"]
            self.assertEqual(final["refined_prompt"], "suggested query")
            self.assertEqual(final["original_prompt"], ORIGINAL)
            self.assertEqual(
                final["clarifying_questions"], SUGGESTION["clarifying_questions"]
            )

    def test_saved_waiting_checkpoint_resumes_from_first_answer_without_provider(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(storage, "DATA_DIR", str(Path(directory) / "conversations")),
        ):
            run_id = str(uuid4())
            run_admission.prepare(
                run_id,
                CreateRunRequest(
                    content=ORIGINAL,
                    interaction_policy="persistent",
                    recovery_policy="explicit_retry",
                    refine=True,
                ),
            )
            state = waiting()
            instance = PromptInstance(
                run_id=run_id,
                instance_id=uuid4(),
                prompt=get_prompt_refinement_prompt(SUGGESTION).to_dict(),
            )
            run_interaction.put_prompt(
                run_id, instance, state.to_continuation(), expected_revision=0
            )
            raw_answer = AnswerRequest(
                responses={
                    "refinement_action": "use_refined",
                    "refined_prompt": "  exact edited  ",
                }
            )
            run_interaction.answer(run_id, str(instance.instance_id), raw_answer)
            # A reloaded checkpoint contains all inputs. No workflow or provider
            # is invoked to reconstruct the already generated suggestion.
            entry = run_interaction.get(run_id)["prompts"][0]
            restored = RefinementState.from_continuation(
                run_interaction.Continuation.model_validate(entry["continuation"])
            )
            accepted = entry["answer"]
            derived = restored.apply_response(
                PromptResponse(
                    prompt_id=entry["instance"]["prompt"]["prompt_id"],
                    responses=accepted["responses"],
                    skipped=accepted["skipped"],
                )
            ).state
            run_interaction.advance(
                run_id, str(instance.instance_id), derived.to_continuation()
            )
            receipt_path = Path(directory) / "run_receipts" / f"{run_id}.json"
            raw = receipt_path.read_bytes()
            run_interaction.advance(
                run_id,
                str(instance.instance_id),
                restored.apply_response(
                    PromptResponse(prompt_id="prompt_refinement", **accepted)
                ).state.to_continuation(),
            )
            self.assertEqual(receipt_path.read_bytes(), raw)
            result = run_interaction.get(run_id)["prompts"][0]
            self.assertEqual(result["answer"], raw_answer.model_dump(mode="json"))
            self.assertEqual(derived.refined_prompt, "exact edited")
            self.assertEqual(result["phase"], "consumed")
            self.assertEqual(
                RefinementState.from_continuation(
                    run_interaction.Continuation.model_validate(
                        result["next_continuation"]
                    )
                ),
                derived,
            )


if __name__ == "__main__":
    unittest.main()
