"""Walk the audit hash chain and report whether it is intact.

Exits non-zero at the first break, so it can gate CI.
Run: python -m scripts.verify_audit
"""
import asyncio
import sys

from app.audit.chain import verify_chain
from app.db.client import connect, disconnect
from app.db.documents import AuditEvent


async def main() -> int:
    await connect()
    try:
        events = await AuditEvent.find_all().sort("+seq").to_list()
        result = verify_chain([e.model_dump() for e in events])
    finally:
        await disconnect()

    if result["ok"]:
        print(f"Audit chain intact — {result['checked']} entries verified.")
        return 0

    print(f"AUDIT CHAIN BROKEN at seq {result['broken_at']}: {result['detail']}", file=sys.stderr)
    print(f"{result['checked']} entries verified before the break.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
