"""Voice receptionist provider integrations.

`elevenlabs` — ConvAI agents (the conversational brain) + SIP phone-number import.
`krosai`     — phone-number inventory / purchase / SIP endpoint (inbound numbers).
`provisioning` — orchestrates the two into one self-serve, multi-tenant receptionist.

All three are pure provider/IO layers: they never resolve a tenant themselves. The
caller (router / webhook) is responsible for tenant scoping, mirroring the rest of
the codebase's "resolve tenant at the edge, pass it down" rule.
"""
