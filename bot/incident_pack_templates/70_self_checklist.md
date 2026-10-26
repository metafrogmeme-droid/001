# Checklist (for you, not to send)

- [ ] Stop using the affected wallet. Move any remaining assets to a new wallet created on a clean device. Do not type the seed into a website.
- [ ] Revoke remaining approvals, Permit2 allowances, and any 7702 delegation. Use the wallet's own revoke flow. The timeline lists the spenders.
- [ ] On Hyperliquid, revoke API wallets and builder approvals you do not recognise.
- [ ] Keep pack.json unchanged. Its sha256 ({{SEAL}}) is the integrity check.
- [ ] Be wary of recovery services that contact you after an incident.
