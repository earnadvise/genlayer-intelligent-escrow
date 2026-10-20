import json
import pytest
from genlayer import *


def test_bounty_pool_creation_and_funding(direct_deploy, direct_vm):
    """
    Tests pool creation, reward structure validation, and initial funding requirements.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")

    direct_vm.sender = sponsor
    direct_vm.value = u256(10000)

    pool_id = oracle.create_bounty_pool(
        "GenLayer Core Protocol",
        "https://github.com/genlayer/genlayer-core",
        "In-scope: GenVM, Consensus engine, and State storage. Out of scope: Docs and UI typos.",
        u256(5000),  # Critical
        u256(2500),  # High
        u256(1000),  # Medium
        u256(250),   # Low
    )
    assert pool_id == u256(0)

    pool = oracle.get_pool(pool_id)
    assert pool.sponsor == sponsor
    assert pool.project_name == "GenLayer Core Protocol"
    assert pool.total_funded == u256(10000)
    assert pool.total_paid == u256(0)
    assert pool.active is True


def test_valid_critical_vulnerability_triage_and_payout(direct_deploy, direct_vm):
    """
    Tests valid Critical report triage:
    - Researcher submits report with anti-spam stake.
    - AI consensus validates PoC and assigns CRITICAL severity.
    - Contract releases 5,000 WEI bounty + 100 WEI stake refund to researcher.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")
    whitehat = Address("0x0000000000000000000000000000000000000099")

    # 1. Sponsor creates pool with 10,000 WEI
    direct_vm.sender = sponsor
    direct_vm.value = u256(10000)
    pool_id = oracle.create_bounty_pool(
        "DeFi Vault",
        "https://github.com/example/vault",
        "Reentrancy, unauthorized withdrawals, and state corruption.",
        u256(5000),
        u256(2000),
        u256(500),
        u256(100)
    )
    direct_vm.value = u256(0)

    # 2. Whitehat submits vulnerability with 100 WEI stake
    direct_vm.sender = whitehat
    direct_vm.value = u256(100)
    report_id = oracle.submit_vulnerability(
        pool_id,
        "Zero-day Reentrancy in Flash Loan Callback",
        "https://gist.github.com/whitehat/poc-reentrancy",
        "CRITICAL"
    )
    assert report_id == u256(0)
    direct_vm.value = u256(0)

    report = oracle.get_report(report_id)
    assert report.status == "SUBMITTED"
    assert report.stake_amount == u256(100)

    # 3. Mock AI consensus response
    direct_vm.mock_web(r".*poc-reentrancy", {"status": 200, "body": "PoC: Exploit script drains vault via reentrancy callback."})
    direct_vm.mock_web(r".*vault", {"status": 200, "body": "Vault Contract: deposit, withdraw, flashLoan."})

    decision = json.dumps({
        "verdict": "VALID",
        "verified_severity": "CRITICAL",
        "reasoning": "Reproducible zero-day draining funds via non-reentrant missing guard."
    })
    direct_vm.mock_llm(r".*Triage Validator.*", decision)

    # 4. Triage and settle
    oracle.triage_and_settle(report_id)

    # 5. Verify results
    updated_report = oracle.get_report(report_id)
    assert updated_report.status == "PAID"
    assert updated_report.awarded_severity == "CRITICAL"
    assert updated_report.awarded_amount == u256(5000)

    updated_pool = oracle.get_pool(pool_id)
    assert updated_pool.total_paid == u256(5000)


def test_out_of_scope_report_stake_refund(direct_deploy, direct_vm):
    """
    Tests that out-of-scope reports receive no payout, but have their anti-spam stake safely returned.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")
    whitehat = Address("0x0000000000000000000000000000000000000099")

    direct_vm.sender = sponsor
    direct_vm.value = u256(6000)
    pool_id = oracle.create_bounty_pool(
        "Protocol", "https://example.com", "Core smart contracts only.",
        u256(4000), u256(2000), u256(500), u256(100)
    )

    direct_vm.sender = whitehat
    direct_vm.value = u256(100)
    report_id = oracle.submit_vulnerability(
        pool_id, "Missing Favicon in Landing Page", "https://example.com/ui-bug", "LOW"
    )

    direct_vm.mock_web(r".*ui-bug", {"status": 200, "body": "404 favicon"})
    decision = json.dumps({
        "verdict": "OUT_OF_SCOPE",
        "verified_severity": "NONE",
        "reasoning": "UI asset is outside core smart contracts scope."
    })
    direct_vm.mock_llm(r".*Triage Validator.*", decision)

    oracle.triage_and_settle(report_id)

    report = oracle.get_report(report_id)
    assert report.status == "REJECTED_OUT_OF_SCOPE"
    assert report.awarded_amount == u256(0)


def test_spam_report_stake_slashed(direct_deploy, direct_vm):
    """
    Tests that malicious or spam reports have their stake slashed and sent to the sponsor.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")
    spammer = Address("0x0000000000000000000000000000000000000066")

    direct_vm.sender = sponsor
    direct_vm.value = u256(5000)
    pool_id = oracle.create_bounty_pool(
        "Bridge", "https://example.com", "Bridge contracts.",
        u256(3000), u256(1500), u256(400), u256(100)
    )

    direct_vm.sender = spammer
    direct_vm.value = u256(100)
    report_id = oracle.submit_vulnerability(
        pool_id, "Gibberish spam text", "https://example.com/nonsense", "CRITICAL"
    )

    direct_vm.mock_web(r".*nonsense", {"status": 404, "body": "Not found"})
    decision = json.dumps({
        "verdict": "SPAM",
        "verified_severity": "NONE",
        "reasoning": "Fake report with broken link and non-existent exploit."
    })
    direct_vm.mock_llm(r".*Triage Validator.*", decision)

    oracle.triage_and_settle(report_id)

    report = oracle.get_report(report_id)
    assert report.status == "REJECTED_SPAM"
    assert "Slashed" in report.triage_reasoning


def test_pool_top_up_and_closure(direct_deploy, direct_vm):
    """
    Tests pool top-ups and sponsor withdrawal upon pool closure.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")
    other = Address("0x0000000000000000000000000000000000000077")

    direct_vm.sender = sponsor
    direct_vm.value = u256(5000)
    pool_id = oracle.create_bounty_pool(
        "AMM", "https://example.com", "Swap math.",
        u256(4000), u256(2000), u256(500), u256(100)
    )

    # Top up pool with additional 2000 WEI
    direct_vm.sender = other
    direct_vm.value = u256(2000)
    oracle.top_up_pool(pool_id)

    pool = oracle.get_pool(pool_id)
    assert pool.total_funded == u256(7000)

    # Sponsor closes pool
    direct_vm.sender = sponsor
    oracle.close_bounty_pool(pool_id)

    closed_pool = oracle.get_pool(pool_id)
    assert closed_pool.active is False

    # Attempting to submit report on closed pool should fail
    direct_vm.sender = other
    direct_vm.value = u256(100)
    with pytest.raises(Exception):
        oracle.submit_vulnerability(pool_id, "Bug", "https://example.com/p", "LOW")


def test_pool_closure_atomically_refunds_pending_stakes(direct_deploy, direct_vm):
    """
    Tests that closing a bounty pool with pending SUBMITTED reports atomically
    refunds all researcher stakes without stranding funds.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")
    whitehat1 = Address("0x0000000000000000000000000000000000000091")
    whitehat2 = Address("0x0000000000000000000000000000000000000092")

    # 1. Sponsor creates pool
    direct_vm.sender = sponsor
    direct_vm.value = u256(10000)
    pool_id = oracle.create_bounty_pool(
        "Bridge", "https://example.com/code", "Bridge contracts.",
        u256(5000), u256(2000), u256(500), u256(100)
    )

    # 2. Two researchers submit reports with stakes
    direct_vm.sender = whitehat1
    direct_vm.value = u256(150)
    r1 = oracle.submit_vulnerability(pool_id, "Vuln 1", "https://example.com/p1", "HIGH")

    direct_vm.sender = whitehat2
    direct_vm.value = u256(200)
    r2 = oracle.submit_vulnerability(pool_id, "Vuln 2", "https://example.com/p2", "CRITICAL")

    assert oracle.get_report(r1).status == "SUBMITTED"
    assert oracle.get_report(r2).status == "SUBMITTED"

    # 3. Sponsor closes pool while reports are pending
    direct_vm.sender = sponsor
    oracle.close_bounty_pool(pool_id)

    # 4. Verify pool is inactive and all pending reports are atomically refunded
    assert oracle.get_pool(pool_id).active is False

    rep1 = oracle.get_report(r1)
    assert rep1.status == "CANCELLED_POOL_CLOSED"
    assert "stake refunded" in rep1.triage_reasoning

    rep2 = oracle.get_report(r2)
    assert rep2.status == "CANCELLED_POOL_CLOSED"
    assert "stake refunded" in rep2.triage_reasoning


def test_researcher_can_cancel_submitted_report_and_reclaim_stake(direct_deploy, direct_vm):
    """
    Tests that a researcher can voluntarily cancel their pending report and reclaim their stake.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")
    whitehat = Address("0x0000000000000000000000000000000000000099")
    imposter = Address("0x0000000000000000000000000000000000000044")

    direct_vm.sender = sponsor
    direct_vm.value = u256(8000)
    pool_id = oracle.create_bounty_pool(
        "Oracle", "https://example.com/code", "Price feed.",
        u256(5000), u256(2000), u256(500), u256(100)
    )

    direct_vm.sender = whitehat
    direct_vm.value = u256(100)
    r_id = oracle.submit_vulnerability(pool_id, "Stale Price", "https://example.com/p", "MEDIUM")

    # Imposter cannot cancel
    direct_vm.sender = imposter
    with pytest.raises(Exception):
        oracle.cancel_report(r_id)

    # True researcher cancels
    direct_vm.sender = whitehat
    oracle.cancel_report(r_id)

    rep = oracle.get_report(r_id)
    assert rep.status == "CANCELLED"
    assert "cancelled by researcher" in rep.triage_reasoning.lower()


def test_triage_on_inactive_pool_refunds_stake(direct_deploy, direct_vm):
    """
    Tests that calling triage_and_settle on an inactive pool safely refunds
    the researcher's stake rather than reverting and locking it.
    """
    oracle = direct_deploy("contracts/AutonomousBountyOracle.py", min_stake=u256(100))
    sponsor = Address("0x0000000000000000000000000000000000000010")
    whitehat = Address("0x0000000000000000000000000000000000000099")

    direct_vm.sender = sponsor
    direct_vm.value = u256(5000)
    pool_id = oracle.create_bounty_pool(
        "Protocol", "https://example.com/code", "Scope.",
        u256(4000), u256(2000), u256(500), u256(100)
    )

    direct_vm.sender = whitehat
    direct_vm.value = u256(100)
    r_id = oracle.submit_vulnerability(pool_id, "Bug", "https://example.com/p", "LOW")

    # Simulate pool deactivated directly or externally
    pool = oracle.get_pool(pool_id)
    pool.active = False
    oracle.bounty_pools[pool_id] = pool

    # Triage does not revert; it refunds the stake
    oracle.triage_and_settle(r_id)

    rep = oracle.get_report(r_id)
    assert rep.status == "CANCELLED_POOL_CLOSED"
    assert "stake refunded" in rep.triage_reasoning
