import hashlib

from app.domain.intent import OrderIntent
from app.policy import verdict as verdict_signing
from app.policy.clock import Clock, SystemClock
from app.policy.policy import Policy
from app.policy.rules import RULES
from app.policy.types import RuleContext, Verdict


class PolicyEngine:
    """The chokepoint (tenet T1): no network, no database, no LLM call
    happens anywhere in evaluate(). Current prices and spend counters arrive
    via the injected RuleContext, built *before* this is called — so unit
    tests run fully offline, with no Mongo and no LLM key at all.

    Total: every rule always runs, so a DENY carries every violated reason,
    not just the first one found. Precedence: DENY > REQUIRE_APPROVAL > ALLOW.
    """

    def __init__(self, policy: Policy, signing_key: str, token_ttl_s: int = 120, clock: Clock | None = None):
        self.policy = policy
        self.signing_key = signing_key
        self.token_ttl_s = token_ttl_s
        self.clock = clock or SystemClock()

    def evaluate(self, intent: OrderIntent, ctx: RuleContext) -> Verdict:
        findings = tuple(rule(intent, self.policy, ctx) for rule in RULES)

        if any(f.outcome == "deny" for f in findings):
            decision = "DENY"
        elif any(f.outcome == "require_approval" for f in findings):
            decision = "REQUIRE_APPROVAL"
        else:
            decision = "ALLOW"

        reasons = [f.reason for f in findings if f.outcome != "pass"]
        reason_summary = " ".join(reasons) if reasons else "Within all policy limits."

        now = self.clock.now()
        evaluation_id = hashlib.sha256(f"{intent.hash()}|{now.isoformat()}".encode()).hexdigest()[:26]

        token = None
        if decision == "ALLOW":
            token = verdict_signing.sign(self.signing_key, intent.hash(), evaluation_id, self.token_ttl_s, now)

        return Verdict(
            decision=decision,
            findings=findings,
            reason_summary=reason_summary,
            policy_version=self.policy.version,
            intent_hash=intent.hash(),
            evaluation_id=evaluation_id,
            token=token,
        )
