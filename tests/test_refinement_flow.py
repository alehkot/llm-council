"""Execute the real legacy graph through refinement with fake model boundaries."""

import asyncio
import os
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("API_AUTH_KEY", "test-api-key")

from backend import langgraph_flow as flow
from backend.interactive import PromptResponse
from backend.trace import TraceCollector

ORIGINAL = "  Original query\nwith detail  "


class RefinementFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_refinement(
        self, answers: list[PromptResponse], *, timeout: bool = False
    ) -> tuple:
        events, updates = [], []
        queue = asyncio.Queue()
        context = flow.FlowContext(
            trace=TraceCollector(),
            callbacks=flow.FlowCallbacks(
                emit_event=lambda e: events.append(deepcopy(e)),
                update_message=lambda u: updates.append(deepcopy(u)),
            ),
            interactive_enabled=True,
            prompt_refinement_enabled=True,
            interactive_queue=queue,
            session_id="synthetic-session",
            interactive_timeout_seconds=0.001 if timeout else 1,
            collected_responses={
                "pre_query_config": PromptResponse(
                    prompt_id="pre_query_config", responses={}, skipped=True
                )
            },
        )
        for answer in answers:
            queue.put_nowait(answer)
        suggestions = [
            {
                "intent": f"intent{n}",
                "clarifying_questions": [f"question{n}"],
                "refined_prompt": f"suggestion{n}",
            }
            for n in range(1, 4)
        ]
        # Stop after the refinement handoff by having the fake Stage1 return no
        # members. This proves real graph refinement/trace/persistence behavior,
        # not end-to-end council execution or model quality.
        with (
            patch.object(
                flow, "generate_prompt_refinement", AsyncMock(side_effect=suggestions)
            ) as provider,
            patch.object(
                flow, "stage1_collect_responses", AsyncMock(return_value=[])
            ) as stage1,
            patch.object(flow, "_ensure_skills_runtime", AsyncMock()),
        ):
            result = await flow.run_council_graph(ORIGINAL, None, context)
        self.assertIn("All models failed", result["error"])
        return result, context, events, updates, provider, stage1

    async def test_edited_refined_prompt_handed_to_stage1_and_saved(self) -> None:
        answer = PromptResponse(
            "prompt_refinement",
            {"refinement_action": "use_refined", "refined_prompt": " edited query "},
        )
        result, context, events, updates, provider, stage1 = await self.run_refinement(
            [answer]
        )
        self.assertEqual(provider.await_count, 1)
        self.assertEqual(provider.call_args.kwargs["user_query"], ORIGINAL)
        self.assertEqual(stage1.call_args.args[0], "edited query")
        final = result["metadata"]["prompt_refinement"]
        self.assertEqual(
            final,
            {
                "enabled": True,
                "decision": "use_refined",
                "original_prompt": ORIGINAL,
                "refined_prompt": "edited query",
                "intent": "intent1",
                "clarifying_questions": ["question1"],
                "clarifying_answers": [],
                "iterations": 1,
            },
        )
        self.assertEqual(
            context.collected_responses["prompt_refinement_1"].responses[
                "refined_prompt"
            ],
            " edited query ",
        )
        self.assertTrue(
            any(
                u.get("metadata", {}).get("prompt_refinement") == final for u in updates
            )
        )
        prompt_events = [
            e for e in events if e.get("prompt_type") == "prompt_refinement"
        ]
        self.assertEqual(len(prompt_events), 1)
        self.assertEqual(prompt_events[0]["session_id"], "synthetic-session")
        trace = [
            e for e in context.trace.events if e["kind"] == "prompt_refinement_response"
        ]
        self.assertEqual(trace[0]["final_action"], "use_refined")

    async def test_use_original_and_skip_preserve_exact_original(self) -> None:
        for answer in (
            PromptResponse("prompt_refinement", {"refinement_action": "use_original"}),
            PromptResponse("prompt_refinement", {}, skipped=True),
        ):
            with self.subTest(answer=answer):
                result, context, _, _, provider, stage1 = await self.run_refinement(
                    [answer]
                )
                self.assertEqual(stage1.call_args.args[0], ORIGINAL)
                self.assertEqual(
                    result["metadata"]["prompt_refinement"]["decision"], "use_original"
                )
                self.assertEqual(provider.await_count, 1)
                if answer.skipped:
                    self.assertTrue(
                        any(
                            e["kind"] == "prompt_refinement_skipped"
                            for e in context.trace.events
                        )
                    )

    async def test_clarification_forces_round_and_exact_provider_context(self) -> None:
        answers = [
            PromptResponse(
                "prompt_refinement",
                {
                    "refinement_action": "use_original",
                    "refined_prompt": " working query ",
                    "clarifying_answer_0": " detail ",
                    "additional_insights": " insight ",
                },
            ),
            PromptResponse("prompt_refinement", {"refinement_action": "use_refined"}),
        ]
        result, context, _, _, provider, stage1 = await self.run_refinement(answers)
        self.assertEqual(provider.await_count, 2)
        args = provider.call_args.kwargs
        self.assertEqual(args["user_query"], ORIGINAL)
        self.assertEqual(
            args["clarifying_answers"], [{"question": "question1", "answer": "detail"}]
        )
        self.assertEqual(args["previous_refined_prompt"], "working query")
        self.assertEqual(args["additional_context"], "insight")
        self.assertEqual(args["iteration"], 2)
        self.assertEqual(stage1.call_args.args[0], "suggestion2")
        self.assertEqual(result["metadata"]["prompt_refinement"]["iterations"], 2)
        trace = [
            e for e in context.trace.events if e["kind"] == "prompt_refinement_response"
        ]
        self.assertEqual(trace[0]["user_action"], "use_original")
        self.assertEqual(trace[0]["final_action"], "refine_again")
        self.assertEqual(trace[0]["clarifying_answer_count"], 1)
        self.assertEqual(trace[0]["additional_insights_length"], 7)

    async def test_repeat_is_capped_at_three_and_saves_all_raw_answers(self) -> None:
        answers = [
            PromptResponse(
                "prompt_refinement",
                {
                    "refinement_action": "refine_again",
                    "additional_insights": f" insight{n} ",
                },
            )
            for n in range(1, 4)
        ]
        result, context, events, _, provider, stage1 = await self.run_refinement(
            answers
        )
        self.assertEqual(provider.await_count, 3)
        self.assertEqual(
            provider.call_args.kwargs["additional_context"], "insight1\n\ninsight2"
        )
        self.assertEqual(stage1.call_args.args[0], "suggestion3")
        self.assertEqual(result["metadata"]["prompt_refinement"]["iterations"], 3)
        for n in range(1, 4):
            self.assertEqual(
                context.collected_responses[f"prompt_refinement_{n}"].responses[
                    "additional_insights"
                ],
                f" insight{n} ",
            )
        prompts = [e for e in events if e.get("prompt_type") == "prompt_refinement"]
        self.assertEqual(len(prompts), 3)
        self.assertIn("Round 3", prompts[-1]["title"])

    async def test_legacy_timeout_remains_skip_not_new_durable_timeout(self) -> None:
        result, context, _, _, provider, stage1 = await self.run_refinement(
            [], timeout=True
        )
        self.assertEqual(provider.await_count, 1)
        self.assertEqual(stage1.call_args.args[0], ORIGINAL)
        self.assertEqual(
            result["metadata"]["prompt_refinement"]["decision"], "use_original"
        )
        self.assertTrue(
            any(e["kind"] == "prompt_refinement_skipped" for e in context.trace.events)
        )

    async def test_refinement_disabled_does_not_call_provider(self) -> None:
        context = flow.FlowContext(
            trace=TraceCollector(),
            interactive_enabled=False,
            prompt_refinement_enabled=False,
        )
        with (
            patch.object(flow, "generate_prompt_refinement", AsyncMock()) as provider,
            patch.object(flow, "stage1_collect_responses", AsyncMock(return_value=[])),
            patch.object(flow, "_ensure_skills_runtime", AsyncMock()),
        ):
            await flow.run_council_graph(ORIGINAL, None, context)
        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
