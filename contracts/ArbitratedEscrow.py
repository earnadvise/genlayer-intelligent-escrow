# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

import json
from dataclasses import dataclass
import genlayer as gl
from genlayer import *


@allow_storage
@dataclass
class EscrowRecord:
    id: u256
    buyer: Address
    seller: Address
    amount: u256
    status: str  # "AWAITING_DEPOSIT" | "ESCROWED" | "DELIVERED" | "DISPUTED" | "RESOLVED" | "REFUNDED"
    agreement_desc: str
    delivery_artifact: str
    dispute_buyer_statement: str
    dispute_seller_statement: str
    resolution_reason: str
    payout_seller_percent: u256


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass
    class Write:
        pass


class ArbitratedEscrow(gl.contract.Contract):
    escrows: TreeMap[u256, EscrowRecord]
    next_escrow_id: u256

    def __init__(self) -> None:
        self.next_escrow_id = u256(0)

    @gl.public.write
    def create_escrow(self, seller_address: str, agreement_desc: str) -> u256:
        """
        Creates a new escrow record.
        """
        escrow_id = self.next_escrow_id
        self.next_escrow_id = self.next_escrow_id + u256(1)

        new_escrow = EscrowRecord(
            id=escrow_id,
            buyer=gl.message.sender_address,
            seller=Address(seller_address),
            amount=u256(0),
            status="AWAITING_DEPOSIT",
            agreement_desc=agreement_desc,
            delivery_artifact="",
            dispute_buyer_statement="",
            dispute_seller_statement="",
            resolution_reason="",
            payout_seller_percent=u256(0)
        )
        self.escrows[escrow_id] = new_escrow
        return escrow_id

    @gl.public.write.payable
    def deposit(self, escrow_id: u256) -> None:
        """
        Deposits the contract funds (GEN) into the escrow.
        """
        if escrow_id not in self.escrows:
            raise gl.vm.UserError("Escrow not found")

        escrow = self.escrows[escrow_id]

        if escrow.buyer != gl.message.sender_address:
            raise gl.vm.UserError("Only the buyer can deposit funds")

        if escrow.status != "AWAITING_DEPOSIT":
            raise gl.vm.UserError("Escrow is not awaiting deposit")

        if gl.message.value == u256(0):
            raise gl.vm.UserError("Deposit value must be greater than 0")

        escrow.amount = gl.message.value
        escrow.status = "ESCROWED"
        self.escrows[escrow_id] = escrow

    @gl.public.write
    def submit_delivery(self, escrow_id: u256, delivery_artifact: str) -> None:
        """
        Allows the seller to submit their delivery (e.g., project URL, IPFS hash, or file contents).
        """
        if escrow_id not in self.escrows:
            raise gl.vm.UserError("Escrow not found")

        escrow = self.escrows[escrow_id]

        if escrow.seller != gl.message.sender_address:
            raise gl.vm.UserError("Only the seller can submit delivery")

        if escrow.status != "ESCROWED":
            raise gl.vm.UserError("Escrow is not in active escrowed state")

        escrow.delivery_artifact = delivery_artifact
        escrow.status = "DELIVERED"
        self.escrows[escrow_id] = escrow

    @gl.public.write
    def approve_delivery(self, escrow_id: u256) -> None:
        """
        Allows the buyer to approve the delivery, releasing 100% of the funds to the seller.
        """
        if escrow_id not in self.escrows:
            raise gl.vm.UserError("Escrow not found")

        escrow = self.escrows[escrow_id]

        if escrow.buyer != gl.message.sender_address:
            raise gl.vm.UserError("Only the buyer can approve delivery")

        if escrow.status not in ["ESCROWED", "DELIVERED"]:
            raise gl.vm.UserError("Escrow cannot be approved in current status")

        amount = escrow.amount
        seller_addr = escrow.seller

        # Transfer funds to the seller
        _Recipient(seller_addr).emit_transfer(value=amount)

        escrow.status = "RESOLVED"
        escrow.payout_seller_percent = u256(100)
        escrow.resolution_reason = "Buyer approved delivery"
        escrow.amount = u256(0)
        self.escrows[escrow_id] = escrow

    @gl.public.write
    def refund_buyer(self, escrow_id: u256) -> None:
        """
        Allows the seller to voluntarily refund the buyer (e.g., if they cannot fulfill the contract).
        """
        if escrow_id not in self.escrows:
            raise gl.vm.UserError("Escrow not found")

        escrow = self.escrows[escrow_id]

        if escrow.seller != gl.message.sender_address:
            raise gl.vm.UserError("Only the seller can voluntarily refund the buyer")

        if escrow.status not in ["ESCROWED", "DELIVERED"]:
            raise gl.vm.UserError("Escrow cannot be refunded in current status")

        amount = escrow.amount
        buyer_addr = escrow.buyer

        # Refund the buyer
        _Recipient(buyer_addr).emit_transfer(value=amount)

        escrow.status = "REFUNDED"
        escrow.payout_seller_percent = u256(0)
        escrow.resolution_reason = "Seller voluntarily refunded the buyer"
        escrow.amount = u256(0)
        self.escrows[escrow_id] = escrow

    @gl.public.write
    def dispute_escrow(self, escrow_id: u256, statement: str) -> None:
        """
        Allows either buyer or seller to flag the escrow as disputed and submit their statements.
        """
        if escrow_id not in self.escrows:
            raise gl.vm.UserError("Escrow not found")

        escrow = self.escrows[escrow_id]
        sender = gl.message.sender_address

        if sender != escrow.buyer and sender != escrow.seller:
            raise gl.vm.UserError("Only the buyer or seller can dispute the escrow")

        if escrow.status not in ["ESCROWED", "DELIVERED", "DISPUTED"]:
            raise gl.vm.UserError("Escrow cannot be disputed in current status")

        if sender == escrow.buyer:
            escrow.dispute_buyer_statement = statement
        else:
            escrow.dispute_seller_statement = statement

        escrow.status = "DISPUTED"
        self.escrows[escrow_id] = escrow

    @gl.public.write
    def adjudicate_escrow(self, escrow_id: u256) -> None:
        """
        Resolves a disputed escrow using the AI-Validator consensus mechanism.
        """
        if escrow_id not in self.escrows:
            raise gl.vm.UserError("Escrow not found")

        escrow = self.escrows[escrow_id]

        if escrow.status != "DISPUTED":
            raise gl.vm.UserError("Escrow is not in disputed state")

        if not escrow.dispute_buyer_statement and not escrow.dispute_seller_statement:
            raise gl.vm.UserError("At least one party must submit a dispute statement")

        # Compile inputs for the non-deterministic block
        agreement = escrow.agreement_desc
        artifact = escrow.delivery_artifact
        buyer_stmt = escrow.dispute_buyer_statement
        seller_stmt = escrow.dispute_seller_statement

        def resolve_dispute() -> str:
            # We can attempt to render the artifact if it's a URL
            web_content = ""
            if artifact.startswith("http://") or artifact.startswith("https://"):
                try:
                    web_content = gl.nondet.web.render(artifact, mode="text")
                except Exception as e:
                    web_content = f"Error loading artifact webpage: {str(e)}"

            task = f"""
            You are an independent, neutral decentralized arbitrator. Review the original contract agreement, the seller's submitted delivery artifact (and web content if retrieved), and the statements from both the buyer and seller regarding the dispute.
            
            CONTRACT AGREEMENT:
            {agreement}
            
            DELIVERY ARTIFACT SUBMITTED BY SELLER:
            {artifact}
            
            WEB CONTENT OF ARTIFACT (IF APPLICABLE):
            {web_content}
            
            BUYER STATEMENT:
            {buyer_stmt}
            
            SELLER STATEMENT:
            {seller_stmt}
            
            Determine who is in the right and allocate the percentage of funds (from 0 to 100) to be paid to the Seller, with the remainder refunded to the Buyer.
            Format the output strictly as a JSON object:
            {{
                "payment_to_seller_percentage": int, // Integer from 0 to 100
                "reasoning": str // Detailed reasoning for your decision
            }}
            It is mandatory that you respond only using the JSON format above. Don't include any other words or characters, your output must be only JSON without any formatting prefix or suffix.
            """
            result = gl.nondet.exec_prompt(task, response_format="json")
            return json.dumps(result, sort_keys=True)

        # Call the Equivalence Principle consensus mechanism
        result_str = gl.eq_principle.strict_eq(resolve_dispute)
        result_json = json.loads(result_str)

        payout_percent = int(result_json["payment_to_seller_percentage"])
        reasoning = result_json["reasoning"]

        # Bound check the percentage to protect against LLM hallucinations
        if payout_percent < 0:
            payout_percent = 0
        elif payout_percent > 100:
            payout_percent = 100

        total_amount = escrow.amount
        seller_share = (total_amount * u256(payout_percent)) // u256(100)
        buyer_share = total_amount - seller_share

        # Execute payouts
        if seller_share > u256(0):
            _Recipient(escrow.seller).emit_transfer(value=seller_share)
        if buyer_share > u256(0):
            _Recipient(escrow.buyer).emit_transfer(value=buyer_share)

        # Update state
        escrow.status = "RESOLVED"
        escrow.payout_seller_percent = u256(payout_percent)
        escrow.resolution_reason = reasoning
        escrow.amount = u256(0)
        self.escrows[escrow_id] = escrow

    @gl.public.view
    def get_escrow(self, escrow_id: u256) -> EscrowRecord:
        """
        Returns the details of an escrow record.
        """
        if escrow_id not in self.escrows:
            raise gl.vm.UserError("Escrow not found")
        return self.escrows[escrow_id]
