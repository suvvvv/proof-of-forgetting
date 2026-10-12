"""Forget a user under ArmorIQ, scan for leftovers, then issue and verify a Deletion Certificate.

    python -m scripts.forget_and_certify
"""

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")

from agent import certificate  # noqa: E402
from agent import run as agent  # noqa: E402
from memory_mcp import store  # noqa: E402
from scripts.seed import ALICE, SECRETS  # noqa: E402

REQUEST = "Forget everything you know about me."


async def main():
    summary = await agent.run(ALICE, REQUEST, guarded=True)
    residue = {"found_in": {s: store.find_anywhere(s) for s in SECRETS}}
    residue["forgotten"] = all(not hits for hits in residue["found_in"].values())
    cert = certificate.build(
        summary, summary["plan"]["signed_token"], residue, store.get_outbox(), store.get_lessons()
    )
    out = Path("certificates") / f"{cert['armoriq']['plan_id'] or cert['armoriq']['token_id']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(cert, indent=2))
    result = certificate.verify(cert)
    print(f"\nDeletion Certificate → {out}")
    for name, ok in result["checks"].items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print("CERTIFICATE VALID" if result["valid"] else "CERTIFICATE INVALID")
    return out


if __name__ == "__main__":
    asyncio.run(main())
