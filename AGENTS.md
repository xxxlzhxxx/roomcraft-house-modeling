# RoomCraft execution

Every Blender operation and Blender script edit must be performed through Codex CLI with Kimi K3.1. This includes verification, asset import/export and rendering. Do not run Blender directly as the parent assistant or use a different model or CLI.

Verify the provider/model mapping against the operator's private inventory, and retain redacted evidence of each run. The job metadata's model label alone is not proof of the provider used. If the required model is unavailable, stop instead of switching models.

Keep inputs, model credentials, endpoint IDs, generated scenes, user plans, and experiment outputs outside Git. Historical cases are case-specific; human geometry and visual review are required. Do not change global Codex or LiteLLM settings.
