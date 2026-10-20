# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from dataclasses import dataclass
import genlayer as gl
from genlayer import *


@allow_storage
@dataclass
class BountyPool:
    id: u256
    sponsor: Address
    project_name: str
    codebase_url: str
    scope_description: str
    critical_reward: u256
    high_reward: u256
    medium_reward: u256
    low_reward: u256
    total_funded: u256
    total_paid: u256
    active: bool


@allow_storage
@dataclass
class VulnerabilityReport:
    id: u256
    pool_id: u256
    researcher: Address
    report_title: str
    poc_url: str
    claimed_severity: str       # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"
    status: str                 # "SUBMITTED" | "TRIAGED" | "PAID" | "REJECTED_SPAM" | "REJECTED_OUT_OF_SCOPE" | "CANCELLED" | "CANCELLED_POOL_CLOSED"
    stake_amount: u256
    awarded_severity: str       # "NONE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    awarded_amount: u256
    triage_reasoning: str
    triaged_timestamp: str


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass
    class Write:
        pass


class AutonomousBountyOracle(gl.Contract):
    """
    AutonomousBountyOracle: A decentralized bug bounty triage & automated settlement primitive.
    Leverages GenLayer AI-consensus to independently verify vulnerability reports, calculate
    CVSS severity, and trigger autonomous on-chain payouts without centralized triage intermediaries.
    """
    bounty_pools: TreeMap[u256, BountyPool]
    reports: TreeMap[u256, VulnerabilityReport]
    next_pool_id: u256
    next_report_id: u256
    min_researcher_stake: u256

    def __init__(self, min_stake: u256 = u256(100)) -> None:
        self.next_pool_id = u256(0)
        self.next_report_id = u256(0)
        self.min_researcher_stake = min_stake

    @gl.public.write.payable
    def create_bounty_pool(
        self,
        project_name: str,
        codebase_url: str,
        scope_description: str,
        critical_reward: u256,
        high_reward: u256,
        medium_reward: u256,
        low_reward: u256
    ) -> u256:
        """
        Creates and funds a new autonomous bug bounty program.
        """
        if not project_name or not codebase_url or not scope_description:
            raise gl.vm.UserError("Project details and scope description cannot be empty")

        if critical_reward < high_reward or high_reward < medium_reward or medium_reward < low_reward:
            raise gl.vm.UserError("Reward hierarchy must satisfy: Critical >= High >= Medium >= Low")

        if low_reward == u256(0):
            raise gl.vm.UserError("Lowest tier reward must be greater than zero")

        deposited_funds = gl.message.value
        if deposited_funds < critical_reward:
            raise gl.vm.UserError("Initial funding must cover at least one Critical reward payout")

        pool_id = self.next_pool_id
        self.next_pool_id = self.next_pool_id + u256(1)

        new_pool = BountyPool(
            id=pool_id,
            sponsor=gl.message.sender_address,
            project_name=project_name,
            codebase_url=codebase_url,
            scope_description=scope_description,
            critical_reward=critical_reward,
            high_reward=high_reward,
            medium_reward=medium_reward,
            low_reward=low_reward,
            total_funded=deposited_funds,
            total_paid=u256(0),
            active=True
        )

        self.bounty_pools[pool_id] = new_pool
        return pool_id

    @gl.public.write.payable
    def top_up_pool(self, pool_id: u256) -> None:
        """
        Allows sponsor or contributors to deposit additional funds into the bounty pool.
        """
        if pool_id not in self.bounty_pools:
            raise gl.vm.UserError("Bounty pool not found")

        pool = self.bounty_pools[pool_id]
        if not pool.active:
            raise gl.vm.UserError("Cannot fund an inactive bounty pool")

        deposit_amount = gl.message.value
        if deposit_amount == u256(0):
            raise gl.vm.UserError("Deposit amount must be greater than zero")

        pool.total_funded = pool.total_funded + deposit_amount
        self.bounty_pools[pool_id] = pool

    @gl.public.write.payable
    def submit_vulnerability(
        self,
        pool_id: u256,
        report_title: str,
        poc_url: str,
        claimed_severity: str
    ) -> u256:
        """
        Submits a vulnerability report with an anti-spam stake deposit.
        """
        if pool_id not in self.bounty_pools:
            raise gl.vm.UserError("Bounty pool not found")

        pool = self.bounty_pools[pool_id]
        if not pool.active:
            raise gl.vm.UserError("Bounty pool is not active")

        if not report_title or not poc_url:
            raise gl.vm.UserError("Report title and PoC URL are required")

        valid_severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        norm_severity = claimed_severity.upper().strip()
        if norm_severity not in valid_severities:
            raise gl.vm.UserError("Claimed severity must be CRITICAL, HIGH, MEDIUM, or LOW")

        stake = gl.message.value
        if stake < self.min_researcher_stake:
            raise gl.vm.UserError("Submitted stake is below minimum anti-spam requirement")

        report_id = self.next_report_id
        self.next_report_id = self.next_report_id + u256(1)

        new_report = VulnerabilityReport(
            id=report_id,
            pool_id=pool_id,
            researcher=gl.message.sender_address,
            report_title=report_title,
            poc_url=poc_url,
            claimed_severity=norm_severity,
            status="SUBMITTED",
            stake_amount=stake,
            awarded_severity="NONE",
            awarded_amount=u256(0),
            triage_reasoning="",
            triaged_timestamp=""
        )

        self.reports[report_id] = new_report
        return report_id

    @gl.public.write
    def triage_and_settle(self, report_id: u256) -> None:
        """
        Executes decentralized AI-validator consensus triage.
        Inspects the live codebase scope and PoC URL, determines legitimacy and CVSS severity,
        and automatically settles the bounty payout and stake refund.
        """
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")

        report = self.reports[report_id]
        if report.status != "SUBMITTED":
            raise gl.vm.UserError("Report has already been triaged")

        pool = self.bounty_pools[report.pool_id]
        if not pool.active:
            # Prevent stranding stake if pool became inactive: automatically refund researcher stake
            stake = report.stake_amount
            report.status = "CANCELLED_POOL_CLOSED"
            report.triage_reasoning = "Parent bounty pool is inactive; stake refunded."
            self.reports[report_id] = report
            if stake > u256(0):
                _Recipient(report.researcher).emit_transfer(value=stake)
            return

        codebase_url = pool.codebase_url
        scope_desc = pool.scope_description
        poc_url = report.poc_url
        report_title = report.report_title
        claimed_sev = report.claimed_severity

        # GenLayer Consensus Equivalence Principle Block
        def evaluate_vulnerability() -> str:
            # 1. Fetch live PoC artifact / advisory text via nondet web
            poc_content = ""
            if poc_url.startswith("http://") or poc_url.startswith("https://"):
                try:
                    poc_content = gl.nondet.web.render(poc_url, mode="text")
                except Exception as err:
                    poc_content = f"Failed to fetch PoC: {str(err)}"

            # 2. Fetch codebase reference or spec if available
            codebase_content = ""
            if codebase_url.startswith("http://") or codebase_url.startswith("https://"):
                try:
                    codebase_content = gl.nondet.web.render(codebase_url, mode="text")
                except Exception as err:
                    codebase_content = f"Codebase reference: {str(err)}"

            task = f"""
            You are an expert Web3 Security Auditor & Triage Validator.
            Evaluate this bug report against the project scope:

            PROJECT NAME: {pool.project_name}
            IN-SCOPE SPECIFICATION: {scope_desc}
            CODEBASE SUMMARY / LINK: {codebase_url} ({codebase_content[:800]})
            REPORT TITLE: {report_title}
            CLAIMED SEVERITY: {claimed_sev}
            POC URL: {poc_url}
            POC DELIVERABLE CONTENT: {poc_content[:1500]}

            Rules:
            1. Determine if the report is VALID (reproducible vulnerability in-scope), OUT_OF_SCOPE, or SPAM/INVALID.
            2. If VALID, assign verified severity: "CRITICAL", "HIGH", "MEDIUM", or "LOW".
            3. Return strict JSON format:
            {{
                "verdict": "VALID" | "OUT_OF_SCOPE" | "SPAM",
                "verified_severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "NONE",
                "reasoning": "Concise justification under 200 chars"
            }}
            """
            response = gl.nondet.exec_prompt(task, response_format="json")
            return json.dumps(response, sort_keys=True)

        result_str = gl.eq_principle.strict_eq(evaluate_vulnerability)
        result = json.loads(result_str)

        verdict = str(result.get("verdict", "SPAM")).upper().strip()
        verified_sev = str(result.get("verified_severity", "NONE")).upper().strip()
        reasoning = str(result.get("reasoning", "Triage completed via GenLayer AI consensus"))

        payout = u256(0)
        researcher = report.researcher
        stake = report.stake_amount

        if verdict == "VALID" and verified_sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            # Calculate reward based on pool configuration
            if verified_sev == "CRITICAL":
                payout = pool.critical_reward
            elif verified_sev == "HIGH":
                payout = pool.high_reward
            elif verified_sev == "MEDIUM":
                payout = pool.medium_reward
            elif verified_sev == "LOW":
                payout = pool.low_reward

            # Cap payout to available balance
            available_balance = pool.total_funded - pool.total_paid
            if payout > available_balance:
                payout = available_balance

            # Transfer payout + refund stake to researcher
            total_transfer = payout + stake
            if total_transfer > u256(0):
                _Recipient(researcher).emit_transfer(value=total_transfer)

            pool.total_paid = pool.total_paid + payout
            report.status = "PAID"
            report.awarded_severity = verified_sev
            report.awarded_amount = payout
            report.triage_reasoning = reasoning

        elif verdict == "OUT_OF_SCOPE":
            # Return stake to researcher, but zero bounty payout
            if stake > u256(0):
                _Recipient(researcher).emit_transfer(value=stake)

            report.status = "REJECTED_OUT_OF_SCOPE"
            report.awarded_severity = "NONE"
            report.awarded_amount = u256(0)
            report.triage_reasoning = reasoning

        else: # SPAM / MALICIOUS
            # Slash anti-spam stake: Stake is transferred to the pool sponsor
            sponsor = pool.sponsor
            if stake > u256(0):
                _Recipient(sponsor).emit_transfer(value=stake)

            report.status = "REJECTED_SPAM"
            report.awarded_severity = "NONE"
            report.awarded_amount = u256(0)
            report.triage_reasoning = f"Slashed: {reasoning}"

        # Persist updated state
        self.bounty_pools[report.pool_id] = pool
        self.reports[report_id] = report

    @gl.public.write
    def cancel_report(self, report_id: u256) -> None:
        """
        Allows a researcher to cancel their pending report and reclaim their anti-spam stake
        if the report has not yet been triaged, or if the parent pool was deactivated.
        """
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")

        report = self.reports[report_id]
        if report.researcher != gl.message.sender_address:
            raise gl.vm.UserError("Only the reporting researcher can cancel this report")

        if report.status != "SUBMITTED":
            raise gl.vm.UserError("Only SUBMITTED reports can be cancelled")

        stake = report.stake_amount
        report.status = "CANCELLED"
        report.triage_reasoning = "Report cancelled by researcher; stake refunded."
        self.reports[report_id] = report

        if stake > u256(0):
            _Recipient(report.researcher).emit_transfer(value=stake)

    @gl.public.write
    def close_bounty_pool(self, pool_id: u256) -> None:
        """
        Allows the sponsor to close the bounty pool and withdraw remaining unspent funds.
        Atomically resolves and refunds every pending researcher stake so no funds are stranded.
        """
        if pool_id not in self.bounty_pools:
            raise gl.vm.UserError("Bounty pool not found")

        pool = self.bounty_pools[pool_id]
        if pool.sponsor != gl.message.sender_address:
            raise gl.vm.UserError("Only pool sponsor can close the bounty pool")

        if not pool.active:
            raise gl.vm.UserError("Bounty pool is already closed")

        # Atomically refund all pending reports associated with this pool
        for r_id in range(int(self.next_report_id)):
            report = self.reports[u256(r_id)]
            if report.pool_id == pool_id and report.status == "SUBMITTED":
                stake = report.stake_amount
                report.status = "CANCELLED_POOL_CLOSED"
                report.triage_reasoning = "Bounty pool closed by sponsor; stake refunded to researcher."
                self.reports[u256(r_id)] = report
                if stake > u256(0):
                    _Recipient(report.researcher).emit_transfer(value=stake)

        remaining_balance = pool.total_funded - pool.total_paid
        pool.active = False
        self.bounty_pools[pool_id] = pool

        if remaining_balance > u256(0):
            _Recipient(pool.sponsor).emit_transfer(value=remaining_balance)

    @gl.public.view
    def get_pool(self, pool_id: u256) -> BountyPool:
        if pool_id not in self.bounty_pools:
            raise gl.vm.UserError("Bounty pool not found")
        return self.bounty_pools[pool_id]

    @gl.public.view
    def get_report(self, report_id: u256) -> VulnerabilityReport:
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")
        return self.reports[report_id]
