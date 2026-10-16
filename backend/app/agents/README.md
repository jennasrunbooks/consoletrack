# app/agents

The triage agent loop, its prompts (`prompts/`), and the `submit_analysis` schema. Filled by #10.
It moves changes and incidents only through `app/services/change_state.py` and
`app/services/incident_state.py`, and never applies anything itself.
