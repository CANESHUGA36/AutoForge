"""
Sprint     +       
"""
from __future__ import annotations
import logging
from pathlib import Path
import config
from agents import Agent
from prompts import SPRINT_PLANNER_SYSTEM, SPRINT_CONTRACT_BUILDER_SYSTEM
from tools_impl import TOOL_SCHEMAS
log = logging.getLogger("harness")
def plan_sprint(workspace: Path, round_num: int, sprint_planner: Agent, logger: logging.Logger) -> None:
    """"""
    logger.info("Sprint contract generation phase")
    sprint_path = workspace / config.SPRINT_FILE
    if not sprint_path.exists():
        logger.warning("sprint.md not found  ?skipping per-sprint contract")
        return
    writer = Agent("SprintContractWriter", SPRINT_CONTRACT_BUILDER_SYSTEM, TOOL_SCHEMAS, logger=logger)
    writer.run(
        f"Round {round_num}: Read {config.SPRINT_FILE} and {config.CONTRACT_FILE}, "
        f"then write the sprint contract to {config.SPRINT_CONTRACT_FILE}."
    )
    sprint_contract_path = workspace / config.SPRINT_CONTRACT_FILE
    if sprint_contract_path.exists():
        logger.info("Sprint contract created")
    else:
        logger.warning("SprintContractWriter did not create sprint_contract.md")
def negotiate_contract(workspace: Path, round_num: int, logger: logging.Logger) -> None:
    """"""