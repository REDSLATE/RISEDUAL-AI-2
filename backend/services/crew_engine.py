"""
CrewAI-style Multi-Agent Engine using Emergent LLM.

Implements sequential agent orchestration where each specialized agent
analyzes data from its expertise area, and a final synthesizer agent
combines all findings into a consensus output.

Pattern: Data → Agent1 → Agent2 → Agent3 → Synthesizer → Final Output

Thread Safety:
- run_parallel_crew uses ThreadPoolExecutor with asyncio.run() per thread
- Each thread gets its own event loop, LlmChat instance, and HTTP session
- No shared mutable state: api_key is copied as a string, no DB connections
- LlmChat is instantiated fresh per agent call (no connection pool sharing)
"""

import os
import logging
import asyncio

from services.structured_log import unwrap_gather_result

from dataclasses import dataclass
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)

_MAX_PARALLEL_AGENTS = 4


@dataclass
class AgentConfig:
    """Defines a specialized AI agent with role, goal, and backstory."""
    role: str
    goal: str
    backstory: str
    model_provider: str = "openai"
    model_name: str = "gpt-5.2"


@dataclass
class TaskResult:
    """Output from a single agent task."""
    agent_role: str
    output: str
    success: bool


def _run_agent_sync(api_key: str, role: str, goal: str, backstory: str,
                    model_provider: str, model_name: str,
                    task_prompt: str, prior_context: str,
                    session_suffix: str) -> TaskResult:
    """
    Thread-safe, self-contained agent execution.

    This function is designed to run inside a ThreadPoolExecutor worker.
    It creates its own event loop (via asyncio.run) and its own LlmChat
    instance — no shared mutable state with the calling thread.
    """
    system_message = (
        f"You are a {role}.\n"
        f"Your goal: {goal}\n"
        f"Background: {backstory}\n\n"
        "Be specific, cite data points, and be concise. "
        "Output your analysis as structured text with clear sections."
    )

    full_prompt = task_prompt
    if prior_context:
        full_prompt = (
            "PREVIOUS AGENT FINDINGS (use these to inform your analysis):\n"
            f"{prior_context}\n\n"
            "YOUR TASK:\n"
            f"{task_prompt}"
        )

    async def _call() -> str:
        session_id = f"crew_{role.replace(' ', '_')}_{session_suffix}"
        chat = LlmChat(
            api_key=api_key,
            session_id=session_id,
            system_message=system_message
        ).with_model(model_provider, model_name)
        return await chat.send_message(UserMessage(text=full_prompt))

    try:
        response = asyncio.run(_call())
        text = response.strip() if isinstance(response, str) else str(response).strip()
        return TaskResult(agent_role=role, output=text, success=True)
    except Exception as e:
        logger.error(f"Agent '{role}' failed: {e}")
        return TaskResult(agent_role=role, output=f"Agent error: {e}", success=False)


class CrewEngine:
    """Orchestrates multiple AI agents in sequence or parallel."""

    def __init__(self, api_key: str = None):
        self._api_key = api_key or os.environ.get("EMERGENT_LLM_KEY", "")

    async def _run_agent(self, agent: AgentConfig, task_prompt: str,
                         prior_context: str = "", session_suffix: str = "") -> TaskResult:
        """Execute a single agent on the current event loop (used by synthesizer)."""
        return _run_agent_sync(
            api_key=self._api_key,
            role=agent.role, goal=agent.goal, backstory=agent.backstory,
            model_provider=agent.model_provider, model_name=agent.model_name,
            task_prompt=task_prompt, prior_context=prior_context,
            session_suffix=session_suffix,
        )

    async def run_crew(self, agents: list[AgentConfig], tasks: list[str],
                       synthesizer: AgentConfig, synth_prompt: str,
                       session_suffix: str = "") -> dict:
        """
        Run a sequential crew: each agent gets the accumulated context
        from all prior agents, then a synthesizer combines everything.
        """
        accumulated_context = ""
        agent_results = []

        for agent, task in zip(agents, tasks):
            result = await asyncio.to_thread(
                _run_agent_sync,
                api_key=self._api_key,
                role=agent.role, goal=agent.goal, backstory=agent.backstory,
                model_provider=agent.model_provider, model_name=agent.model_name,
                task_prompt=task, prior_context=accumulated_context,
                session_suffix=session_suffix,
            )
            agent_results.append(result)
            if result.success:
                accumulated_context += f"\n\n--- {agent.role} Analysis ---\n{result.output}"

        synth_result = await asyncio.to_thread(
            _run_agent_sync,
            api_key=self._api_key,
            role=synthesizer.role, goal=synthesizer.goal, backstory=synthesizer.backstory,
            model_provider=synthesizer.model_provider, model_name=synthesizer.model_name,
            task_prompt=synth_prompt, prior_context=accumulated_context,
            session_suffix=session_suffix,
        )

        return {
            "agent_outputs": [
                {"role": r.agent_role, "output": r.output, "success": r.success}
                for r in agent_results
            ],
            "synthesis": synth_result.output,
            "all_agents_succeeded": all(r.success for r in agent_results),
            "synthesis_succeeded": synth_result.success,
        }

    async def run_parallel_crew(self, agents: list[AgentConfig], tasks: list[str],
                                synthesizer: AgentConfig, synth_prompt: str,
                                session_suffix: str = "") -> dict:
        """
        Run agents in parallel using thread pool, then synthesize.

        Thread safety: Each worker thread runs _run_agent_sync which:
        - Creates its own asyncio event loop via asyncio.run()
        - Instantiates a fresh LlmChat (own HTTP session)
        - Receives only immutable/copied arguments (strings)
        - Never touches shared DB connections or mutable state
        """
        import concurrent.futures

        loop = asyncio.get_running_loop()
        api_key = self._api_key  # Copy immutable ref for closure

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(agents), _MAX_PARALLEL_AGENTS)) as pool:
            futures = [
                loop.run_in_executor(
                    pool,
                    _run_agent_sync,
                    api_key,
                    agent.role, agent.goal, agent.backstory,
                    agent.model_provider, agent.model_name,
                    task, "",
                    session_suffix,
                )
                for agent, task in zip(agents, tasks)
            ]
            agent_results = await asyncio.gather(*futures, return_exceptions=True)

        clean_results = []
        accumulated_context = ""
        for i, r in enumerate(agent_results):
            # Canonical `asyncio.gather` unwrap — silent on
            # CancelledError, logged on other BaseExceptions.
            # Fallback is a failure TaskResult so downstream
            # synthesis doesn't crash at `tr.success`.
            r = unwrap_gather_result(
                r,
                TaskResult(agent_role=agents[i].role,
                           output=f"Error or cancelled: {r}",
                           success=False),
                logger, "crew_engine", "agent run failed",
                agent_role=agents[i].role,
            )
            clean_results.append(r)
            if r.success:
                accumulated_context += f"\n\n--- {r.agent_role} Analysis ---\n{r.output}"

        # Synthesizer runs on its own thread (not blocking the main loop)
        synth_result = await asyncio.to_thread(
            _run_agent_sync,
            api_key=self._api_key,
            role=synthesizer.role, goal=synthesizer.goal, backstory=synthesizer.backstory,
            model_provider=synthesizer.model_provider, model_name=synthesizer.model_name,
            task_prompt=synth_prompt, prior_context=accumulated_context,
            session_suffix=session_suffix,
        )

        return {
            "agent_outputs": [
                {"role": r.agent_role, "output": r.output, "success": r.success}
                for r in clean_results
            ],
            "synthesis": synth_result.output,
            "all_agents_succeeded": all(r.success for r in clean_results),
            "synthesis_succeeded": synth_result.success,
        }
