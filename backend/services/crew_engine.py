"""
CrewAI-style Multi-Agent Engine using Emergent LLM.

Implements sequential agent orchestration where each specialized agent
analyzes data from its expertise area, and a final synthesizer agent
combines all findings into a consensus output.

Pattern: Data → Agent1 → Agent2 → Agent3 → Synthesizer → Final Output
"""

import os
import json
import logging
import asyncio
from typing import Dict, List, Optional
from dataclasses import dataclass
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)


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


class CrewEngine:
    """Orchestrates multiple AI agents in sequence, passing context forward."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("EMERGENT_LLM_KEY", "")

    async def _run_agent(self, agent: AgentConfig, task_prompt: str,
                         prior_context: str = "", session_suffix: str = "") -> TaskResult:
        """Execute a single agent's task with optional prior context from other agents."""
        system_message = (
            f"You are a {agent.role}.\n"
            f"Your goal: {agent.goal}\n"
            f"Background: {agent.backstory}\n\n"
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

        try:
            session_id = f"crew_{agent.role.replace(' ', '_')}_{session_suffix}"
            chat = LlmChat(
                api_key=self.api_key,
                session_id=session_id,
                system_message=system_message
            ).with_model(agent.model_provider, agent.model_name)

            response = await chat.send_message(UserMessage(text=full_prompt))
            text = response.strip() if isinstance(response, str) else str(response).strip()
            return TaskResult(agent_role=agent.role, output=text, success=True)

        except Exception as e:
            logger.error(f"Agent '{agent.role}' failed: {e}")
            return TaskResult(agent_role=agent.role, output=f"Agent error: {e}", success=False)

    async def run_crew(self, agents: List[AgentConfig], tasks: List[str],
                       synthesizer: AgentConfig, synth_prompt: str,
                       session_suffix: str = "") -> Dict:
        """
        Run a sequential crew: each agent gets the accumulated context
        from all prior agents, then a synthesizer combines everything.

        Args:
            agents: List of specialized agents
            tasks: List of task prompts (one per agent)
            synthesizer: Final agent that combines all findings
            synth_prompt: Synthesis task prompt template
            session_suffix: Unique suffix for session tracking
        Returns:
            Dict with individual agent outputs and final synthesis
        """
        accumulated_context = ""
        agent_results = []

        # Run agents sequentially — each sees prior findings
        for agent, task in zip(agents, tasks):
            result = await self._run_agent(
                agent, task, accumulated_context, session_suffix
            )
            agent_results.append(result)
            if result.success:
                accumulated_context += f"\n\n--- {agent.role} Analysis ---\n{result.output}"

        # Synthesizer combines all findings
        synth_result = await self._run_agent(
            synthesizer, synth_prompt, accumulated_context, session_suffix
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

    async def run_parallel_crew(self, agents: List[AgentConfig], tasks: List[str],
                                synthesizer: AgentConfig, synth_prompt: str,
                                session_suffix: str = "") -> Dict:
        """
        Run agents in parallel using thread pool (since LLM calls may block),
        then synthesize. Faster but agents don't see each other's output.
        """
        import concurrent.futures

        loop = asyncio.get_event_loop()

        # Use a thread pool to run agents truly in parallel
        def _run_sync(agent, task):
            return asyncio.run(self._run_agent(agent, task, "", session_suffix))

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as pool:
            futures = [
                loop.run_in_executor(pool, _run_sync, agent, task)
                for agent, task in zip(agents, tasks)
            ]
            agent_results = await asyncio.gather(*futures, return_exceptions=True)

        # Handle exceptions
        clean_results = []
        accumulated_context = ""
        for i, r in enumerate(agent_results):
            if isinstance(r, Exception):
                tr = TaskResult(agent_role=agents[i].role, output=f"Error: {r}", success=False)
            else:
                tr = r
            clean_results.append(tr)
            if tr.success:
                accumulated_context += f"\n\n--- {tr.agent_role} Analysis ---\n{tr.output}"

        # Synthesizer combines all parallel findings
        synth_result = await self._run_agent(
            synthesizer, synth_prompt, accumulated_context, session_suffix
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
