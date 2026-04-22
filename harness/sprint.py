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
    """Generate sprint.md (if missing) and derive sprint_contract.md from it."""
    logger.info("Sprint contract generation phase")
    sprint_path = workspace / config.SPRINT_FILE
    
    # Create sprint.md if it doesn't exist using the SprintPlanner agent
    if not sprint_path.exists():
        logger.info("sprint.md not found — invoking SprintPlanner to create it")
        sprint_planner.run(
            f"Round {round_num}: Read {config.SPEC_FILE} and {config.CONTRACT_FILE}, "
            f"then create the sprint plan and save it to {config.SPRINT_FILE}. "
            f"This is a fresh project — create the first sprint."
        )
        if not sprint_path.exists():
            logger.warning("SprintPlanner did not create sprint.md — skipping per-sprint contract")
            return
        logger.info("sprint.md created by SprintPlanner")
    
    # Create sprint_contract.md from sprint.md using a dedicated contract writer
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
    """Negotiate acceptance criteria contract based on spec.md.
    
    Uses a proposer agent to draft the contract and a reviewer to validate it.
    Falls back to spec.md if contract generation fails after 3 attempts.
    """
    contract_path = workspace / config.CONTRACT_FILE
    if contract_path.exists():
        logger.info("Contract already exists, skipping negotiation")
        return
    
    from agents import Agent
    
    proposer = Agent("ContractProposer", SPRINT_CONTRACT_BUILDER_SYSTEM, TOOL_SCHEMAS, logger=logger)
    
    for attempt in range(3):
        logger.info(f"Contract negotiation attempt {attempt + 1}/3")
        proposer.run(
            f"Read {config.SPEC_FILE} and create detailed acceptance criteria in {config.CONTRACT_FILE}. "
            f"Include functional requirements, quality standards, and scoring criteria."
        )
        if contract_path.exists():
            logger.info("Contract created successfully")
            return
        logger.warning(f"Contract not created on attempt {attempt + 1}")
    
    # Fallback: create a minimal contract from spec
    logger.warning("Max contract negotiation attempts reached, creating fallback contract")
    spec_path = workspace / config.SPEC_FILE
    if spec_path.exists():
        spec_content = spec_path.read_text(encoding="utf-8")
        fallback = f"""# Acceptance Criteria (Fallback)

Generated from {config.SPEC_FILE}.

## Functional Requirements
- All features described in the spec must be implemented
- The application must build without errors
- The application must run without runtime errors

## Source Specification
{spec_content[:5000]}

## Scoring
- 10/10: All features implemented, no bugs, excellent UX
- 7-9/10: Most features implemented, minor issues
- 4-6/10: Some features implemented, notable issues
- 1-3/10: Few features implemented, major issues
- 0/10: Non-functional or critically broken
"""
        contract_path.write_text(fallback, encoding="utf-8")
        logger.info("Fallback contract created from spec")